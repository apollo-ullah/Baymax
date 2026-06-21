"""run_dashboard_demo.py — single-process live demo (3 agents + scan dashboard).

For the scan dashboard on ONE MacBook you need Hospital A, B, and C in the same
process (Bureau). run_front.py alone cannot collect offers from B/C — they must be
reachable. This runner wires the same dashboard Redis pollers as run_front.py but
keeps all three agents in-process (no Mailbox / Agentverse required), and also
starts the FastAPI scan dashboard in a daemon thread so a single command brings up
the whole UI.

Triggers from the dashboard's /scan-and-negotiate endpoint land on baymax:trigger,
the FRONT picks them up, runs the negotiation (with source="dashboard"), and
narrates each milestone back onto baymax:narration so the dashboard's SSE feed
updates in real time. Admin decisions from /decide (= /decision) land on
baymax:decision and are routed to resume_after_admin_decision().

Simulated settlement (stub ref) — same as the dashboard path in run_front.py.

Run (with Redis + camera workers):
    ./.venv/bin/python run_dashboard_demo.py
Then open http://localhost:8080 and click "Scan + Negotiate".

Or use: ./scripts/single_mac_demo.sh
"""
from __future__ import annotations

import os
import threading

# Dashboard env defaults (set BEFORE importing baymax_agents).
os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "30")
os.environ.setdefault("BAYMAX_REDIS", "1")
os.environ.setdefault("BAYMAX_SPARSE_NARRATION", "0")  # full narration in dashboard mode

import agent_base  # noqa: F401,E402  (installs the Python 3.14 event loop first)
from agent_base import build_hospital_agent  # noqa: E402
from uagents import Bureau  # noqa: E402

import baymax_agents as sp  # noqa: E402
import dashboard_bus  # noqa: E402
from baymax_agents import attach_front_handlers, attach_hospital_handlers  # noqa: E402


def build_bureau():
    front = build_hospital_agent("Hospital A")
    hb = build_hospital_agent("Hospital B")
    hc = build_hospital_agent("Hospital C")
    attach_front_handlers(front)
    attach_hospital_handlers(hb, "Hospital B")
    attach_hospital_handlers(hc, "Hospital C")

    # Wire the narration sink so each milestone reaches the dashboard SSE.
    sp.register_narration_sink(dashboard_bus.publish_narration)

    @front.on_interval(period=1.0)
    async def _poll_dashboard_trigger(ctx):
        # Don't start a dashboard negotiation while a chat-driven one
        # (reply_to is None means dashboard; a non-None reply_to is a chat user)
        # is still in flight — one negotiation at a time keeps the demo legible.
        for neg in sp.NEGOTIATIONS.values():
            if neg.get("reply_to") is None and not neg.get("done"):
                return
        trig = dashboard_bus.pop_trigger()
        if not trig:
            return
        item = trig.get("item") or os.getenv("BAYMAX_ITEM", "Saline")
        requester = trig.get("requester") or "Hospital A"
        qty = trig.get("quantity")
        ctx.logger.info(
            f"dashboard trigger -> start_negotiation({item!r}, qty={qty}, source=dashboard)")
        await sp.start_negotiation(
            ctx, item, requester=requester, quantity_needed=qty,
            reply_to=None, source="dashboard")

    @front.on_interval(period=0.5)
    async def _poll_dashboard_decision(ctx):
        dec = dashboard_bus.pop_decision()
        if not dec:
            return
        decision = dec.get("decision")
        if decision not in ("approve", "order", "reject"):
            return
        req_id = dec.get("req_id")
        if not req_id:
            # No req_id given: target the latest dashboard negotiation that is
            # waiting on an admin decision.
            req_id = next(
                (rid for rid, n in sp.NEGOTIATIONS.items()
                 if n.get("source") == "dashboard" and not n.get("done")
                 and n.get("state") == sp.NegotiationState.AWAITING_APPROVAL),
                None)
        if not req_id:
            return
        ctx.logger.info(f"dashboard decision: {req_id} -> {decision}")
        await sp.resume_after_admin_decision(ctx, req_id, decision)

    bureau = Bureau()
    for a in (front, hb, hc):
        bureau.add(a)
    return bureau, front


def _start_dashboard():
    """Run the FastAPI scan dashboard in a daemon thread (non-blocking)."""
    try:
        import uvicorn
        from scan_dashboard import app, DASHBOARD_PORT
        uvicorn.run(app, host="0.0.0.0", port=DASHBOARD_PORT, log_level="warning")
    except ImportError as exc:
        print(f"[dashboard] scan_dashboard unavailable ({exc}); skipping UI. "
              "Run scan_dashboard.py in another terminal instead.")


if __name__ == "__main__":
    bureau, front = build_bureau()

    # Start the dashboard HTTP server in a daemon thread so it doesn't block the Bureau.
    t = threading.Thread(target=_start_dashboard, daemon=True)
    t.start()

    dashboard_port = int(os.getenv("DASHBOARD_PORT") or os.getenv("BAYMAX_DASHBOARD_PORT", "8080"))
    worker_a = os.getenv("WORKER_A_URL") or os.getenv("BAYMAX_WORKER_A_URL", "http://localhost:8765")
    worker_b = os.getenv("WORKER_B_URL") or os.getenv("BAYMAX_WORKER_B_URL", "http://localhost:8766")
    print("=" * 70)
    print("Baymax LIVE DASHBOARD demo — 3-agent Bureau (A + B + C, one process)")
    print(f"  dashboard     : http://localhost:{dashboard_port}")
    print(f"  worker A      : {worker_a}")
    print(f"  worker B      : {worker_b}")
    print(f"  FRONT address : {front.address}")
    print(f"  BAYMAX_REDIS  : {os.getenv('BAYMAX_REDIS')}")
    print(f"  item default  : {os.getenv('BAYMAX_ITEM', 'Saline')}")
    print("-" * 70)
    print("In other terminals (camera workers):")
    print("  ./.venv/bin/python camera_worker.py --hospital a")
    print("  ./.venv/bin/python camera_worker.py --hospital b --port 8766")
    print("Then open the dashboard URL and click 'Scan + Negotiate'.")
    print("=" * 70)

    bureau.run()
