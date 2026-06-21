"""scan_dashboard.py — Baymax scan dashboard (FastAPI web UI).

Serves a live two-camera scan dashboard over HTTP:
  * / or /index.html   : web UI (HTML)
  * /stream-a          : MJPEG proxy stream from camera worker A
  * /stream-b          : MJPEG proxy stream from camera worker B (Tailscale peer)
  * /scan              : POST — trigger a scan of both cameras; returns counts JSON
  * /scan-and-negotiate: POST — scan + push a trigger onto the dashboard bus
  * /decision          : POST — push an admin approve/order/reject onto the bus
  * /narration         : GET  — SSE stream of negotiation milestone events

Env:
  BAYMAX_WORKER_A_URL   URL of camera worker A (default http://localhost:8765)
  BAYMAX_WORKER_B_URL   URL of camera worker B (default http://localhost:8766)
  BAYMAX_SCAN_ITEM      item to scan for (default "saline")
  BAYMAX_DASHBOARD_PORT dashboard HTTP port (default 8080)

Run standalone (dashboard only; pair with run_dashboard_demo.py or run_front.py):
    ./.venv/bin/python scan_dashboard.py
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

import dashboard_bus

WORKER_A_URL = os.getenv("BAYMAX_WORKER_A_URL", "http://localhost:8765")
WORKER_B_URL = os.getenv("BAYMAX_WORKER_B_URL", "http://localhost:8766")
SCAN_ITEM = os.getenv("BAYMAX_SCAN_ITEM", "saline")
DASHBOARD_PORT = int(os.getenv("BAYMAX_DASHBOARD_PORT", "8080"))

app = FastAPI(title="Baymax Scan Dashboard", docs_url=None, redoc_url=None)

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Baymax Scan Dashboard</title>
<style>
  body { font-family: sans-serif; background: #0a0a0a; color: #e0e0e0; margin: 0; }
  h1 { text-align: center; padding: 1rem; color: #58a6ff; margin: 0; }
  .cameras { display: flex; gap: 1rem; justify-content: center; padding: 1rem; }
  .cam-box { flex: 1; max-width: 640px; }
  .cam-box h3 { text-align: center; margin-bottom: 0.5rem; color: #aaa; }
  img.stream { width: 100%; border: 2px solid #333; border-radius: 8px;
               background: #111; min-height: 240px; }
  .controls { display: flex; gap: 1rem; justify-content: center; padding: 1rem; }
  button { padding: 0.6rem 1.4rem; border: none; border-radius: 6px;
           font-size: 1rem; cursor: pointer; }
  #btn-scan { background: #238636; color: #fff; }
  #btn-negotiate { background: #1f6feb; color: #fff; }
  #btn-scan:hover { background: #2ea043; }
  #btn-negotiate:hover { background: #388bfd; }
  #scan-result { text-align: center; color: #8b949e; min-height: 1.5rem; }
  .admin-row { display: flex; gap: 0.5rem; justify-content: center; padding: 0.5rem 1rem; }
  #req-id-input { padding: 0.4rem 0.8rem; border-radius: 6px; border: 1px solid #444;
                  background: #161b22; color: #e0e0e0; font-size: 0.9rem; width: 140px; }
  .btn-approve { background: #238636; color: #fff; border: none; border-radius: 6px;
                 padding: 0.4rem 1rem; cursor: pointer; font-size: 0.9rem; }
  .btn-order   { background: #9e6a03; color: #fff; border: none; border-radius: 6px;
                 padding: 0.4rem 1rem; cursor: pointer; font-size: 0.9rem; }
  .btn-reject  { background: #b91c1c; color: #fff; border: none; border-radius: 6px;
                 padding: 0.4rem 1rem; cursor: pointer; font-size: 0.9rem; }
  #narration { margin: 1rem auto; max-width: 900px; background: #161b22;
               border: 1px solid #30363d; border-radius: 8px; padding: 1rem;
               min-height: 200px; max-height: 400px; overflow-y: auto;
               font-family: monospace; font-size: 0.85rem; }
  .msg { padding: 0.2rem 0; border-bottom: 1px solid #21262d; }
  .msg.final { color: #3fb950; }
  .msg.failed { color: #f85149; }
  .msg.awaiting { color: #d29922; }
</style>
</head>
<body>
<h1>🏥 Baymax Scan Dashboard</h1>
<div class="cameras">
  <div class="cam-box">
    <h3>Hospital A</h3>
    <img class="stream" src="/stream-a" alt="Camera A feed" onerror="this.alt='Camera A offline'">
  </div>
  <div class="cam-box">
    <h3>Hospital B</h3>
    <img class="stream" src="/stream-b" alt="Camera B feed" onerror="this.alt='Camera B offline'">
  </div>
</div>
<div id="scan-result">—</div>
<div class="controls">
  <button id="btn-scan" onclick="doScan()">Scan Shelves</button>
  <button id="btn-negotiate" onclick="doNegotiate()">Scan + Negotiate</button>
</div>
<div class="admin-row">
  <span style="color:#8b949e; font-size:0.9rem; align-self:center">Admin:</span>
  <input id="req-id-input" placeholder="req_id (optional)">
  <button class="btn-approve" onclick="sendDecision('approve')">Approve</button>
  <button class="btn-order"   onclick="sendDecision('order')">Order</button>
  <button class="btn-reject"  onclick="sendDecision('reject')">Reject</button>
</div>
<div id="narration"></div>
<script>
async function doScan() {
  document.getElementById('scan-result').textContent = 'Scanning…';
  const r = await fetch('/scan', {method:'POST'});
  const j = await r.json();
  document.getElementById('scan-result').textContent =
    'A: ' + JSON.stringify(j.a) + '  |  B: ' + JSON.stringify(j.b);
}
async function doNegotiate() {
  document.getElementById('scan-result').textContent = 'Scanning + negotiating…';
  const r = await fetch('/scan-and-negotiate', {method:'POST'});
  const j = await r.json();
  document.getElementById('scan-result').textContent = j.message || JSON.stringify(j);
}
async function sendDecision(decision) {
  const req_id = document.getElementById('req-id-input').value.trim();
  const body = {decision};
  if (req_id) body.req_id = req_id;
  const r = await fetch('/decision', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const j = await r.json();
  appendNarration({state:'admin_decision', detail: decision + (req_id?' '+req_id:'')});
}
const log = document.getElementById('narration');
function appendNarration(payload) {
  const div = document.createElement('div');
  div.className = 'msg' + (payload.final?' final':'')
    + (payload.state==='failed'?' failed':'')
    + (payload.state==='awaiting_approval'?' awaiting':'');
  div.textContent = '[' + (payload.state||'') + '] ' + (payload.detail||'');
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}
const es = new EventSource('/narration');
es.onmessage = e => { try { appendNarration(JSON.parse(e.data)); } catch(_){} };
es.onerror = () => { appendNarration({state:'sse_error', detail:'SSE disconnected — retrying'}); };
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_HTML)


async def _proxy_stream(worker_url: str) -> AsyncIterator[bytes]:
    """Proxy the MJPEG stream from a camera worker."""
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


async def _scan_worker(worker_url: str) -> dict:
    """POST /scan to a camera worker and return its JSON result."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{worker_url}/scan")
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/scan")
async def scan():
    """Trigger scans on both camera workers and return their counts."""
    a, b = await asyncio.gather(_scan_worker(WORKER_A_URL), _scan_worker(WORKER_B_URL))
    return JSONResponse({"a": a, "b": b})


