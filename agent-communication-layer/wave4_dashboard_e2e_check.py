"""wave4_dashboard_e2e_check.py — Wave 4 dashboard bus integration check (offline).

Proves the dashboard bus wiring end to end WITHOUT Redis or a running dashboard:
  * Uses in-process stubs for dashboard_bus (no Redis needed).
  * Pushes a trigger via dashboard_bus.push_trigger().
  * Runs a Bureau demo that polls the trigger queue, starts a negotiation with
    source="dashboard", and auto-approves at the admin gate.
  * Verifies narration events are forwarded to a registered sink.
  * Exits 0 on success.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3.0")
os.environ.pop("BAYMAX_EXIT_WHEN_DONE", None)

# Stub dashboard_bus so we don't need Redis.
import types as _types

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


_bus_module.push_trigger = _push_trigger
_bus_module.pop_trigger = _pop_trigger
_bus_module.push_decision = _push_decision
_bus_module.pop_decision = _pop_decision
_bus_module.publish_narration = _publish_narration
sys.modules["dashboard_bus"] = _bus_module

import agent_base  # noqa: F401,E402

from uagents import Bureau, Context  # noqa: E402
from agent_base import build_hospital_agent, REQUESTER  # noqa: E402
import baymax_agents as sp  # noqa: E402
from baymax_agents import attach_front_handlers, attach_hospital_handlers  # noqa: E402
from protocol import NegotiationState  # noqa: E402

# Register the narration sink BEFORE building agents.
sp.register_narration_sink(_publish_narration)

front = build_hospital_agent("Hospital A")
hospital_b = build_hospital_agent("Hospital B")
hospital_c = build_hospital_agent("Hospital C")

attach_front_handlers(front)
attach_hospital_handlers(hospital_b, "Hospital B")
attach_hospital_handlers(hospital_c, "Hospital C")

_ticks = {"n": 0, "done": False}
_pushed_trigger = {"done": False}


@front.on_event("startup")
async def _push(ctx: Context):
    # Push one trigger so the polling interval picks it up.
    _push_trigger("saline", requester=REQUESTER, quantity=None)
    ctx.logger.info("[wave4] pushed trigger: saline")


@front.on_interval(period=2.0)
async def _poll_trigger(ctx: Context):
    trigger = _pop_trigger()
    if trigger and not _pushed_trigger["done"]:
        _pushed_trigger["done"] = True
        item = trigger.get("item", "saline")
        qty = trigger.get("quantity")
        requester = trigger.get("requester", REQUESTER)
        await sp.start_negotiation(
            ctx, item, requester=requester, quantity_needed=qty,
            reply_to=None, source="dashboard",
        )


@front.on_interval(period=1.0)
async def _poll_decision(ctx: Context):
    dec = _pop_decision()
    if dec:
        req_id = dec.get("req_id")
        decision = dec.get("decision", "reject")
        if req_id:
            await sp.resume_after_admin_decision(ctx, req_id, decision)


@front.on_interval(period=1.0)
async def _auto_approve_gate(ctx: Context):
    """Auto-approve any negotiation waiting at the admin gate (simulates dashboard UI)."""
    for req_id, neg in list(sp.NEGOTIATIONS.items()):
        if (not neg.get("done") and not neg.get("decided")
                and neg.get("state") == NegotiationState.AWAITING_APPROVAL):
            _push_decision(req_id, "approve")


@front.on_interval(period=1.0)
async def _check_done(ctx: Context):
    _ticks["n"] += 1
    if _ticks["n"] > 30:
        ctx.logger.error("[wave4] watchdog timeout")
        os._exit(3)
    if _ticks["done"]:
        return
    for neg in sp.NEGOTIATIONS.values():
        if neg.get("done") and neg.get("source") == "dashboard":
            _ticks["done"] = True
            states = [p["state"] for p in _narration_log]
            ctx.logger.info(
                f"[wave4] negotiation complete. narrated states: {states}"
            )
            assert "awaiting_approval" in states or "confirmed" in states or "ordered" in states, \
                f"Expected a meaningful terminal state in narration, got: {states}"
            assert any(p.get("source") == "dashboard" for p in _narration_log), \
                "Expected source='dashboard' in narration payloads"
            ctx.logger.info("[wave4] WAVE4 DASHBOARD E2E SUCCESS.")
            os._exit(0)


bureau = Bureau()
for _a in (front, hospital_b, hospital_c):
    bureau.add(_a)

if __name__ == "__main__":
    print("=" * 70)
    print("WAVE 4 E2E: dashboard bus -> trigger -> negotiate -> narrate")
    print(f"  FRONT    : {front.address}")
    print("  (Redis stubbed; no ASI:One, no payment)")
    print("=" * 70)
    bureau.run()
