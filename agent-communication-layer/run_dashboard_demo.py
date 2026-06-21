"""run_dashboard_demo.py — Bureau demo wired to the scan dashboard bus.

Runs the 3-agent negotiation Bureau in the same process as the scan dashboard,
connecting them via dashboard_bus. Triggers from the dashboard's /scan-and-negotiate
endpoint land on baymax:trigger, the FRONT picks them up, runs the negotiation (with
source="dashboard"), and narrates each milestone back onto baymax:narration so the
dashboard's SSE feed updates in real time.

Admin decisions from /decision land on baymax:decision and are routed to
resume_after_admin_decision().

Run:
    ./.venv/bin/python run_dashboard_demo.py
Then open http://localhost:8080 and click "Scan + Negotiate".
"""

from __future__ import annotations

import asyncio
import os
import threading

# Dashboard env defaults (set BEFORE importing baymax_agents).
os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "4.0")
os.environ.setdefault("BAYMAX_REDIS", "1")
os.environ.setdefault("BAYMAX_SPARSE_NARRATION", "0")  # full narration in dashboard mode

import agent_base  # noqa: F401,E402  (installs Python 3.14 event-loop first)

from uagents import Bureau, Context  # noqa: E402
from agent_base import build_hospital_agent, REQUESTER  # noqa: E402
import baymax_agents as sp  # noqa: E402
from baymax_agents import (  # noqa: E402
    attach_front_handlers,
    attach_hospital_handlers,
)
from protocol import NegotiationState  # noqa: E402
import dashboard_bus  # noqa: E402

# Wire the narration sink FIRST so no events are lost.
sp.register_narration_sink(dashboard_bus.publish_narration)

# Build the 3-agent Bureau.
front = build_hospital_agent("Hospital A")
hospital_b = build_hospital_agent("Hospital B")
hospital_c = build_hospital_agent("Hospital C")

attach_front_handlers(front)
attach_hospital_handlers(hospital_b, "Hospital B")
attach_hospital_handlers(hospital_c, "Hospital C")


@front.on_interval(period=2.0)
async def _poll_trigger(ctx: Context):
    """Pick up scan-and-negotiate triggers from the dashboard."""
    trigger = dashboard_bus.pop_trigger()
    if trigger:
        item = trigger.get("item", "saline")
        qty = trigger.get("quantity")
        requester = trigger.get("requester", REQUESTER)
        ctx.logger.info(f"[dashboard] trigger: {item} qty={qty} requester={requester}")
        await sp.start_negotiation(
            ctx, item, requester=requester, quantity_needed=qty,
            reply_to=None, source="dashboard",
        )


@front.on_interval(period=1.0)
async def _poll_decision(ctx: Context):
    """Pick up admin approve/order/reject decisions from the dashboard."""
    dec = dashboard_bus.pop_decision()
    if dec:
        req_id = dec.get("req_id")
        decision = dec.get("decision", "reject")
        if req_id:
            ctx.logger.info(f"[dashboard] admin decision: {decision} for {req_id}")
            await sp.resume_after_admin_decision(ctx, req_id, decision)


def _start_dashboard():
    """Run the FastAPI dashboard in a daemon thread."""
    try:
        import uvicorn
        from scan_dashboard import app, DASHBOARD_PORT
        uvicorn.run(app, host="0.0.0.0", port=DASHBOARD_PORT, log_level="warning")
    except ImportError as exc:
        print(f"[dashboard] scan_dashboard unavailable ({exc}); skipping UI.")


if __name__ == "__main__":
    # Start the dashboard HTTP server in a daemon thread so it doesn't block the Bureau.
    t = threading.Thread(target=_start_dashboard, daemon=True)
    t.start()

    dashboard_port = int(os.getenv("BAYMAX_DASHBOARD_PORT", "8080"))
    worker_a = os.getenv("BAYMAX_WORKER_A_URL", "http://localhost:8765")
    worker_b = os.getenv("BAYMAX_WORKER_B_URL", "http://localhost:8766")
    print("=" * 70)
    print("Baymax dashboard demo (Bureau + scan dashboard)")
    print(f"  dashboard : http://localhost:{dashboard_port}")
    print(f"  worker A  : {worker_a}")
    print(f"  worker B  : {worker_b}")
    print(f"  FRONT     : {front.address}")
    print("-" * 70)
    print("Open the dashboard URL and click 'Scan + Negotiate' to trigger a demo.")
    print("=" * 70)

    bureau = Bureau()
    bureau.add(front)
    bureau.add(hospital_b)
    bureau.add(hospital_c)
    bureau.run()
