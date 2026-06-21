"""
Hospital A <-> Hospital B human approval service (FastAPI).

Drives the approval state machine over Poke notifications with tappable action
links, so accept/deny is a real tapped link (deterministic) rather than parsed
free text. Run from the repo root:

    uvicorn fetch.approval.service:app --port 8080

Flow:
    POST /shortage  -> detect (A short, B surplus) -> text Dr A (accept/reject)
    /req/{id}/a/accept -> text Dr B (accept/deny)
    /req/{id}/a/reject -> end
    /req/{id}/b/accept -> log transfer (dashboard) + confirm both
    /req/{id}/b/deny   -> notify A + publish approval_b_denied (partner's buy hook)
"""

import json
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from ..shared import redis_io
from . import state
from .inventory_seam import detect_shortage, make_shortage
from .imessage_client import notify

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stockpile.approval")

BASE_URL = os.getenv("APPROVAL_BASE_URL", "http://localhost:8080").rstrip("/")

app = FastAPI(title="Stockpile Approval Service")


def _link(rid: str, path: str) -> str:
    return f"{BASE_URL}/req/{rid}/{path}"


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>"
        "body{font-family:-apple-system,system-ui,sans-serif;max-width:640px;"
        "margin:48px auto;padding:0 20px;color:#111}"
        ".card{border:1px solid #e6e6e6;border-radius:14px;padding:28px;"
        "box-shadow:0 2px 14px rgba(0,0,0,.06)}"
        "a.btn{display:inline-block;padding:12px 20px;border-radius:10px;"
        "text-decoration:none;font-weight:600;margin:8px 8px 0 0}"
        ".ok{background:#16a34a;color:#fff}.no{background:#dc2626;color:#fff}"
        ".muted{color:#666}</style></head>"
        f"<body><div class=card>{body}</div></body></html>"
    )


@app.post("/shortage")
def trigger_shortage(item: str | None = None, requester: str | None = None,
                     provider: str | None = None, quantity: int | None = None):
    """Start an approval flow. Auto-detects who's short, OR force a direction by
    passing requester + provider (e.g. requester=hospital_b&provider=hospital_a
    for a B→A request). The requester's doctor is texted first."""
    if requester and provider:
        sh = make_shortage(requester, provider, item or "IV Fluids", quantity or 110)
    else:
        sh = detect_shortage(item)
    rec = state.create(sh)
    rid = rec["request_id"]
    msg = (
        f"🏥 {sh.requester_name}: running low on {sh.item}.\n"
        f"{sh.provider_name} can spare {sh.quantity}. Request a transfer?\n"
        f"✅ Request: {_link(rid, 'a/accept')}\n"
        f"❌ Skip: {_link(rid, 'a/reject')}"
    )
    notify(sh.requester_id, msg)
    rec = state.update(rid, "A_NOTIFIED")
    return JSONResponse({"request_id": rid, "status": rec["status"], "request": rec})


@app.get("/req/{rid}")
def get_req(rid: str):
    rec = state.get(rid)
    if not rec:
        return JSONResponse({"error": "not found"}, status_code=404)
    return rec


@app.get("/req/{rid}/a/accept")
def a_accept(rid: str):
    rec = state.get(rid)
    if not rec:
        return _page("Not found", "<h2>Request not found</h2>")
    state.update(rid, "A_ACCEPTED")
    msg = (
        f"🏥 {rec['requester_name']} requests {rec['quantity']} {rec['item']} "
        f"from {rec['provider_name']}.\nApprove this transfer?\n"
        f"✅ Accept: {_link(rid, 'b/accept')}\n"
        f"❌ Deny: {_link(rid, 'b/deny')}"
    )
    notify(rec["provider_id"], msg)
    state.update(rid, "B_NOTIFIED")
    return _page(
        "Request sent",
        f"<h2>✅ Request sent</h2><p class=muted>{rec['provider_name']} has been "
        f"asked to approve {rec['quantity']} {rec['item']}.</p>",
    )


@app.get("/req/{rid}/a/reject")
def a_reject(rid: str):
    rec = state.get(rid)
    if not rec:
        return _page("Not found", "<h2>Request not found</h2>")
    state.update(rid, "A_REJECTED")
    notify(rec["requester_id"], f"No transfer requested for {rec['item']}.")
    return _page("Skipped",
                 "<h2>Request skipped</h2><p class=muted>No transfer requested.</p>")


@app.get("/req/{rid}/b/accept")
def b_accept(rid: str):
    rec = state.get(rid)
    if not rec:
        return _page("Not found", "<h2>Request not found</h2>")
    transfer = {
        "from_hospital": rec["provider_id"],
        "to_hospital": rec["requester_id"],
        "item": rec["item"],
        "quantity": rec["quantity"],
        "eta_minutes": 20,
        "status": "confirmed",
        "settlement_status": "approved_by_doctors",
    }
    try:
        redis_io.log_transfer(transfer)
    except Exception as exc:  # noqa: BLE001
        log.warning("log_transfer failed: %s", exc)
    state.update(rid, "SETTLED")
    notify(rec["requester_id"],
           f"✅ {rec['provider_name']} approved: {rec['quantity']} {rec['item']} "
           f"inbound (ETA ~20 min).")
    notify(rec["provider_id"],
           f"✅ Transfer confirmed: {rec['quantity']} {rec['item']} → "
           f"{rec['requester_name']}.")
    return _page(
        "Approved",
        f"<h2>✅ Transfer approved</h2><p class=muted>{rec['quantity']} "
        f"{rec['item']} → {rec['requester_name']} (ETA ~20 min). Logged to the "
        f"network.</p>",
    )


@app.get("/req/{rid}/b/deny")
def b_deny(rid: str):
    rec = state.get(rid)
    if not rec:
        return _page("Not found", "<h2>Request not found</h2>")
    state.update(rid, "B_DENIED")
    # Hook for the partner's Browserbase "buy" step.
    try:
        redis_io.get_redis().publish(redis_io.EVENTS_CHANNEL, json.dumps({
            "type": "approval_b_denied",
            "request_id": rid,
            "item": rec["item"],
            "quantity": rec["quantity"],
            "requester_id": rec["requester_id"],
            "requester_name": rec["requester_name"],
        }))
    except Exception as exc:  # noqa: BLE001
        log.warning("publish approval_b_denied failed: %s", exc)
    notify(rec["requester_id"],
           f"❌ {rec['provider_name']} can't spare {rec['item']} right now. "
           f"A purchase option will follow.")
    return _page(
        "Denied",
        f"<h2>Request denied</h2><p class=muted>{rec['provider_name']} declined. "
        f"{rec['requester_name']} has been notified.</p>",
    )


@app.get("/", response_class=HTMLResponse)
def index():
    return _page(
        "Stockpile Approval",
        "<h2>Stockpile — Approval Service</h2>"
        "<p class=muted>Human-in-the-loop transfer approvals over Poke "
        "(Hospital A &harr; Hospital B).</p>"
        f"<form method=post action='{BASE_URL}/shortage'>"
        "<button class='btn ok'>Trigger shortage demo</button></form>",
    )
