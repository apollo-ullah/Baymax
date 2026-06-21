"""wave6_crisis_dashboard_e2e_check.py — offline check of the dashboard CRISIS path.

The Demo-1 backend wiring, with NO Redis / camera / ASI:One. An in-process stub
stands in for `dashboard_bus` (now including push_crisis/pop_crisis), so the real
crisis-poller wiring in run_dashboard_demo.py / run_front.py is exercised. The
harness:

  * pushes a crisis prompt via dashboard_bus.push_crisis() (the dashboard crisis box),
  * polls it exactly as the runners' _poll_dashboard_crisis does and calls
    start_crisis(..., source="dashboard", reply_to=None),
  * lets the research seam (mock) infer the crisis type + at-risk item, then the
    negotiation runs to the AWAITING_APPROVAL gate,
  * auto-clicks "approve" through the bus decision queue,
  * forwards every milestone (incl. the crisis research milestones) to the
    narration sink (the dashboard's live feed).

Asserts: research milestones surfaced on the sink, the gate surfaced, and the
deal stub-settled to CONFIRMED.

Run:  ./.venv/bin/python wave6_crisis_dashboard_e2e_check.py
"""

from __future__ import annotations

import os
import sys
import types as _types

os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3")
os.environ.pop("BAYMAX_EXIT_WHEN_DONE", None)

# --- In-process dashboard_bus stub (mirrors the real surface incl. crisis). ---
_bus_module = _types.ModuleType("dashboard_bus")
_crisis_queue: list = []
_decision_queue: list = []
_narration_log: list = []


def _push_crisis(crisis_text, requester="Hospital A", region="san_francisco"):
    _crisis_queue.append({"crisis_text": crisis_text, "requester": requester, "region": region})


def _pop_crisis():
    return _crisis_queue.pop(0) if _crisis_queue else None


def _push_decision(req_id, decision):
    _decision_queue.append({"req_id": req_id, "decision": decision})


def _pop_decision():
    return _decision_queue.pop(0) if _decision_queue else None


def _pop_trigger():  # present for parity; unused in this check
    return None


def _publish_narration(payload: dict):
    _narration_log.append(payload)
    print(f"[DASH NARRATION] {payload['state']}: {payload['detail']}")


_bus_module.push_crisis = _push_crisis
_bus_module.pop_crisis = _pop_crisis
_bus_module.push_decision = _push_decision
_bus_module.pop_decision = _pop_decision
_bus_module.pop_trigger = _pop_trigger
_bus_module.publish_narration = _publish_narration
sys.modules["dashboard_bus"] = _bus_module

import agent_base  # noqa: F401,E402
from agent_base import build_hospital_agent  # noqa: E402
from uagents import Bureau, Context  # noqa: E402

from baymax_agents import (  # noqa: E402
    NEGOTIATIONS,
    attach_front_handlers,
    attach_hospital_handlers,
    register_narration_sink,
    resume_after_admin_decision,
    start_crisis,
)
from protocol import NegotiationState  # noqa: E402

CRISIS = os.getenv("BAYMAX_CRISIS_INTENT", "wildfires near Hospital A")
milestones = _narration_log
register_narration_sink(_publish_narration)


def main() -> None:
    front = build_hospital_agent("Hospital A")
    hb = build_hospital_agent("Hospital B")
    hc = build_hospital_agent("Hospital C")
    attach_front_handlers(front)
    attach_hospital_handlers(hb, "Hospital B")
    attach_hospital_handlers(hc, "Hospital C")

    state = {"triggered": False, "approved": False, "ticks": 0}

    @front.on_event("startup")
    async def _kick(ctx: Context):
        _push_crisis(CRISIS, requester="Hospital A")
        ctx.logger.info(f"=== WAVE6: dashboard crisis pushed ({CRISIS!r}) ===")

    @front.on_interval(period=1.0)
    async def _poll_crisis(ctx: Context):
        # Exactly as the runners' _poll_dashboard_crisis.
        if state["triggered"]:
            return
        for neg in NEGOTIATIONS.values():
            if neg.get("reply_to") is None and not neg.get("done"):
                return
        crisis = _pop_crisis()
        if not crisis:
            return
        state["triggered"] = True
        await start_crisis(
            ctx, crisis["crisis_text"], requester=crisis.get("requester", "Hospital A"),
            region=crisis.get("region", "san_francisco"), reply_to=None, source="dashboard")

    @front.on_interval(period=0.5)
    async def _enqueue_approval(ctx: Context):
        if state["approved"]:
            return
        for rid, neg in list(NEGOTIATIONS.items()):
            if (neg.get("source") == "dashboard"
                    and neg.get("state") == NegotiationState.AWAITING_APPROVAL
                    and not neg.get("done")):
                state["approved"] = True
                ctx.logger.info(f"=== WAVE6: gate surfaced for {rid} -> approve ===")
                _push_decision(rid, "approve")
                return

    @front.on_interval(period=0.5)
    async def _poll_decision(ctx: Context):
        dec = _pop_decision()
        if not dec:
            return
        if dec.get("req_id"):
            await resume_after_admin_decision(ctx, dec["req_id"], dec.get("decision", "reject"))

    @front.on_interval(period=0.5)
    async def _watch(ctx: Context):
        state["ticks"] += 1
        states = [m["state"] for m in milestones]
        if state["ticks"] > 90:
            print("\n=== WAVE6 RESULT ===\n  RESULT: FAIL (watchdog timeout)")
            print("  milestones:", states)
            sys.stdout.flush()
            os._exit(3)
        final = next((m for m in milestones if m.get("final")), None)
        if not final:
            return
        research = any(m["state"] in ("researching", "researched") for m in milestones)
        gate = "awaiting_approval" in states
        confirmed = final["state"] == "confirmed"
        ok = research and gate and confirmed
        print("\n=== WAVE6 RESULT ===")
        print("  research milestones :", research)
        print("  gate surfaced       :", gate)
        print("  terminal state      :", final["state"])
        print("  milestones          :", states)
        print("  RESULT              :", "PASS" if ok else "FAIL")
        sys.stdout.flush()
        os._exit(0 if ok else 1)

    bureau = Bureau()
    for a in (front, hb, hc):
        bureau.add(a)
    bureau.run()


if __name__ == "__main__":
    print("=" * 70)
    print("WAVE 6 E2E: dashboard crisis box -> research -> negotiate -> gate -> settle -> narrate")
    print("  (Redis stubbed; no ASI:One, no payment)")
    print("=" * 70)
    main()
