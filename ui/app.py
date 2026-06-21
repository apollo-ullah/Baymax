"""
Stockpile Pipeline Dashboard — Flask UI.

Reads the live Redis state for every pipeline stage and serves a dashboard
that auto-refreshes every 3 seconds. No uAgents need to be running; the UI
reads Redis directly.

Endpoints:
    GET  /              dashboard HTML
    GET  /api/state     JSON snapshot of all pipeline data
    GET  /image/latest  latest capture JPEG (or placeholder)
    POST /api/capture   run mock capture (--counts a=4,b=2) → Redis
    POST /api/refresh_who   re-run WHO fetch + Claude reasoning → Redis

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
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, send_file, request

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# ── Bridge to redis/src ──────────────────────────────────────────────────────
_REDIS_SRC = Path(__file__).resolve().parents[1] / "redis" / "src"
if str(_REDIS_SRC) not in sys.path:
    sys.path.insert(0, str(_REDIS_SRC))

# ── Bridge to who_agent fetcher ──────────────────────────────────────────────
_FETCH_ROOT = Path(__file__).resolve().parents[1]
if str(_FETCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_FETCH_ROOT))

log = logging.getLogger("stockpile_ui")
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = Flask(__name__)

REGION = os.getenv("FORECAST_REGION", "san_francisco")
HARDWARE_DIR = Path(__file__).resolve().parents[1] / "hardware" / "camera connection"
SYNC_SCRIPT = HARDWARE_DIR / "sync_to_redis.py"
AGENT_DIR = Path(__file__).resolve().parents[1] / "agent-communication-layer"
AGENT_VENV_PYTHON = AGENT_DIR / ".venv" / "bin" / "python"


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


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("index.html", region=REGION)


@app.route("/api/state")
def api_state():
    return jsonify(get_state())


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


@app.route("/api/capture", methods=["POST"])
def api_capture():
    """
    Capture a real frame from the MacBook camera, send to Claude Vision,
    parse saline counts, and write them to Redis.
    """
    log.info(json.dumps({
        "tag": "VISION", "file": "ui/app.py",
        "action": "capture_triggered",
        "source": "camera + Claude Vision",
    }))

    try:
        result = subprocess.run(
            [sys.executable, str(SYNC_SCRIPT)],
            capture_output=True, text=True, timeout=60,
            cwd=str(HARDWARE_DIR),
        )
        output = result.stdout + result.stderr
        log.info(json.dumps({
            "tag": "VISION", "file": "ui/app.py",
            "action": "capture_complete",
            "stdout": result.stdout.strip(),
            "returncode": result.returncode,
        }))
        if result.returncode != 0:
            return jsonify({"ok": False, "error": output}), 500
        return jsonify({"ok": True, "output": output})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Camera capture timed out (60s)"}), 500
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


def _vision_to_negotiation_params() -> dict:
    """
    Read vision:latest from Redis and derive negotiation parameters.

    Hospital A's saline count from the camera becomes the current on-hand qty.
    We negotiate to reach a target of 20 units — so need = max(0, 20 - count_a).
    Hospital B's count is written to Redis inventory so the agent reads it as
    surplus when STOCKPILE_REDIS=1.

    Returns {"item", "need", "vision_a", "vision_b", "target", "source"}.
    Falls back to IV fluids / need=200 when no vision data is available.
    """
    SALINE_TARGET = int(os.getenv("SALINE_TARGET", "20"))
    try:
        r = _redis()
        raw = r.get("vision:latest")
        if not raw:
            return {"item": "IV fluids", "need": 200, "source": "default (no vision data)"}
        v = json.loads(raw)
        count_a = (v.get("hospital_a") or {}).get("saline")
        count_b = (v.get("hospital_b") or {}).get("saline")
        if count_a is None:
            return {"item": "IV fluids", "need": 200, "source": "default (vision missing hospital_a)"}
        need = max(1, SALINE_TARGET - count_a)
        return {
            "item": "Saline",
            "need": need,
            "vision_a": count_a,
            "vision_b": count_b,
            "target": SALINE_TARGET,
            "source": f"vision (Hospital A has {count_a}, target {SALINE_TARGET} → need {need})",
        }
    except Exception as e:
        return {"item": "IV fluids", "need": 200, "source": f"default (redis error: {e})"}


@app.route("/api/negotiate", methods=["POST"])
def api_negotiate():
    """
    Run the Fetch.ai 3-agent Bureau negotiation driven by the latest camera
    vision counts. Hospital A's saline count sets the shortfall; B and C offer
    from their Redis surplus.

    Body (JSON, all optional — overrides vision-derived values):
        item   — supply item
        need   — units needed
        redis  — use live Redis inventory (default true)
    """
    data = request.get_json(silent=True) or {}

    # Derive from vision unless caller overrides
    vision_params = _vision_to_negotiation_params()
    item     = data.get("item") or vision_params["item"]
    need     = int(data.get("need") or vision_params["need"])
    use_redis = data.get("redis", True)

    if not AGENT_VENV_PYTHON.exists():
        return jsonify({"ok": False,
                        "error": f"Agent venv not found at {AGENT_VENV_PYTHON}. "
                                 "Run: cd agent-communication-layer && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"}), 500

    log.info(json.dumps({
        "tag": "FETCH", "file": "ui/app.py",
        "action": "negotiate_triggered",
        "item": item, "need": need, "use_redis": use_redis,
        "vision_source": vision_params.get("source"),
    }))

    env = {**os.environ,
           "STOCKPILE_EXIT_WHEN_DONE": "1",
           "STOCKPILE_ITEM": item,
           "STOCKPILE_NEED": str(need),
           "REDIS_URL": os.getenv("REDIS_URL", "redis://localhost:6379")}
    if use_redis:
        env["STOCKPILE_REDIS"] = "1"

    try:
        result = subprocess.run(
            [str(AGENT_VENV_PYTHON), "stockpile_agents.py"],
            capture_output=True, text=True, timeout=45,
            cwd=str(AGENT_DIR), env=env,
        )
        raw_log = result.stdout + result.stderr

        # Parse key events from the log for structured display
        events = _parse_negotiation_log(raw_log)

        log.info(json.dumps({
            "tag": "FETCH", "file": "ui/app.py",
            "action": "negotiate_complete",
            "returncode": result.returncode,
            "events_found": len(events),
        }))
        return jsonify({"ok": True, "log": raw_log, "events": events,
                        "item": item, "need": need,
                        "vision_source": vision_params.get("source"),
                        "vision_a": vision_params.get("vision_a"),
                        "vision_b": vision_params.get("vision_b")})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Negotiation timed out (45s)"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
    port = int(os.getenv("UI_PORT", "5001"))
    print(f"\n  Stockpile Pipeline Dashboard → http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
