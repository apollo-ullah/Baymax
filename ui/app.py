"""
Baymax Pipeline Dashboard — Flask UI.

Reads the live Redis state for every pipeline stage and serves a dashboard
that auto-refreshes every 3 seconds. Also manages the Baymax Bureau subprocess
and bridges iMessage approval notifications.

Endpoints:
    GET  /              dashboard HTML
    GET  /api/state     JSON snapshot of all pipeline data
    GET  /image/latest  latest capture JPEG (or placeholder)
    POST /api/capture   capture from MacBook camera → Claude Vision → Redis
    POST /api/scan      analyse a browser-uploaded JPEG frame → Redis
    POST /api/refresh_who   re-run WHO fetch + Claude reasoning → Redis
    POST /api/ingest        alias for /api/refresh_who
    POST /api/crisis        push a crisis onto baymax:crisis + seed crisis:active
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
from flask import Flask, Response, jsonify, request, send_file

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# ── Bridge to redis/src ──────────────────────────────────────────────────────
_REDIS_SRC = Path(__file__).resolve().parents[1] / "redis" / "src"
if str(_REDIS_SRC) not in sys.path:
    sys.path.insert(0, str(_REDIS_SRC))

# ── Bridge to who_agent fetcher and fetch.approval ───────────────────────────
_FETCH_ROOT = Path(__file__).resolve().parents[1]
if str(_FETCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_FETCH_ROOT))

_AGENT_DIR = Path(__file__).resolve().parents[1] / "agent-communication-layer"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

log = logging.getLogger("baymax_ui")
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = Flask(__name__)

DEFAULT_REDIS_URL = "redis://localhost:6379"


def _redis_url() -> str:
    """Treat blank REDIS_URL= in .env as unset (os.getenv default won't)."""
    return (os.getenv("REDIS_URL") or "").strip() or DEFAULT_REDIS_URL

UI_PORT = int(os.getenv("UI_PORT", "5001"))
REGION = os.getenv("FORECAST_REGION", "san_francisco")
HARDWARE_DIR = Path(__file__).resolve().parents[1] / "hardware" / "camera connection"
SYNC_SCRIPT = HARDWARE_DIR / "sync_to_redis.py"
AGENT_DIR = Path(__file__).resolve().parents[1] / "agent-communication-layer"
# The venv + secrets live in the SIBLING dir (a directory rename moved them out of
# agent-communication-layer/). Resolve it there; allow an override; and fall back to
# whatever interpreter is running this app (correct when app.py is launched with the
# sibling venv). Prevents a FileNotFoundError when spawning the Bureau subprocess.
_SIBLING_VENV_PYTHON = (
    Path(__file__).resolve().parents[1]
    / "adyan-agent-communication-layer" / ".venv" / "bin" / "python"
)
AGENT_VENV_PYTHON = Path(os.getenv("BAYMAX_AGENT_PYTHON") or _SIBLING_VENV_PYTHON)
if not AGENT_VENV_PYTHON.exists():
    AGENT_VENV_PYTHON = Path(sys.executable)

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
    global _bureau_proc
    if _bureau_proc and _bureau_proc.poll() is None:
        return
    _free_bureau_port()
    # The negotiation engine is now the Claude multi-agent orchestrator
    # (run_claude_demo.py — no uAgents/Bureau). It binds :8000 as a health port so
    # _free_bureau_port() / start_demo.sh's wait_port keep working unchanged.
    env = {**os.environ,
           "BAYMAX_REDIS": "1",
           "BAYMAX_CLAUDE_RESEARCH": "1",
           "BAYMAX_CLAUDE_RANKING": "1",
           "BAYMAX_HOSPITAL_LLM": "1",
           "BAYMAX_HEALTH_PORT": str(BUREAU_PORT),
           "REDIS_URL": _redis_url()}
    log_fh = open(BUREAU_LOG, "w")
    _bureau_proc = subprocess.Popen(
        [str(AGENT_VENV_PYTHON), "run_claude_demo.py"],
        cwd=str(AGENT_DIR), env=env,
        stdout=log_fh, stderr=log_fh,
    )
    log.info("Claude engine started (pid %s) — log: %s", _bureau_proc.pid, BUREAU_LOG)


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


def _narration_subscriber():
    """Background thread: subscribe to baymax:narration pubsub and update state."""
    while True:
        try:
            import redis as _redis_lib
            url = _redis_url()
            r = _redis_lib.Redis.from_url(url, decode_responses=True,
                                          socket_connect_timeout=2, socket_timeout=2)
            pubsub = r.pubsub()
            pubsub.subscribe("baymax:narration")
            for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    payload = json.loads(message["data"])
                except (ValueError, TypeError):
                    continue

                # Append to log, cap at 50
                _narration_log.append(payload)
                if len(_narration_log) > 50:
                    del _narration_log[:-50]

                # Check for awaiting_approval
                state = payload.get("state")
                req_id = payload.get("req_id")
                if state == "awaiting_approval" and req_id and req_id not in _awaiting_approval:
                    detail = payload.get("detail", "")
                    _awaiting_approval[req_id] = {"detail": detail, "notified": False}
                    _send_approval_imessage(req_id, detail)
                    _awaiting_approval[req_id]["notified"] = True

        except Exception as exc:
            log.warning("[narration_subscriber] error: %s — retrying in 2s", exc)
            time.sleep(2)


# Start subscriber thread on module load
_subscriber_thread = threading.Thread(target=_narration_subscriber, daemon=True)
_subscriber_thread.start()


# ── Redis helpers ─────────────────────────────────────────────────────────────

def _redis():
    import redis as _redis_lib
    url = _redis_url()
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
        r.rpush("baymax:decision", json.dumps({"req_id": req_id, "decision": decision}))
        _awaiting_approval.pop(req_id, None)
        log.info("[decision] pushed %s for req_id=%s", decision, req_id)
    except Exception as exc:
        log.warning("[decision] push failed: %s", exc)


def _push_trigger(item: str, requester: str = "Hospital A", quantity: int | None = None):
    try:
        r = _redis()
        r.rpush("baymax:trigger", json.dumps({"item": item, "requester": requester, "quantity": quantity}))
        log.info("[trigger] pushed item=%s qty=%s", item, quantity)
    except Exception as exc:
        log.warning("[trigger] push failed: %s", exc)


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
        "crisis": None,
        "vision": None,
        "vision_image_available": False,
        "transfers": [],
        "alerts": [],
        "negotiation_log": _narration_log[-20:],
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

    # scenario:heatstroke
    state["scenario"] = _safe_json(r.get("scenario:heatstroke"))

    # crisis:active — active crisis-response card (seeded by /api/crisis, then
    # overwritten by the agent with crisis_type + at_risk)
    state["crisis"] = _safe_json(r.get("crisis:active"))

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
def root():
    # The UI now lives entirely in the Next.js app (web/, http://localhost:3000).
    # This Flask service is the headless API backend that app proxies.
    return jsonify({"service": "baymax-api", "ui": "http://localhost:3000", "region": REGION})


@app.route("/api/state")
def api_state():
    return jsonify(get_state())


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
    item = data.get("item") or os.getenv("BAYMAX_ITEM", "Saline")

    log.info(json.dumps({
        "tag": "VISION", "file": "ui/app.py",
        "action": "capture_triggered",
        "hospital_id": hospital_id,
        "item": item,
        "manual_count": manual_count,
    }))

    env = {**os.environ, "HOSPITAL_ID": hospital_id, "ITEM": item,
           "REDIS_URL": _redis_url(),
           "BAYMAX_VISION_DEMO": os.getenv("BAYMAX_VISION_DEMO", "1")}

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


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Run Claude Vision on a browser-captured frame (the web 'Scan inventory' button).

    Body (JSON): { image_b64 (data URL or raw base64 JPEG), hospital_id?, item? }
    The browser owns the live webcam; this only analyses the uploaded still, so there is
    no camera contention with getUserMedia. Reuses capture_single.py --image, which runs
    Claude Vision and writes vision:image + inventory to Redis. Returns the parsed count.
    """
    import base64 as _b64, re as _re, tempfile as _tmp
    data = request.get_json(silent=True) or {}
    hospital_id = data.get("hospital_id") or HOSPITAL_ID
    item = data.get("item") or os.getenv("BAYMAX_ITEM", "Saline")
    image_b64 = data.get("image_b64") or ""
    if "," in image_b64:                      # strip "data:image/jpeg;base64," prefix
        image_b64 = image_b64.split(",", 1)[1]
    if not image_b64:
        return jsonify({"ok": False, "error": "missing image_b64"}), 400
    try:
        raw = _b64.b64decode(image_b64)
    except Exception as e:
        return jsonify({"ok": False, "error": f"bad base64: {e}"}), 400

    fd, tmp = _tmp.mkstemp(suffix=".jpg", prefix="scan_", dir=str(HARDWARE_DIR))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        env = {**os.environ, "HOSPITAL_ID": hospital_id, "ITEM": item,
               "REDIS_URL": _redis_url(),
               "BAYMAX_VISION_DEMO": os.getenv("BAYMAX_VISION_DEMO", "1")}
        cmd = [sys.executable, str(CAPTURE_SINGLE_SCRIPT), "--image", tmp]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                cwd=str(HARDWARE_DIR), env=env)
        output = result.stdout + result.stderr
        if result.returncode != 0:
            return jsonify({"ok": False, "error": output}), 500
        m = _re.search(r"qty=(\d+)\s+pct=([\d.]+)\s+status=(\w+)\s+surplus=(\d+)", output)
        dm = _re.search(r"Detected:\s*\d+\s+\S+\s*[—-]\s*(.+)", output)
        return jsonify({
            "ok": True, "hospital_id": hospital_id, "item": item,
            "count": int(m.group(1)) if m else None,
            "status": m.group(3) if m else None,
            "surplus": int(m.group(4)) if m else None,
            "note": dm.group(1).strip() if dm else "",
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Vision scan timed out (60s)"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


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


@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    """Alias for /api/refresh_who — runs the WHO+weather+illness+Claude ingest."""
    return api_refresh_who()


@app.route("/api/crisis", methods=["POST"])
def api_crisis():
    """
    Kick off a crisis-response negotiation. Pushes the crisis onto the
    `baymax:crisis` list (FRONT-agent poller does LPOP → RPUSH gives FIFO) and
    seeds the `crisis:active` card so the UI shows "researching…" immediately.
    The agent overwrites crisis:active with the researched result moments later;
    all milestones arrive on the existing baymax:narration feed.

    Body (JSON):
        crisis_text — required, non-empty
        requester   — optional (default "Hospital A")
        region      — optional (default "san_francisco")
    """
    data = request.get_json(silent=True) or {}
    crisis_text = (data.get("crisis_text") or "").strip()
    if not crisis_text:
        return jsonify({"ok": False, "error": "crisis_text is required"}), 400

    requester = data.get("requester") or "Hospital A"
    region = data.get("region") or "san_francisco"

    log.info(json.dumps({
        "tag": "CRISIS", "file": "ui/app.py",
        "action": "crisis_triggered",
        "crisis_text": crisis_text, "requester": requester, "region": region,
    }))

    try:
        r = _redis()
        r.rpush("baymax:crisis", json.dumps({
            "crisis_text": crisis_text,
            "requester": requester,
            "region": region,
        }))
        r.set("crisis:active", json.dumps({
            "crisis_text": crisis_text,
            "region": region,
            "requester": requester,
            "status": "researching",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503

    return jsonify({"ok": True, "crisis_text": crisis_text})


def _vision_to_negotiation_params() -> dict:
    """
    Read vision:latest + crisis:active from Redis and derive negotiation parameters.

    Uses whichever item the crisis flow selected (or the active vision item).
    need = max(1, target - count_a) with a demo-friendly default target of 3.
    Falls back to mock-scale defaults when no vision data is available.
    """
    from interfaces import demo_mode, demo_target_on_hand, vision_item_key

    target = demo_target_on_hand() if demo_mode() else int(os.getenv("BAYMAX_DEMO_TARGET", "20"))
    default_item = "IV fluids"
    default_need = 2 if demo_mode() else 200

    try:
        r = _redis()
        item = default_item
        crisis_raw = r.get("crisis:active")
        if crisis_raw:
            crisis = json.loads(crisis_raw)
            at_risk = crisis.get("at_risk") or []
            if at_risk and isinstance(at_risk[0], dict):
                item = at_risk[0].get("item") or item

        raw = r.get("vision:latest")
        if not raw:
            return {"item": item, "need": default_need,
                    "source": "default (no vision data)"}
        v = json.loads(raw)
        item = v.get("active_item") or item
        ikey = vision_item_key(item)
        count_a = (v.get("hospital_a") or {}).get(ikey)
        count_b = (v.get("hospital_b") or {}).get(ikey)
        if count_a is None:
            return {"item": item, "need": default_need,
                    "source": f"default (vision missing hospital_a/{ikey})"}
        need = max(1, target - int(count_a))
        return {
            "item": item,
            "need": need,
            "vision_a": count_a,
            "vision_b": count_b,
            "target": target,
            "source": f"vision ({item}: Hospital A has {count_a}, target {target} → need {need})",
        }
    except Exception as e:
        return {"item": default_item, "need": default_need,
                "source": f"default (redis error: {e})"}


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

    # Derive from vision unless caller overrides
    vision_params = _vision_to_negotiation_params()
    item = data.get("item") or vision_params["item"]
    need = int(data.get("need") or vision_params["need"])

    log.info(json.dumps({
        "tag": "FETCH", "file": "ui/app.py",
        "action": "negotiate_triggered",
        "item": item, "need": need,
        "vision_source": vision_params.get("source"),
    }))

    _ensure_bureau()
    _push_trigger(item, quantity=need)

    return jsonify({
        "ok": True,
        "message": "Negotiation triggered — watching for updates via SSE",
        "item": item,
        "need": need,
        "vision_source": vision_params.get("source"),
        "vision_a": vision_params.get("vision_a"),
        "vision_b": vision_params.get("vision_b"),
    })


@app.route("/api/bureau/start", methods=["POST"])
def api_bureau_start():
    """Manually start or restart the bureau subprocess."""
    _ensure_bureau()
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