@app.post("/scan-and-negotiate")
async def scan_and_negotiate(request: Request):
    """Scan both cameras and push a trigger onto the dashboard bus if a shortfall is found."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    item = body.get("item", SCAN_ITEM)
    a, b = await asyncio.gather(_scan_worker(WORKER_A_URL), _scan_worker(WORKER_B_URL))

    # If the camera scan failed, do NOT fabricate a need from count=0/reserve=1 —
    # defer to the agent's inventory-derived shortfall (quantity=None) so the real
    # shortfall (and the split it implies) drives the negotiation.
    if "error" in a:
        dashboard_bus.push_trigger(item, requester="Hospital A", quantity=None)
        return JSONResponse({
            "message": f"Camera offline — negotiating {item} from inventory shortfall.",
            "a": a, "b": b, "triggered": True,
        })

    # Derive the shortfall from worker A's scan result.
    a_count = a.get("count", 0)
    a_reserve = a.get("reserve", 1)
    a_shortfall = max(0, a_reserve - a_count)

    if a_shortfall <= 0:
        return JSONResponse({
            "message": f"No shortfall detected: Hospital A has {a_count} {item} (reserve {a_reserve}).",
            "a": a, "b": b, "triggered": False,
        })

    dashboard_bus.push_trigger(item, requester="Hospital A", quantity=a_shortfall)
    return JSONResponse({
        "message": f"Shortfall detected ({a_count} on hand, reserve {a_reserve}). "
                   f"Trigger pushed for {a_shortfall} {item}.",
        "a": a, "b": b, "triggered": True,
    })


@app.post("/decision")
async def decision(request: Request):
    """Push an admin approve/order/reject decision onto the bus."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")
    dec = body.get("decision", "").lower()
    if dec not in ("approve", "order", "reject"):
        raise HTTPException(status_code=400, detail="decision must be approve, order, or reject")
    req_id = body.get("req_id", "")
    dashboard_bus.push_decision(req_id, dec)
    return JSONResponse({"ok": True, "decision": dec, "req_id": req_id})


@app.get("/narration")
async def narration():
    """SSE stream: push narration events as they arrive via dashboard_bus."""
    async def _generate():
        import json
        async for payload in dashboard_bus.narration_events():
            data = json.dumps(payload)
            yield f"data: {data}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import uvicorn
    print(f"Baymax scan dashboard on http://0.0.0.0:{DASHBOARD_PORT}")
    print(f"  worker A : {WORKER_A_URL}")
    print(f"  worker B : {WORKER_B_URL}")
    uvicorn.run(app, host="0.0.0.0", port=DASHBOARD_PORT)
