"""
Baymax Pipeline Dashboard — Flask UI.

Reads the live Redis state for every pipeline stage and serves a dashboard
that auto-refreshes every 3 seconds. Also manages the Baymax Bureau subprocess
and bridges iMessage approval notifications.

Endpoints:
    GET  /              dashboard HTML
    GET  /api/state     JSON snapshot of all pipeline data
    GET  /image/latest  latest capture JPEG (or placeholder)
    POST /api/capture   run mock capture (--counts a=4,b=2) → Redis
    POST /api/refresh_who   re-run WHO fetch + Claude reasoning → Redis
    POST /api/negotiate     push trigger to bureau (non-blocking)
    POST /api/bureau/start  start/restart the bureau subprocess
    GET  /api/narration     SSE stream of narration events
    GET  /req/<rid>/approve  approve a pending transfer
    GET  /req/<rid>/order    order externally
    GET  /req/<rid>/reject   reject a pending transfer

Run:
    python ui/app.py
    open http://localhost:5001
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# ── Bridge to redis/src ──────────────────────────────────────────────────────
_REDIS_SRC = Path(__file__).resolve().parents[1] / "redis" / "src"
if str(_REDIS_SRC) not in sys.path:
    sys.path.insert(0, str(_REDIS_SRC))

# ── Bridge to who_agent fetcher and fetch.approval ───────────────────────────
_FETCH_ROOT = Path(__file__).resolve().parents[1]
if str(_FETCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_FETCH_ROOT))

log = logging.getLogger("baymax_ui")
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = Flask(__name__)

UI_PORT = int(os.getenv("UI_PORT", "5001"))
REGION = os.getenv("FORECAST_REGION", "san_francisco")
HARDWARE_DIR = Path(__file__).resolve().parents[1] / "hardware" / "camera connection"
SYNC_SCRIPT = HARDWARE_DIR / "sync_to_redis.py"
AGENT_DIR = Path(__file__).resolve().parents[1] / "agent-communication-layer"


def _agent_python() -> str:
    """Interpreter used to spawn the Bureau.

    Prefer the agent layer's own venv; if it's missing, fall back to the
    dashboard's own interpreter (the main .venv) so the Bureau still launches
    as long as uagents is installed there.
    """
    candidate = AGENT_DIR / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


AGENT_VENV_PYTHON = _agent_python()

# ── Bureau subprocess ─────────────────────────────────────────────────────────
_bureau_proc: subprocess.Popen | None = None


BUREAU_LOG = Path("/tmp/baymax_bureau.log")
BUREAU_PORT = 8000  # uAgents Bureau internal ASGI port


def _free_bureau_port():
    """Kill anything holding the Bureau's port so a fresh spawn can bind."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{BUREAU_PORT}"],
            capture_output=True, text=True,
        )
        pids = result.stdout.strip().split()
        for pid in pids:
            try:
                os.kill(int(pid), 9)
                log.info("Killed stale process %s on port %s", pid, BUREAU_PORT)
            except (ProcessLookupError, ValueError):
                pass
    except Exception as exc:
        log.warning("_free_bureau_port: %s", exc)


def _ensure_bureau():
    """Spawn the Bureau subprocess if not already running.

    Raises RuntimeError with a clear message on misconfiguration (missing script
    or interpreter) so callers can surface a clean JSON error instead of a 500.
    """
    global _bureau_proc
    if _bureau_proc and _bureau_proc.poll() is None:
        return
    script = AGENT_DIR / "run_dashboard_demo.py"
    if not script.exists():
        raise RuntimeError(f"Bureau script not found: {script}")
    if not Path(AGENT_VENV_PYTHON).exists():
        raise RuntimeError(f"Python interpreter not found: {AGENT_VENV_PYTHON}")
    _free_bureau_port()
    env = {**os.environ,
           "BAYMAX_REDIS": "1",
           "BAYMAX_OFFER_TIMEOUT": "4.0",
           "BAYMAX_SPARSE_NARRATION": "0",
           "BAYMAX_DASHBOARD_PORT": "8079",  # avoid conflict with Flask
           "REDIS_URL": os.getenv("REDIS_URL", "redis://localhost:6379")}
    log_fh = open(BUREAU_LOG, "w")
    _bureau_proc = subprocess.Popen(
        [str(AGENT_VENV_PYTHON), "run_dashboard_demo.py"],
        cwd=str(AGENT_DIR), env=env,
        stdout=log_fh, stderr=log_fh,
    )
    log.info("Bureau started (pid %s) — log: %s", _bureau_proc.pid, BUREAU_LOG)


