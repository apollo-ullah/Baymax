"""wave4_dashboard_e2e_check.py — offline check of the dashboard-triggered path.

No Redis, no camera, no ASI:One. Builds a 3-agent Bureau (FRONT + surplus B/C),
registers a narration sink (stands in for the dashboard SSE), starts a negotiation
with source="dashboard" / reply_to=None (exactly as run_front's trigger poller
does), and auto-clicks "approve" when the in-page gate surfaces (as the dashboard
Approve button does via baymax:decision). Asserts:

  * the AWAITING_APPROVAL gate SURFACED (dashboard runs no longer auto-approve),
  * the deal stub-settled to a CONFIRMED final milestone (simulated settlement),
  * every milestone reached the narration sink (the dashboard's live feed).

i.e. live scan -> negotiate -> human-in-the-loop gate -> simulated settle, end to
end, with zero external services.

Run:  ./.venv/bin/python wave4_dashboard_e2e_check.py
"""
from __future__ import annotations

import os

import agent_base  # noqa: F401  (event-loop install must precede any Agent build)
from agent_base import build_hospital_agent
from uagents import Bureau, Context

from baymax_agents import (
    NEGOTIATIONS,
    attach_front_handlers,
    attach_hospital_handlers,
    register_narration_sink,
    resume_after_admin_decision,
    start_negotiation,
)
from protocol import NegotiationState

os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3")

ITEM = os.getenv("BAYMAX_ITEM", "IV fluids")

milestones: list[dict] = []


def main() -> None:
    front = build_hospital_agent("Hospital A")
    hb = build_hospital_agent("Hospital B")
    hc = build_hospital_agent("Hospital C")
    attach_front_handlers(front)
    attach_hospital_handlers(hb, "Hospital B")
    attach_hospital_handlers(hc, "Hospital C")

    def sink(payload: dict) -> None:
        milestones.append(payload)
        print(f"[DASH NARRATION] {payload['state']}: {payload['detail']}")
    register_narration_sink(sink)

    approved = {"done": False}

    @front.on_event("startup")
    async def _kick(ctx: Context):
        ctx.logger.info(
            f"=== WAVE4: dashboard trigger start_negotiation({ITEM!r}, source=dashboard) ===")
        await start_negotiation(ctx, ITEM, requester="Hospital A",
                                quantity_needed=None, reply_to=None, source="dashboard")

    @front.on_interval(period=0.5)
    async def _auto_approve(ctx: Context):
        # Stand in for the dashboard Approve button: when the gate surfaces, decide.
        if approved["done"]:
            return
        for rid, neg in list(NEGOTIATIONS.items()):
            if (neg.get("source") == "dashboard"
                    and neg.get("state") == NegotiationState.AWAITING_APPROVAL
                    and not neg.get("done")):
                approved["done"] = True
                ctx.logger.info(f"=== WAVE4: gate surfaced for {rid} -> approve ===")
                await resume_after_admin_decision(ctx, rid, "approve")
                return

    @front.on_interval(period=0.5)
    async def _watch(ctx: Context):
        final = next((m for m in milestones if m.get("final")), None)
        if not final:
            return
        gate = any(m["state"] == "awaiting_approval" for m in milestones)
        confirmed = final["state"] == "confirmed"
        ok = gate and confirmed
        print("\n=== WAVE4 RESULT ===")
        print("  gate surfaced :", gate)
        print("  terminal state:", final["state"])
        print("  milestones    :", [m["state"] for m in milestones])
        print("  RESULT        :", "PASS" if ok else "FAIL")
        import sys
        sys.stdout.flush()
        os._exit(0 if ok else 1)

    bureau = Bureau()
    for a in (front, hb, hc):
        bureau.add(a)
    bureau.run()


if __name__ == "__main__":
    main()
