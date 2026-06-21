"""scan_dashboard.py — Baymax scan dashboard (runs on MacBook A).

Single pane of glass for the demo: two live camera feeds, a one-click Scan, and a
one-click Scan & Negotiate that triggers the FRONT agent and streams the live
negotiation back onto the page.

    ./.venv/bin/python scan_dashboard.py   # :8080 (Bureau uses :8000)

Over Tailscale (when public WiFi blocks LAN peers):

    BAYMAX_USE_TAILSCALE=1 TAILSCALE_SERVER=baymax-a TAILSCALE_PEER_B=baymax-b \\
        ./.venv/bin/python scan_dashboard.py
    # or: ./scripts/tailscale_server.sh dashboard

Talks to:
    * the two camera_worker.py servers (POST /scan; their /stream is embedded
      directly in the page <img> tags, loaded by the browser)
    * Redis, via dashboard_bus: RPUSH baymax:trigger (negotiate) + SUB baymax:narration
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
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

WORKER_A_URL = os.getenv("WORKER_A_URL", "http://localhost:8765")
WORKER_B_URL = os.getenv("WORKER_B_URL", "http://localhost:8766")
ITEM = os.getenv("BAYMAX_ITEM", "Saline")
PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
MIN_NEED = int(os.getenv("BAYMAX_DEMO_MIN_NEED", "1"))

app = FastAPI()

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
async def decide(req: Request):
    body = await req.json()
    decision = body.get("decision")
    if decision not in ("approve", "order", "reject"):
        return JSONResponse(status_code=400, content={"error": "bad decision"})
    trig = dashboard_bus.push_decision(body.get("req_id"), decision)
    return JSONResponse({"ok": True, **trig})


@app.get("/events")
async def events():
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
def index():
    return (PAGE.replace("__A__", WORKER_A_URL)
                .replace("__B__", WORKER_B_URL)
                .replace("__ITEM__", ITEM))


def main() -> None:
    print(f"[scan_dashboard] :{PORT}  A={WORKER_A_URL}  B={WORKER_B_URL}  item={ITEM}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