# ── In-memory narration state ─────────────────────────────────────────────────
_narration_log: list[dict] = []    # last 50 events
_awaiting_approval: dict = {}      # req_id -> {"detail": str, "notified": bool}


def _send_approval_imessage(req_id: str, detail: str):
    base = os.getenv("APPROVAL_BASE_URL", f"http://localhost:{UI_PORT}").rstrip("/")
    approve_url = f"{base}/req/{req_id}/approve"
    order_url   = f"{base}/req/{req_id}/order"
    reject_url  = f"{base}/req/{req_id}/reject"
    msg = (
        f"🏥 Baymax needs your approval.\n"
        f"{detail[:220]}\n\n"
        f"✅ Approve transfer: {approve_url}\n"
        f"🛒 Order externally: {order_url}\n"
        f"❌ Reject: {reject_url}"
    )
    try:
        from fetch.approval.imessage_client import notify
        notify("hospital_a", msg)
        log.info("[imessage] sent approval request for %s", req_id)
    except Exception as exc:
        log.warning("[imessage] failed: %s", exc)


def _handle_narration_payload(payload: dict):
    """Process one narration event into in-memory state and trigger iMessage if needed."""
    _narration_log.append(payload)
    if len(_narration_log) > 200:
        del _narration_log[:-200]
    state  = payload.get("state")
    req_id = payload.get("req_id")
    if state == "awaiting_approval" and req_id and req_id not in _awaiting_approval:
        detail = payload.get("detail", "")
        _awaiting_approval[req_id] = {"detail": detail, "notified": False}
        _send_approval_imessage(req_id, detail)
        _awaiting_approval[req_id]["notified"] = True


def _narration_subscriber():
    """Poll baymax:narration:log Redis list — reliable, never misses events."""
    while True:
        idx = 0
        try:
            import redis as _redis_lib
            url = os.getenv("REDIS_URL", "redis://localhost:6379")
            r = _redis_lib.Redis.from_url(url, decode_responses=True,
                                          socket_connect_timeout=3, socket_timeout=10)
            # Start from the current end so we don't replay old events on startup
            idx = r.llen("baymax:narration:log")
            log.info("[narration] connected, watching from index %d", idx)
            while True:
                items = r.lrange("baymax:narration:log", idx, idx + 49)
                for raw in items:
                    try:
                        _handle_narration_payload(json.loads(raw))
                    except Exception:
                        pass
                idx += len(items)
                time.sleep(0.4)
        except Exception as exc:
            log.warning("[narration] error: %s — retrying in 2s", exc)
            time.sleep(2)


# Start subscriber thread on module load
_subscriber_thread = threading.Thread(target=_narration_subscriber, daemon=True)
_subscriber_thread.start()


# ── Redis helpers ─────────────────────────────────────────────────────────────

def _redis():
    import redis as _redis_lib
    url = os.getenv("REDIS_URL", "redis://localhost:6379")
    return _redis_lib.Redis.from_url(url, decode_responses=True,
                                     socket_connect_timeout=1, socket_timeout=1)


def _safe_json(raw):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _push_decision(req_id: str, decision: str):
    try:
        r = _redis()
        r.lpush("baymax:decision", json.dumps({"req_id": req_id, "decision": decision}))
        _awaiting_approval.pop(req_id, None)
        log.info("[decision] pushed %s for req_id=%s", decision, req_id)
    except Exception as exc:
        log.warning("[decision] push failed: %s", exc)


def _push_trigger(item: str, requester: str = "Hospital A", quantity: int | None = None,
                  prompt: str | None = None, stock: dict | None = None):
    try:
        r = _redis()
        payload = {
            "item": item, "requester": requester, "quantity": quantity,
            "prompt": prompt, "stock": stock,
        }
        r.lpush("baymax:trigger", json.dumps(payload))
        log.info(json.dumps({
            "tag": "FETCH", "file": "ui/app.py", "action": "trigger_pushed",
            "item": item, "quantity": quantity, "prompt": prompt, "stock": stock,
        }))
    except Exception as exc:
        log.warning("[trigger] push failed: %s", exc)


