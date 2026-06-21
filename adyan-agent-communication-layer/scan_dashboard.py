"""scan_dashboard.py — Baymax scan dashboard (runs on MacBook A).

Single pane of glass for the demo: two live camera feeds, a one-click Scan, and a
one-click Scan & Negotiate that triggers the FRONT agent and streams the live
negotiation back onto the page.

    WORKER_B_URL=http://<B-ip>:8765 ./.venv/bin/python scan_dashboard.py   # :8000

Talks to:
    * the two camera_worker.py servers (POST /scan; their /stream is embedded
      directly in the page <img> tags, loaded by the browser)
    * Redis, via dashboard_bus: RPUSH baymax:trigger (negotiate) + SUB baymax:narration
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

import dashboard_bus

WORKER_A_URL = os.getenv("WORKER_A_URL", "http://localhost:8765")
WORKER_B_URL = os.getenv("WORKER_B_URL", "http://localhost:8766")
ITEM = os.getenv("BAYMAX_ITEM", "saline")
PORT = int(os.getenv("DASHBOARD_PORT", "8000"))

app = FastAPI()


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
    trig = dashboard_bus.push_trigger(item=ITEM, requester="Hospital A", quantity=None)
    return JSONResponse({"scanned": scanned, "triggered": trig})


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
            {"state": "connected", "detail": "Waiting for a negotiation…"}) + "\n\n"
        async for msg in dashboard_bus.narration_events():
            yield "data: " + json.dumps(msg) + "\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


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


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Baymax · Supply Operations</title>
<style>
  :root{--bg:#0b1220;--panel:#121b2e;--line:#1e2b45;--ink:#e8eef9;--mut:#8aa0c6;
        --ok:#34d399;--warn:#fbbf24;--low:#f87171;--accent:#5eead4;}
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#15233f,#0b1220);
       color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto}
  header{display:flex;align-items:center;gap:12px;padding:18px 26px;border-bottom:1px solid var(--line)}
  header .dot{width:11px;height:11px;border-radius:50%;background:var(--accent);box-shadow:0 0 14px var(--accent)}
  header h1{font-size:17px;letter-spacing:.5px;margin:0;font-weight:650}
  header .sub{color:var(--mut);font-size:13px;margin-left:auto}
  main{max-width:1080px;margin:0 auto;padding:24px}
  .panels{display:grid;grid-template-columns:1fr 1fr;gap:18px}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden}
  .panel h2{margin:0;padding:14px 16px;font-size:14px;letter-spacing:.4px;border-bottom:1px solid var(--line);
            display:flex;align-items:center;gap:8px}
  .feed{aspect-ratio:4/3;background:#05080f;display:block;width:100%;object-fit:cover}
  .card{padding:14px 16px;display:grid;grid-template-columns:auto 1fr auto;gap:8px 12px;align-items:center}
  .qty{font-size:30px;font-weight:700;font-variant-numeric:tabular-nums}
  .bar{height:10px;border-radius:6px;background:#0a1020;overflow:hidden;grid-column:1/-1}
  .bar>span{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#38bdf8)}
  .meta{color:var(--mut);font-size:13px}
  .pill{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;
        padding:3px 9px;border-radius:999px;justify-self:end}
  .pill.ok{background:rgba(52,211,153,.15);color:var(--ok)}
  .pill.warning{background:rgba(251,191,36,.15);color:var(--warn)}
  .pill.low{background:rgba(248,113,113,.15);color:var(--low)}
  .actions{display:flex;gap:12px;justify-content:center;margin:22px 0}
  button{font:inherit;font-weight:650;color:#04121a;background:var(--accent);border:0;
         padding:12px 20px;border-radius:12px;cursor:pointer;transition:transform .05s,filter .15s}
  button.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}
  button:hover{filter:brightness(1.07)} button:active{transform:translateY(1px)}
  button:disabled{opacity:.5;cursor:progress}
  .gate{display:none;gap:10px;justify-content:center;align-items:center;margin:0 0 18px;
        padding:12px;border:1px solid var(--accent);border-radius:12px;background:rgba(94,234,212,.06)}
  .gate .gatelbl{color:var(--mut);font-size:13px;margin-right:4px}
  .feedwrap{background:var(--panel);border:1px solid var(--line);border-radius:16px;margin-top:8px}
  .feedwrap h3{margin:0;padding:12px 16px;font-size:13px;letter-spacing:.4px;color:var(--mut);
               border-bottom:1px solid var(--line)}
  #log{list-style:none;margin:0;padding:8px 0;max-height:300px;overflow:auto;
       font:13px/1.5 ui-monospace,Menlo,monospace}
  #log li{padding:6px 16px;border-bottom:1px solid rgba(30,43,69,.5)}
  #log .state{color:var(--accent);font-weight:700;margin-right:8px}
  #log li.final .state{color:var(--ok)}
  #log li.failed .state{color:var(--low)}
</style></head><body>
<header><span class="dot"></span><h1>BAYMAX · Supply Operations</h1>
  <span class="sub">item: <b>__ITEM__</b> · two-camera live scan</span></header>
<main>
  <div class="panels">
    <section class="panel"><h2>🏥 Hospital A</h2>
      <img class="feed" src="__A__/stream" alt="Hospital A live feed"
           onerror="this.style.opacity=.25">
      <div class="card" id="card-a">
        <div class="qty" data-qty>—</div><div class="meta" data-meta>not scanned</div>
        <div class="pill" data-pill></div><div class="bar"><span data-bar style="width:0"></span></div>
      </div></section>
    <section class="panel"><h2>🏥 Hospital B</h2>
      <img class="feed" src="__B__/stream" alt="Hospital B live feed"
           onerror="this.style.opacity=.25">
      <div class="card" id="card-b">
        <div class="qty" data-qty>—</div><div class="meta" data-meta>not scanned</div>
        <div class="pill" data-pill></div><div class="bar"><span data-bar style="width:0"></span></div>
      </div></section>
  </div>
  <div class="actions">
    <button class="ghost" id="btn-scan">⛶ Scan Hospitals</button>
    <button id="btn-neg">⚡ Scan &amp; Negotiate</button>
  </div>
  <div class="gate" id="gate">
    <span class="gatelbl">Admin decision needed</span>
    <button data-dec="approve">✅ Approve trade</button>
    <button class="ghost" data-dec="order">📦 Order externally</button>
    <button class="ghost" data-dec="reject">✖ Reject</button>
  </div>
  <div class="feedwrap"><h3>NEGOTIATION FEED</h3><ul id="log"></ul></div>
</main>
<script>
const $=s=>document.querySelector(s);
function paint(side,d){
  const card=$("#card-"+side); if(!card) return;
  if(!d||d.error){card.querySelector("[data-meta]").textContent="error: "+((d&&d.error)||"no data");return;}
  card.querySelector("[data-qty]").textContent=d.qty;
  card.querySelector("[data-meta]").textContent=
    (d.item||"")+" · surplus "+(d.surplus??"–")+" · "+(d.source||"");
  const pill=card.querySelector("[data-pill]");
  pill.textContent=d.status||""; pill.className="pill "+(d.status||"");
  card.querySelector("[data-bar]").style.width=Math.max(2,Math.min(100,d.pct||0))+"%";
}
async function scan(){
  const b=$("#btn-scan"); b.disabled=true;
  try{const r=await fetch("/scan",{method:"POST"});const j=await r.json();
      paint("a",j.hospital_a);paint("b",j.hospital_b);}
  catch(e){}finally{b.disabled=false;}
}
async function negotiate(){
  const b=$("#btn-neg"); b.disabled=true; $("#log").innerHTML=""; hideGate();
  try{const r=await fetch("/scan-and-negotiate",{method:"POST"});const j=await r.json();
      paint("a",j.scanned.hospital_a);paint("b",j.scanned.hospital_b);}
  catch(e){}finally{b.disabled=false;}
}
$("#btn-scan").onclick=scan; $("#btn-neg").onclick=negotiate;
let curReq=null;
function showGate(rid){curReq=rid; $("#gate").style.display="flex";}
function hideGate(){$("#gate").style.display="none";}
document.querySelectorAll("#gate button").forEach(btn=>btn.onclick=async()=>{
  const dec=btn.getAttribute("data-dec"); hideGate();
  try{await fetch("/decide",{method:"POST",headers:{"Content-Type":"application/json"},
       body:JSON.stringify({req_id:curReq,decision:dec})});}catch(e){}
});
const es=new EventSource("/events");
es.onmessage=e=>{
  let m; try{m=JSON.parse(e.data)}catch(_){return;}
  const li=document.createElement("li");
  const st=(m.state||"").toLowerCase();
  if(m.final) li.className="final"; if(st==="failed") li.className="failed";
  li.innerHTML='<span class="state">'+(m.state||"")+"</span>"+(m.detail||"");
  $("#log").appendChild(li); $("#log").scrollTop=$("#log").scrollHeight;
  if(st==="awaiting_approval") showGate(m.req_id);
  if(m.final) hideGate();
};
</script></body></html>"""


def main() -> None:
    print(f"[scan_dashboard] :{PORT}  A={WORKER_A_URL}  B={WORKER_B_URL}  item={ITEM}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
