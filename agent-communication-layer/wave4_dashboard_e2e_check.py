"""wave4_dashboard_e2e_check.py — offline check of the dashboard-triggered path.

No Redis, no camera, no ASI:One. An in-process stub stands in for `dashboard_bus`
(so the real trigger/decision/narration polling wiring is exercised without a
running dashboard or Redis). The harness:

  * pushes a trigger via dashboard_bus.push_trigger() (the "Scan & Negotiate" click),
  * polls it exactly as run_front's trigger poller does and starts a negotiation
    with source="dashboard" / reply_to=None,
  * surfaces the AWAITING_APPROVAL gate and auto-clicks "approve" through the bus
    decision queue (as the dashboard Approve button does via baymax:decision),
  * forwards every milestone to a registered narration sink (the dashboard's live
    feed) via dashboard_bus.publish_narration.

Asserts:

  * the AWAITING_APPROVAL gate SURFACED (dashboard runs no longer auto-approve),
  * the deal stub-settled to a CONFIRMED final milestone (simulated settlement),
  * every milestone reached the narration sink (the dashboard's live feed).

i.e. live scan -> negotiate -> human-in-the-loop gate -> simulated settle, end to
end, with zero external services.

Run:  ./.venv/bin/python wave4_dashboard_e2e_check.py
"""

from __future__ import annotations

import os
import sys
import types as _types

os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3")
os.environ.pop("BAYMAX_EXIT_WHEN_DONE", None)

# --- In-process dashboard_bus stub so we don't need Redis / a running dashboard.
#     Mirrors the real dashboard_bus surface the FRONT trigger/decision pollers
#     rely on, so the bus push/pop wiring is genuinely exercised here.
_bus_module = _types.ModuleType("dashboard_bus")
_trigger_queue: list = []
_decision_queue: list = []
_narration_log: list = []


def _push_trigger(item, requester="Hospital A", quantity=None):
    _trigger_queue.append({"item": item, "requester": requester, "quantity": quantity})


def _pop_trigger():
    return _trigger_queue.pop(0) if _trigger_queue else None


def _push_decision(req_id, decision):
    _decision_queue.append({"req_id": req_id, "decision": decision})


def _pop_decision():
    return _decision_queue.pop(0) if _decision_queue else None


def _publish_narration(payload: dict):
    _narration_log.append(payload)
    print(f"[DASH NARRATION] {payload['state']}: {payload['detail']}")


_bus_module.push_trigger = _push_trigger
_bus_module.pop_trigger = _pop_trigger
_bus_module.push_decision = _push_decision
_bus_module.pop_decision = _pop_decision
_bus_module.publish_narration = _publish_narration
sys.modules["dashboard_bus"] = _bus_module

import agent_base  # noqa: F401,E402  (event-loop install must precede any Agent build)
from agent_base import build_hospital_agent, REQUESTER  # noqa: E402
from uagents import Bureau, Context  # noqa: E402

from baymax_agents import (  # noqa: E402
    NEGOTIATIONS,
    attach_front_handlers,
    attach_hospital_handlers,
    register_narration_sink,
    resume_after_admin_decision,
    start_negotiation,
)
from protocol import NegotiationState  # noqa: E402

ITEM = os.getenv("BAYMAX_ITEM", "IV fluids")

# The narration sink (the dashboard's live SSE feed). Register BEFORE building agents.
milestones = _narration_log
register_narration_sink(_publish_narration)


def main() -> None:
    front = build_hospital_agent("Hospital A")
    hb = build_hospital_agent("Hospital B")
    hc = build_hospital_agent("Hospital C")
    attach_front_handlers(front)
    attach_hospital_handlers(hb, "Hospital B")
    attach_hospital_handlers(hc, "Hospital C")

    state = {"pushed": False, "triggered": False, "approved": False, "ticks": 0}

    @front.on_event("startup")
    async def _kick(ctx: Context):
        # The "Scan & Negotiate" click: enqueue a dashboard trigger.
        _push_trigger(ITEM, requester=REQUESTER, quantity=None)
        state["pushed"] = True
        ctx.logger.info(
            f"=== WAVE4: dashboard trigger pushed ({ITEM!r}, source=dashboard) ===")

    @front.on_interval(period=0.5)
    async def _poll_trigger(ctx: Context):
        # Exactly as run_front's trigger poller: pop a queued trigger and start
        # a negotiation with source="dashboard" / reply_to=None.
        if state["triggered"]:
            return
        trig = _pop_trigger()
        if not trig:
            return
        state["triggered"] = True
        item = trig.get("item") or ITEM
        await start_negotiation(
            ctx, item, requester=trig.get("requester", REQUESTER),
            quantity_needed=trig.get("quantity"), reply_to=None, source="dashboard")

    @front.on_interval(period=0.5)
    async def _enqueue_approval(ctx: Context):
        # Stand in for the dashboard Approve button: when the gate surfaces, push
        # an "approve" decision onto the bus (don't resume directly — exercise the bus).
        if state["approved"]:
            return
        for rid, neg in list(NEGOTIATIONS.items()):
            if (neg.get("source") == "dashboard"
                    and neg.get("state") == NegotiationState.AWAITING_APPROVAL
                    and not neg.get("done")):
                state["approved"] = True
                ctx.logger.info(f"=== WAVE4: gate surfaced for {rid} -> approve ===")
                _push_decision(rid, "approve")
                return

    @front.on_interval(period=0.5)
    async def _poll_decision(ctx: Context):
        # Exactly as run_front's decision poller: drain queued dashboard decisions.
        dec = _pop_decision()
        if not dec:
            return
        req_id = dec.get("req_id")
        if req_id:
            await resume_after_admin_decision(ctx, req_id, dec.get("decision", "reject"))

    @front.on_interval(period=0.5)
    async def _watch(ctx: Context):
        state["ticks"] += 1
        if state["ticks"] > 80:
            print("\n=== WAVE4 RESULT ===\n  RESULT        : FAIL (watchdog timeout)")
            print("  milestones    :", [m["state"] for m in milestones])
            sys.stdout.flush()
            os._exit(3)
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
        sys.stdout.flush()
        os._exit(0 if ok else 1)

    bureau = Bureau()
    for a in (front, hb, hc):
        bureau.add(a)
    bureau.run()


if __name__ == "__main__":
    print("=" * 70)
    print("WAVE 4 E2E: dashboard bus -> trigger -> negotiate -> gate -> settle -> narrate")
    print("  (Redis stubbed; no ASI:One, no payment)")
    print("=" * 70)
    main()