def _hospital_stock(r, item: str | None = None,
                    hids=("hospital_a", "hospital_b")) -> dict:
    """Current inventory stock for hospitals A and B (qty/pct/status per item).

    If ``item`` is given, returns just {hid: {qty,pct,status}} for that item;
    otherwise returns the full per-item map for each hospital.
    """
    out: dict = {}
    for hid in hids:
        inv = {k: (_safe_json(v) or {}) for k, v in r.hgetall(f"hospital:{hid}:inventory").items()}
        if item is not None:
            rec = inv.get(item, {})
            out[hid] = {"qty": rec.get("qty"), "pct": rec.get("pct"), "status": rec.get("status")}
        else:
            out[hid] = {k: {"qty": rec.get("qty"), "status": rec.get("status")} for k, rec in inv.items()}
    return out


def _build_negotiation_prompt(item: str, need: int, stock: dict,
                              source_facility: str | None,
                              destination_facility: str | None) -> str:
    """Compose the natural-language intent sent to the Fetch.ai negotiation."""
    a = (stock.get("hospital_a") or {}).get("qty")
    b = (stock.get("hospital_b") or {}).get("qty")
    dest = HOSP_LABEL.get(destination_facility, destination_facility or "Hospital A")
    src = HOSP_LABEL.get(source_facility, source_facility) if source_facility else "a surplus facility"
    return (
        f"{dest} is critically low on {item} "
        f"(Hospital A on hand: {a if a is not None else '?'}, "
        f"Hospital B on hand: {b if b is not None else '?'}). "
        f"Source {need} units of {item} for {dest} from {src}."
    )


HOSP_LABEL = {"hospital_a": "Hospital A", "hospital_b": "Hospital B", "hospital_c": "Hospital C"}


def get_state() -> dict:
    """Read every pipeline stage from Redis and return as a single dict."""
    state: dict = {
        "redis_connected": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "forecast": None,
        "reasoning": None,
        "inventory": {},
        "surplus": {},
        "meta": {},
        "scenario": None,
        "vision": None,
        "vision_image_available": False,
        "transfers": [],
        "alerts": [],
        "negotiation_log": _narration_log[-100:],
        "awaiting_approval": list(_awaiting_approval.keys()),
        "bureau_running": _bureau_proc is not None and _bureau_proc.poll() is None,
    }

    try:
        r = _redis()
        r.ping()
        state["redis_connected"] = True
    except Exception as e:
        state["redis_error"] = str(e)
        return state

    # forecast:{region}
    state["forecast"] = _safe_json(r.get(f"forecast:{REGION}"))

    # reasoning:latest
    state["reasoning"] = _safe_json(r.get("reasoning:latest"))

    # hospital inventory + surplus + meta
    for hid in ("hospital_a", "hospital_b", "hospital_c"):
        inv_raw = r.hgetall(f"hospital:{hid}:inventory")
        state["inventory"][hid] = {k: _safe_json(v) for k, v in inv_raw.items()}

        sur_raw = r.hgetall(f"hospital:{hid}:surplus")
        state["surplus"][hid] = {k: int(v) for k, v in sur_raw.items()}

        meta_raw = r.hgetall(f"hospital:{hid}:meta")
        state["meta"][hid] = meta_raw

    # scenario:flu_surge (active demo scenario) — overlaid with live WHO + camera
    # signals so the forecast growth and recommendation are derived, not static.
    state["scenario"] = _live_scenario(r, _safe_json(r.get("scenario:flu_surge")))

    # vision:latest
    state["vision"] = _safe_json(r.get("vision:latest"))

    # Check for a capture image on disk
    captures = sorted(HARDWARE_DIR.glob("capture_*.jpg"), key=lambda p: p.stat().st_mtime)
    if captures:
        state["vision_image_available"] = True
        state["vision_image_name"] = captures[-1].name
        state["vision_image_ts"] = int(captures[-1].stat().st_mtime)

    # Recent transfers (stream)
    try:
        entries = r.xrevrange("transfers", count=5)
        for _eid, fields in entries:
            d = fields.get("data")
            if d:
                state["transfers"].append(_safe_json(d))
    except Exception:
        pass

    # Recent alerts
    try:
        entries = r.xrevrange("alerts:log", count=5)
        for _eid, fields in entries:
            a = fields.get("alert")
            if a:
                state["alerts"].append(_safe_json(a))
    except Exception:
        pass

    return state


