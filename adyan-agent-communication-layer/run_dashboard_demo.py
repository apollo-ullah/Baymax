"""run_dashboard_demo.py — single-process live demo (all 3 agents + dashboard bus).

For the scan dashboard on ONE MacBook you need Hospital A, B, and C in the same
process (Bureau). run_front.py alone cannot collect offers from B/C — they must be
reachable. This runner wires the same dashboard Redis pollers as run_front.py but
keeps all three agents in-process (no Mailbox / Agentverse required).

Simulated settlement (stub ref) — same as the dashboard path in run_front.py.

Run (with Redis + camera workers + scan_dashboard.py):
    ./.venv/bin/python run_dashboard_demo.py

Or use: ./scripts/single_mac_demo.sh
"""
from __future__ import annotations

import os

os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "30")
os.environ.setdefault("BAYMAX_REDIS", "1")
os.environ.setdefault("BAYMAX_SPARSE_NARRATION", "1")

import agent_base  # noqa: F401
from agent_base import build_hospital_agent
from uagents import Bureau

import baymax_agents as sp
import dashboard_bus
from baymax_agents import attach_front_handlers, attach_hospital_handlers


def build_bureau():
    front = build_hospital_agent("Hospital A")
    hb = build_hospital_agent("Hospital B")
    hc = build_hospital_agent("Hospital C")
    attach_front_handlers(front)
    attach_hospital_handlers(hb, "Hospital B")
    attach_hospital_handlers(hc, "Hospital C")

    sp.register_narration_sink(dashboard_bus.publish_narration)

    @front.on_interval(period=1.0)
    async def _poll_dashboard_trigger(ctx):
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


if __name__ == "__main__":
    bureau, front = build_bureau()
    print("=" * 70)
    print("Baymax LIVE DASHBOARD demo — 3-agent Bureau (A + B + C, one process)")
    print(f"  FRONT address : {front.address}")
    print(f"  BAYMAX_REDIS  : {os.getenv('BAYMAX_REDIS')}")
    print(f"  item default  : {os.getenv('BAYMAX_ITEM', 'Saline')}")
    print("-" * 70)
    print("In other terminals:")
    print("  ./.venv/bin/python camera_worker.py --hospital a")
    print("  ./.venv/bin/python camera_worker.py --hospital b --port 8766")
    print("  ./.venv/bin/python scan_dashboard.py")
    print("=" * 70)
    bureau.run()
