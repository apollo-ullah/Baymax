"""scan_dashboard.py — Baymax scan dashboard (FastAPI web UI; runs on MacBook A).

Single pane of glass for the demo: two live camera feeds, a one-click Scan, and a
one-click Scan & Negotiate that triggers the FRONT agent and streams the live
negotiation back onto the page.

HTTP surface:
  * / or /index.html      : web UI (HTML, served from dashboard_page.html)
  * /stream-a             : MJPEG proxy stream from camera worker A
  * /stream-b             : MJPEG proxy stream from camera worker B (Tailscale peer)
  * /scan                 : POST — scan both cameras; returns counts JSON
  * /scan-and-negotiate   : POST — scan + push a trigger onto the dashboard bus
  * /decide  (= /decision): POST — push an admin approve/order/reject onto the bus
  * /events  (= /narration): GET — SSE stream of negotiation milestone events
  * /health               : GET — camera worker reachability probe

    ./.venv/bin/python scan_dashboard.py   # :8080 (Bureau uses :8000)

Env:
  WORKER_A_URL / BAYMAX_WORKER_A_URL    camera worker A (default http://localhost:8765)
  WORKER_B_URL / BAYMAX_WORKER_B_URL    camera worker B (default http://localhost:8766)
  BAYMAX_ITEM  / BAYMAX_SCAN_ITEM       item to scan for (default "Saline")
  DASHBOARD_PORT / BAYMAX_DASHBOARD_PORT  dashboard HTTP port (default 8080)

Over Tailscale (when public WiFi blocks LAN peers):

    BAYMAX_USE_TAILSCALE=1 TAILSCALE_SERVER=baymax-a TAILSCALE_PEER_B=baymax-b \\
        ./.venv/bin/python scan_dashboard.py
    # or: ./scripts/tailscale_server.sh dashboard

Run standalone (dashboard only; pair with run_dashboard_demo.py or run_front.py):
    ./.venv/bin/python scan_dashboard.py

Talks to:
    * the two camera_worker.py servers (POST /scan; their /stream is embedded
      directly in the page <img> tags, loaded by the browser, or proxied via
      /stream-a and /stream-b when the browser cannot reach a worker directly)
    * Redis, via dashboard_bus: RPUSH baymax:trigger (negotiate) + SUB baymax:narration
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import dashboard_bus
import tailscale_hosts

tailscale_hosts.apply_env("server")

# Honor both env spellings: OURS (WORKER_A_URL / BAYMAX_ITEM / DASHBOARD_PORT,
# also set by tailscale_hosts.apply_env) and THEIRS (BAYMAX_* prefixed).
WORKER_A_URL = os.getenv("WORKER_A_URL") or os.getenv("BAYMAX_WORKER_A_URL", "http://localhost:8765")
WORKER_B_URL = os.getenv("WORKER_B_URL") or os.getenv("BAYMAX_WORKER_B_URL", "http://localhost:8766")
ITEM = os.getenv("BAYMAX_ITEM") or os.getenv("BAYMAX_SCAN_ITEM", "Saline")
PORT = int(os.getenv("DASHBOARD_PORT") or os.getenv("BAYMAX_DASHBOARD_PORT", "8080"))
MIN_NEED = int(os.getenv("BAYMAX_DEMO_MIN_NEED", "1"))

# Aliases so other modules can import either name (run_dashboard_demo.py imports
# DASHBOARD_PORT; SCAN_ITEM mirrors ITEM for THEIRS callers).
DASHBOARD_PORT = PORT
SCAN_ITEM = ITEM

app = FastAPI(title="Baymax Scan Dashboard", docs_url=None, redoc_url=None)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# The page lives in dashboard_page.html so the markup/CSS can be edited without
# touching this module. Read once at import; tokens are filled per request.
_PAGE_PATH = Path(__file__).resolve().parent / "dashboard_page.html"
PAGE = _PAGE_PATH.read_text(encoding="utf-8")


def _shortfall_from_scan(side: dict) -> int:
    """Derive need from a camera /scan response (reserve - qty)."""
    if not side or side.get("error"):
        return 0
    if "shortfall" in side:
        return max(0, int(side["shortfall"]))
    qty = int(side.get("qty") or 0)
    reserve = int(side.get("reserve") or 50)
    return max(0, reserve - qty)


def _scan_summary(scanned: dict) -> dict:
    a = scanned.get("hospital_a") or {}
    b = scanned.get("hospital_b") or {}
    need = _shortfall_from_scan(a)
    spare_b = max(0, int(b.get("qty") or 0) - int(b.get("reserve") or 1))
    return {
        "hospital_a_qty": a.get("qty"),
        "hospital_b_qty": b.get("qty"),
        "hospital_a_target": a.get("reserve"),
        "hospital_b_reserve": b.get("reserve"),
        "hospital_a_source": a.get("source"),
        "hospital_b_source": b.get("source"),
        "shortfall": need,
        "hospital_b_spare": spare_b,
        "will_request": need if need > 0 else 0,
        "demo_pitch": (
            f"A has {a.get('qty')} (needs {a.get('reserve')} on hand, short {need}); "
            f"B has {b.get('qty')} (keeps {b.get('reserve')} safe, can spare {spare_b})"
            if need > 0 else None
        ),
    }


async def _scan_one(url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{url}/scan")
            return r.json()
    except Exception as e:
        return {"error": str(e)}


async def _scan_both() -> dict:
    a, b = await asyncio.gather(_scan_one(WORKER_A_URL), _scan_one(WORKER_B_URL))
    return {"hospital_a": a, "hospital_b": b}


async def _proxy_stream(worker_url: str) -> AsyncIterator[bytes]:
    """Proxy the MJPEG stream from a camera worker (for browsers that cannot
    reach the worker directly via the page's embedded <img> tags)."""
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{worker_url}/stream") as response:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    yield chunk
    except Exception as exc:
        # Return a minimal MJPEG boundary with an error frame so the browser img
        # tag just shows a blank rather than breaking the whole dashboard.
        yield b"--frame\r\nContent-Type: text/plain\r\n\r\noffline: " + str(exc).encode() + b"\r\n"


@app.get("/stream-a")
async def stream_a():
    return StreamingResponse(
        _proxy_stream(WORKER_A_URL),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/stream-b")
async def stream_b():
    return StreamingResponse(
        _proxy_stream(WORKER_B_URL),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.post("/scan")
async def scan():
    return JSONResponse(await _scan_both())


@app.post("/scan-and-negotiate")
async def scan_and_negotiate():
    scanned = await _scan_both()
    summary = _scan_summary(scanned)
    need = summary["shortfall"]
    if need <= 0:
        return JSONResponse(
            status_code=400,
            content={
                "error": "No shortfall detected at Hospital A",
                "scanned": scanned,
                "summary": summary,
                "hint": "Hold 2 bottles in frame for each hospital. A needs "
                        f"target={summary.get('hospital_a_target') or 3} on hand.",
            },
        )
    trig = dashboard_bus.push_trigger(
        item=ITEM, requester="Hospital A", quantity=need)
    return JSONResponse({"scanned": scanned, "summary": summary, "triggered": trig})


@app.post("/decide")
@app.post("/decision")
async def decide(req: Request):
    body = await req.json()
    decision = (body.get("decision") or "").lower()
    if decision not in ("approve", "order", "reject"):
        return JSONResponse(status_code=400, content={"error": "bad decision"})
    trig = dashboard_bus.push_decision(body.get("req_id"), decision)
    return JSONResponse({"ok": True, **trig})


def _events_response() -> StreamingResponse:
    async def gen():
        yield "data: " + json.dumps(
            {"state": "connected", "detail": "Listening for negotiations…"}) + "\n\n"
        async for msg in dashboard_bus.narration_events():
            yield "data: " + json.dumps(msg) + "\n\n"
            # Keepalive comment so proxies/browsers don't drop idle SSE.
            yield ": keepalive\n\n"
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/events")
async def events():
    return _events_response()


@app.get("/narration")
async def narration():
    """SSE stream alias of /events: negotiation milestone events from the bus."""
    return _events_response()


@app.get("/health")
async def health():
    async def _h(url):
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                return (await c.get(f"{url}/health")).json()
        except Exception as e:
            return {"ok": False, "error": str(e)}
    a, b = await asyncio.gather(_h(WORKER_A_URL), _h(WORKER_B_URL))
    return {"worker_a": a, "worker_b": b}


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
def index():
    return (PAGE.replace("__A__", WORKER_A_URL)
                .replace("__B__", WORKER_B_URL)
                .replace("__ITEM__", ITEM))


def main() -> None:
    print(f"[scan_dashboard] :{PORT}  A={WORKER_A_URL}  B={WORKER_B_URL}  item={ITEM}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