# ── Approval page helper ──────────────────────────────────────────────────────

def _approval_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f1117; color: #e2e8f0; display: flex; align-items: center;
         justify-content: center; min-height: 100vh; margin: 0; padding: 24px; }}
  .box {{ background: #1a1d27; border: 1px solid #2a2d3e; border-radius: 14px;
          padding: 32px 28px; max-width: 440px; width: 100%; text-align: center; }}
  h2 {{ font-size: 22px; margin-bottom: 12px; }}
  p {{ color: #8892a4; line-height: 1.6; font-size: 15px; }}
  a {{ color: #6366f1; }}
</style>
</head>
<body>
<div class="box">{body}<p style="margin-top:20px;font-size:13px"><a href="/">Back to dashboard</a></p></div>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("index.html", region=REGION)


@app.route("/api/state")
def api_state():
    return jsonify(get_state())


ARIZE_TRACES_DIRS = [
    _FETCH_ROOT / "tracks" / "arize" / "traces",
    _FETCH_ROOT / "arize" / "traces",
]


@app.route("/api/arize")
def api_arize():
    """Latest Arize observability trace per trace_name from the on-disk trace store.

    The Arize track (arize/src) persists decision-chain traces as JSON files. This
    surfaces the most recent of each kind so the dashboard can show the
    inventory → forecast → reasoning → transfer → outcome chain. Fail-open: any
    read error just yields an empty trace set so the dashboard never breaks.
    """
    latest: dict = {}
    latest_mtime: dict = {}
    for d in ARIZE_TRACES_DIRS:
        try:
            if not d.is_dir():
                continue
            for fp in d.glob("*.json"):
                try:
                    mtime = fp.stat().st_mtime
                    with fp.open() as f:
                        ev = json.load(f)
                    name = ev.get("trace_name") or fp.stem.rsplit("_", 1)[0]
                    if name not in latest_mtime or mtime > latest_mtime[name]:
                        latest[name] = ev
                        latest_mtime[name] = mtime
                except Exception:
                    continue
        except Exception:
            continue
    return jsonify({"ok": True, "traces": latest})


@app.route("/image/hospital/<hid>")
def image_hospital(hid):
    """Serve a hospital's latest camera image from Redis (vision:image:{hid})."""
    import base64, io
    if hid not in ("hospital_a", "hospital_b"):
        return "Not found", 404
    try:
        r = _redis()
        data = r.get(f"vision:image:{hid}")
        if data:
            return send_file(io.BytesIO(base64.b64decode(data)), mimetype="image/jpeg")
    except Exception:
        pass
    # Placeholder 1×1 gray PNG
    placeholder_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    return send_file(io.BytesIO(base64.b64decode(placeholder_b64)), mimetype="image/png")


@app.route("/image/latest")
def image_latest():
    """Serve the most recent capture JPEG."""
    captures = sorted(HARDWARE_DIR.glob("capture_*.jpg"), key=lambda p: p.stat().st_mtime)
    if captures:
        return send_file(str(captures[-1]), mimetype="image/jpeg")
    # Return a tiny 1×1 gray placeholder PNG if no image exists
    import base64, io
    placeholder_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    return send_file(
        io.BytesIO(base64.b64decode(placeholder_b64)),
        mimetype="image/png",
    )


CAPTURE_SINGLE_SCRIPT = HARDWARE_DIR / "capture_single.py"
HOSPITAL_ID = os.getenv("HOSPITAL_ID", "hospital_a")  # which hospital this Mac is


@app.route("/api/capture", methods=["POST"])
def api_capture():
    """
    Capture from this MacBook's camera, send to Claude Vision, write image +
    inventory to Redis. hospital_id defaults to HOSPITAL_ID env var (hospital_a).

    Body (JSON, optional):
        hospital_id — override which hospital this capture is for
        count       — skip camera/Claude, write a manual count instead
    """
    data = request.get_json(silent=True) or {}
    hospital_id = data.get("hospital_id") or HOSPITAL_ID
    manual_count = data.get("count")

    log.info(json.dumps({
        "tag": "VISION", "file": "ui/app.py",
        "action": "capture_triggered",
        "hospital_id": hospital_id,
        "manual_count": manual_count,
    }))

    env = {**os.environ, "HOSPITAL_ID": hospital_id,
           "REDIS_URL": os.getenv("REDIS_URL", "redis://localhost:6379")}

    cmd = [sys.executable, str(CAPTURE_SINGLE_SCRIPT)]
    if manual_count is not None:
        cmd += ["--count", str(manual_count)]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            cwd=str(HARDWARE_DIR), env=env,
        )
        output = result.stdout + result.stderr
        log.info(json.dumps({
            "tag": "VISION", "file": "ui/app.py",
            "action": "capture_complete",
            "hospital_id": hospital_id,
            "returncode": result.returncode,
        }))
        if result.returncode != 0:
            return jsonify({"ok": False, "error": output}), 500
        return jsonify({"ok": True, "hospital_id": hospital_id, "output": output})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Capture timed out (60s)"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/capture_request", methods=["POST"])
def api_capture_request():
    """
    Publish a one-shot capture trigger to the shared Redis so remote hospital
    machines running `capture_single.py --watch` take a single picture.

    Body (JSON, optional):
        target — "hospital_a" | "hospital_b" | "all" (default "all")
    """
    data = request.get_json(silent=True) or {}
    target = data.get("target", "all")
    try:
        r = _redis()
        n = r.publish("vision:capture_request", json.dumps({"target": target}))
        log.info(json.dumps({
            "tag": "VISION", "file": "ui/app.py",
            "action": "capture_request_published",
            "target": target, "subscribers": n,
        }))
        return jsonify({"ok": True, "target": target, "subscribers": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/refresh_who", methods=["POST"])
def api_refresh_who():
    """Re-run the WHO fetch + Claude reasoning pipeline and write to Redis."""
    log.info(json.dumps({
        "tag": "WHO", "file": "ui/app.py",
        "action": "manual_refresh_triggered",
    }))
    try:
        from fetch.agents.who_agent.fetcher import run_who_update
        result = run_who_update(region=REGION)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# Target on-hand level we negotiate up to, and the camera-tracked inventory item
# (matches capture_single.py's ITEM default). The camera counts general medical
# supplies and records them against this item.
SALINE_TARGET = int(os.getenv("SALINE_TARGET", "20"))
CAMERA_ITEM = os.getenv("ITEM", "Saline")

# CDC focus-illness severity → projected demand growth used by the scenario card.
# Driven by the live who_focus_level, so the scenario "+X%" tracks the WHO signal.
_SEVERITY_GROWTH = {
    "Very High": "+40%", "High": "+25%", "Moderate": "+15%",
    "Low": "+5%", "Minimal": "0%",
}


def _derive_transfer_plan(r) -> dict:
    """
    Build a live transfer plan that ties the WHO focus illness to the camera-
    confirmed shortage:

      - item: the camera-tracked item, chosen only when the WHO focus illness's
              required-supplies list includes it (so influenza → Saline is an
              explicit, data-driven choice rather than a hardcoded constant).
      - destination: the hospital the camera shows lowest on that item.
      - source: the non-destination hospital with the most surplus of that item.
      - need / transfer_quantity: SALINE_TARGET − destination's on-hand count.

    Fails open to a sensible default when WHO/vision data is unavailable.
    """
    try:
        fc_items = (_safe_json(r.get(f"forecast:{REGION}")) or {}).get("items", {}) or {}
        required = fc_items.get("who_required_supplies") or []
        focus = fc_items.get("who_focus_illness")
        required_lc = [str(s).lower() for s in required]

        # Pick the negotiated item: camera item if WHO requires it, else WHO's top
        # supply, else the camera item.
        if CAMERA_ITEM.lower() in required_lc:
            item = CAMERA_ITEM
            item_reason = f"{focus or 'focus illness'} requires {item}; camera confirms shortage"
        elif required:
            item = required[0]
            item_reason = f"{focus or 'focus illness'} top required supply"
        else:
            item = CAMERA_ITEM
            item_reason = "no WHO focus illness; using camera item"

        v = _safe_json(r.get("vision:latest")) or {}
        item_key = CAMERA_ITEM.lower().replace(" ", "_")
        counts: dict = {}
        for hid in ("hospital_a", "hospital_b"):
            hv = v.get(hid) or {}
            c = hv.get(item_key)
            if c is None:
                c = hv.get("saline")  # backward-compat with older captures
            if c is not None:
                counts[hid] = c

        if not counts:
            return {
                "item": item, "need": SALINE_TARGET, "transfer_quantity": SALINE_TARGET,
                "who_focus_illness": focus, "required": required, "item_reason": item_reason,
                "source": f"{item_reason} (no camera counts → need {SALINE_TARGET})",
            }

        dest = min(counts, key=counts.get)
        need = max(1, SALINE_TARGET - counts[dest])

        best_src, best_sur = None, -1
        for hid in ("hospital_a", "hospital_b"):
            if hid == dest:
                continue
            try:
                sur = int(r.hget(f"hospital:{hid}:surplus", item) or 0)
            except Exception:
                sur = 0
            if sur > best_sur:
                best_src, best_sur = hid, sur

        return {
            "item": item,
            "need": need,
            "transfer_quantity": need,
            "vision_a": counts.get("hospital_a"),
            "vision_b": counts.get("hospital_b"),
            "target": SALINE_TARGET,
            "source_facility": best_src,
            "destination_facility": dest,
            "source_surplus": best_sur if best_sur >= 0 else None,
            "who_focus_illness": focus,
            "required": required,
            "item_reason": item_reason,
            "source": (f"{item_reason}: {best_src} (spare {best_sur}) → {dest} "
                       f"(has {counts[dest]}, target {SALINE_TARGET} → need {need})"),
        }
    except Exception as e:
        return {"item": "IV fluids", "need": 200, "source": f"default (error: {e})"}


def _live_scenario(r, base: dict | None) -> dict | None:
    """
    Overlay live WHO + camera signals onto the seeded scenario so the dashboard's
    forecast growth and transfer recommendation are derived, not static.
    The seeded copy in Redis is untouched; this only shapes what the UI shows.
    """
    if not base:
        return base
    try:
        fc_items = (_safe_json(r.get(f"forecast:{REGION}")) or {}).get("items", {}) or {}
        focus = fc_items.get("who_focus_illness")
        level = fc_items.get("who_focus_level")
        trend = fc_items.get("who_trend")
        sc = dict(base)

        growth = _SEVERITY_GROWTH.get(level)
        if growth:
            fcast = dict(sc.get("forecast") or {})
            fcast["predicted_case_growth"] = growth
            fcast["status"] = trend or fcast.get("status")
            fcast.setdefault("window", "next 7 days")
            fcast["source"] = f"derived from WHO focus: {focus} ({level})"
            sc["forecast"] = fcast

        plan = _derive_transfer_plan(r)
        if plan.get("source_facility") and plan.get("destination_facility"):
            rec = dict(sc.get("recommendation") or {})
            rec["status"] = "transfer_recommended"
            rec["item"] = plan["item"]
            rec["source_facility"] = plan["source_facility"]
            rec["destination_facility"] = plan["destination_facility"]
            rec["transfer_quantity"] = plan["transfer_quantity"]
            rec.setdefault("estimated_arrival",
                           (base.get("recommendation") or {}).get("estimated_arrival", "12 min"))
            rec["source"] = "derived from WHO focus illness + live camera counts"
            sc["recommendation"] = rec
        return sc
    except Exception:
        return base


def _vision_to_negotiation_params() -> dict:
    """Negotiation parameters derived from the shared live transfer plan."""
    try:
        return _derive_transfer_plan(_redis())
    except Exception as e:
        return {"item": "IV fluids", "need": 200, "source": f"default (redis error: {e})"}


@app.route("/api/negotiate", methods=["POST"])
def api_negotiate():
    """
    Trigger a Fetch.ai Bureau negotiation driven by the latest camera vision
    counts. Non-blocking: starts the bureau (if not running), pushes a trigger
    to Redis, and returns immediately. SSE at /api/narration streams progress.

    Body (JSON, all optional — overrides vision-derived values):
        item   — supply item
        need   — units needed
        redis  — use live Redis inventory (default true)
    """
    data = request.get_json(silent=True) or {}

    # Derive the transfer plan (item/need/source/dest) from live WHO + camera data
    vision_params = _vision_to_negotiation_params()
    item = data.get("item") or vision_params["item"]
    need = int(data.get("need") or vision_params["need"])

    # Build the message we send to Fetch.ai: a natural-language prompt + the
    # current item stock for Hospital A and Hospital B.
    r = _redis()
    stock = _hospital_stock(r, item=item)
    prompt = data.get("prompt") or _build_negotiation_prompt(
        item, need, stock,
        vision_params.get("source_facility"),
        vision_params.get("destination_facility"),
    )

    log.info(json.dumps({
        "tag": "FETCH", "file": "ui/app.py",
        "action": "negotiate_triggered",
        "item": item, "need": need,
        "prompt": prompt, "stock": stock,
        "vision_source": vision_params.get("source"),
    }))

    _ensure_bureau()
    _push_trigger(item, quantity=need, prompt=prompt, stock=stock)

    return jsonify({
        "ok": True,
        "message": "Negotiation triggered — watching for updates via SSE",
        "item": item,
        "need": need,
        "prompt": prompt,
        "stock": stock,
        "vision_source": vision_params.get("source"),
        "vision_a": vision_params.get("vision_a"),
        "vision_b": vision_params.get("vision_b"),
    })


@app.route("/api/bureau/start", methods=["POST"])
def api_bureau_start():
    """Manually start or restart the bureau subprocess."""
    try:
        _ensure_bureau()
    except Exception as exc:
        log.warning("Bureau start failed: %s", exc)
        return jsonify({"ok": False, "running": False, "error": str(exc)}), 200
    running = _bureau_proc is not None and _bureau_proc.poll() is None
    return jsonify({"ok": True, "running": running,
                    "pid": _bureau_proc.pid if _bureau_proc else None})


@app.route("/api/narration")
def api_narration():
    """SSE stream of narration events from the bureau."""
    def stream():
        sent = 0
        while True:
            if len(_narration_log) > sent:
                for ev in _narration_log[sent:]:
                    yield f"data: {json.dumps(ev)}\n\n"
                sent = len(_narration_log)
            time.sleep(0.4)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Approval endpoints ────────────────────────────────────────────────────────

@app.route("/req/<rid>/approve")
def req_approve(rid):
    _push_decision(rid, "approve")
    return _approval_page("Transfer Approved",
        "<h2>Transfer Approved</h2>"
        "<p>Baymax will proceed with the transfer. You'll receive a confirmation shortly.</p>")


@app.route("/req/<rid>/order")
def req_order(rid):
    _push_decision(rid, "order")
    return _approval_page("External Order",
        "<h2>External Order Initiated</h2>"
        "<p>Baymax will source the supplies from an external supplier.</p>")


@app.route("/req/<rid>/reject")
def req_reject(rid):
    _push_decision(rid, "reject")
    return _approval_page("Rejected",
        "<h2>Transfer Rejected</h2>"
        "<p>The negotiation has been cancelled.</p>")


def _parse_negotiation_log(log_text: str) -> list[dict]:
    """Extract key negotiation events from the uAgent log output."""
    import re
    events = []
    patterns = [
        (r"\[IDLE\] (.+)",             "idle"),
        (r"\[shortfall_detected\] (.+)", "shortfall"),
        (r"\[requesting\] (.+)",        "requesting"),
        (r"\[collecting_offers\] (.+)", "collecting"),
        (r"\[evaluating\] (.+)",        "evaluating"),
        (r"\[proposing\] (.+)",         "proposing"),
        (r"\[settling\] (.+)",          "settling"),
        (r"\[confirmed\] (.+)",         "confirmed"),
        (r"\[failed\] (.+)",            "failed"),
        (r"(Hospital [A-C] offers .+)", "offer"),
        (r"(Transfer .+accepted.+)",    "accepted"),
        (r"(Transfer .+rejected.+)",    "rejected"),
        (r"(Split transfer.+)",         "split"),
        (r"(No offer.+escalat.+)",      "escalation"),
    ]
    for line in log_text.splitlines():
        for pattern, tag in patterns:
            m = re.search(pattern, line, re.IGNORECASE)
            if m:
                events.append({"tag": tag, "msg": m.group(1).strip()})
                break
    return events


if __name__ == "__main__":
    port = UI_PORT
    print(f"\n  Baymax Pipeline Dashboard → http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
