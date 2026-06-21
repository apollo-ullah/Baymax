"""wave5_handshake_e2e_check.py — offline e2e for the two-tap A→B handshake.

Proves that with BAYMAX_PROVIDER_APPROVAL=1 the real agent negotiation requires
TWO human taps before it settles:

  tap 1  requester (Hospital A) admin approves the composed plan  (AWAITING_APPROVAL)
  tap 2  each provider (Hospital B/C) doctor approves the release (release_pending)

The negotiation must NOT settle until BOTH the requester gate and every provider
leg has been approved. Uses an in-memory dashboard_bus (no Redis) and simulates
the taps from the narration sink. Self-exits 0 on success, non-zero on failure.

Run:  ./.venv/bin/python wave5_handshake_e2e_check.py
"""

from __future__ import annotations

import os
import sys
import types

# Enable the handshake + deterministic mock inventory BEFORE importing the core.
os.environ["BAYMAX_PROVIDER_APPROVAL"] = "1"
os.environ.setdefault("BAYMAX_REDIS", "0")
os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "2.0")

# ── In-memory dashboard_bus (no Redis) ──────────────────────────────────────
_bus_module = types.ModuleType("dashboard_bus")
_trigger_queue: list = []
_decision_queue: list = []
_release_queues: dict = {}        # facility -> [ {pid, decision} ]
_narration_log: list = []


def _push_trigger(item, requester=None, quantity=None):
    _trigger_queue.append({"item": item, "requester": requester, "quantity": quantity})


def _pop_trigger():
    return _trigger_queue.pop(0) if _trigger_queue else None


def _push_decision(req_id, decision):
    _decision_queue.append({"req_id": req_id, "decision": decision})


def _pop_decision():
    return _decision_queue.pop(0) if _decision_queue else None


def _push_release_decision(facility, pid, decision):
    _release_queues.setdefault(facility, []).append({"pid": pid, "decision": decision})


def _pop_release_decision(facility):
    q = _release_queues.get(facility)
    return q.pop(0) if q else None


# tap simulators — fire as the sink sees each gate event
_taps = {"requester": 0, "release": 0}


def _publish_narration(payload: dict):
    _narration_log.append(payload)
    st = payload.get("state")
    if st == "awaiting_approval" and payload.get("req_id"):
        _taps["requester"] += 1
        _push_decision(payload["req_id"], "approve")          # tap 1
    elif st == "release_pending":
        _taps["release"] += 1
        _push_release_decision(payload.get("facility"), payload.get("pid"), "approve")  # tap 2


_bus_module.push_trigger = _push_trigger
_bus_module.pop_trigger = _pop_trigger
_bus_module.push_decision = _push_decision
_bus_module.pop_decision = _pop_decision
_bus_module.push_release_decision = _push_release_decision
_bus_module.pop_release_decision = _pop_release_decision
_bus_module.publish_narration = _publish_narration
sys.modules["dashboard_bus"] = _bus_module

import agent_base  # noqa: F401,E402

from uagents import Bureau, Context  # noqa: E402
from agent_base import build_hospital_agent, REQUESTER  # noqa: E402
import baymax_agents as sp  # noqa: E402
from baymax_agents import attach_front_handlers, attach_hospital_handlers  # noqa: E402
from protocol import NegotiationState  # noqa: E402

sp.register_narration_sink(_publish_narration)

front = build_hospital_agent("Hospital A")
hospital_b = build_hospital_agent("Hospital B")
hospital_c = build_hospital_agent("Hospital C")

attach_front_handlers(front)
attach_hospital_handlers(hospital_b, "Hospital B")
attach_hospital_handlers(hospital_c, "Hospital C")

_state = {"ticks": 0, "done": False, "started": False}


@front.on_event("startup")
async def _kick(ctx: Context):
    # IV fluids need=200 → split 150 (B) + 50 (C) → two provider release gates.
    _push_trigger("IV fluids", requester=REQUESTER, quantity=200)
    ctx.logger.info("[wave5] pushed trigger: IV fluids need=200 (expect split B+C)")


@front.on_interval(period=1.0)
async def _poll_trigger(ctx: Context):
    if _state["started"]:
        return
    trigger = _pop_trigger()
    if trigger:
        _state["started"] = True
        await sp.start_negotiation(ctx, trigger["item"], requester=trigger["requester"],
                                   quantity_needed=trigger["quantity"], reply_to=None,
                                   source="dashboard")


@front.on_interval(period=0.5)
async def _poll_decision(ctx: Context):
    dec = _pop_decision()
    if dec and dec.get("req_id"):
        await sp.resume_after_admin_decision(ctx, dec["req_id"], dec.get("decision", "reject"))


@front.on_interval(period=1.0)
async def _check_done(ctx: Context):
    _state["ticks"] += 1
    if _state["ticks"] > 40:
        print("FAIL: watchdog timeout — negotiation never completed")
        print("  states seen:", [p.get("state") for p in _narration_log])
        sys.stdout.flush()
        os._exit(3)
    if _state["done"]:
        return
    for neg in sp.NEGOTIATIONS.values():
        if neg.get("done") and neg.get("source") == "dashboard":
            _state["done"] = True
            states = [p.get("state") for p in _narration_log]
            ok = True

            def check(cond, label):
                nonlocal ok
                print(("  ok  " if cond else "  FAIL") + " " + label)
                ok = ok and cond

            print("--- two-tap handshake assertions ---")
            check("awaiting_approval" in states, "tap 1: requester gate (AWAITING_APPROVAL) fired")
            check(_taps["requester"] == 1, f"requester approved exactly once (got {_taps['requester']})")
            check(states.count("release_pending") == 2, "tap 2: BOTH providers held for release "
                  f"(got {states.count('release_pending')})")
            check(_taps["release"] == 2, f"both releases approved (got {_taps['release']})")
            # ordering: every release_pending comes AFTER the requester approval
            ai = states.index("awaiting_approval") if "awaiting_approval" in states else 1e9
            rps = [i for i, s in enumerate(states) if s == "release_pending"]
            check(all(i > ai for i in rps), "providers held only AFTER requester approved")
            check(states[-1] == "confirmed", f"settled (final state {states[-1]})")

            print("RESULT:", "PASS ✅" if ok else "FAIL ❌")
            sys.stdout.flush()
            os._exit(0 if ok else 1)


if __name__ == "__main__":
    bureau = Bureau()
    bureau.add(front)
    bureau.add(hospital_b)
    bureau.add(hospital_c)
    bureau.run()
