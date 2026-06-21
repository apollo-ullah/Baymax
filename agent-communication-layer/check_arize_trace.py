"""check_arize_trace.py — offline check of the Wave-C2 observability hook.

Verifies, with NO Phoenix/OTel collector and NO Redis:
  PART A (plumbing):
    * baymax_agents.register_trace_hook / _trace fire the registered emitter,
    * arize_hook.emit_trace writes a JSON trace artifact and never raises when
      Phoenix/OTel is unavailable,
    * the new trace-name constants exist in arize/src/trace_schema.py.
  PART B (call sites): drives a crisis flow in a Bureau with a hook that BOTH
    captures events and runs the real arize_hook.emit_trace, then asserts the full
    chain fired:  crisis_research -> inventory_low -> reasoning_decision ->
    transfer_recommendation -> decision_outcome.

Run:  python check_arize_trace.py    (prints ARIZE TRACE CHECK SUCCESS, exit 0)
"""

from __future__ import annotations

import glob
import os
import sys
import types as _types

os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3")
os.environ.pop("BAYMAX_EXIT_WHEN_DONE", None)

# Stub dashboard_bus (Part B uses the crisis poller path; no Redis).
_bus = _types.ModuleType("dashboard_bus")
_crisisq, _decq = [], []
_bus.push_crisis = lambda t, requester="Hospital A", region="san_francisco": _crisisq.append(
    {"crisis_text": t, "requester": requester, "region": region})
_bus.pop_crisis = lambda: _crisisq.pop(0) if _crisisq else None
_bus.push_decision = lambda rid, d: _decq.append({"req_id": rid, "decision": d})
_bus.pop_decision = lambda: _decq.pop(0) if _decq else None
_bus.pop_trigger = lambda: None
_bus.publish_narration = lambda payload: None
sys.modules["dashboard_bus"] = _bus

import agent_base  # noqa: F401,E402
from agent_base import build_hospital_agent  # noqa: E402
from uagents import Bureau, Context  # noqa: E402

import baymax_agents as sp  # noqa: E402
import arize_hook  # noqa: E402
from baymax_agents import (  # noqa: E402
    attach_front_handlers, attach_hospital_handlers, resume_after_admin_decision, start_crisis,
)
from protocol import NegotiationState  # noqa: E402

import trace_store  # noqa: E402  (on sys.path via arize_hook)
import trace_schema as tsch  # noqa: E402


def _fail(msg):
    print("ARIZE TRACE CHECK FAILURE: " + msg)
    sys.stdout.flush()
    os._exit(1)


# ── PART A — plumbing (no Bureau) ──────────────────────────────────────────
print("== PART A: hook plumbing ==")
_probe = []
sp.register_trace_hook(lambda ev, attrs: _probe.append((ev, attrs)))
sp._trace("inventory_low", req_id="probe", item="saline")
assert _probe and _probe[0][0] == "inventory_low", "register_trace_hook/_trace did not fire"
print("  ok  - _trace fires the registered hook")

_before = set(glob.glob(str(trace_store.TRACES_DIR / "*.json")))
arize_hook.emit_trace("crisis_research",
                      {"req_id": "probe", "crisis_type": "wildfire", "at_risk": ["saline"]})
_after = set(glob.glob(str(trace_store.TRACES_DIR / "*.json")))
assert len(_after) > len(_before), "arize_hook.emit_trace wrote no JSON artifact"
print("  ok  - arize_hook.emit_trace wrote a JSON artifact (no Phoenix needed)")

assert tsch.TRACE_CRISIS_RESEARCH == "crisis_research"
assert tsch.TRACE_SUPPLIER_ORDER == "supplier_order"
print("  ok  - new trace-name constants present")

# ── PART B — call sites fire during a real crisis flow ─────────────────────
print("== PART B: crisis flow trace chain ==")
captured: list = []


def _combined(ev, attrs):
    captured.append((ev, attrs))
    arize_hook.emit_trace(ev, attrs)   # exercise the REAL emitter too


sp.register_trace_hook(_combined)

front = build_hospital_agent("Hospital A")
hb = build_hospital_agent("Hospital B")
hc = build_hospital_agent("Hospital C")
attach_front_handlers(front)
attach_hospital_handlers(hb, "Hospital B")
attach_hospital_handlers(hc, "Hospital C")

_state = {"triggered": False, "approved": False, "ticks": 0}
EXPECTED = {"crisis_research", "inventory_low", "reasoning_decision",
            "transfer_recommendation", "decision_outcome"}


@front.on_event("startup")
async def _kick(ctx: Context):
    await start_crisis(ctx, "wildfires near Hospital A", requester="Hospital A",
                       region="san_francisco", reply_to=None, source="dashboard")


@front.on_interval(period=0.5)
async def _approve(ctx: Context):
    if _state["approved"]:
        return
    for rid, neg in list(sp.NEGOTIATIONS.items()):
        if (neg.get("source") == "dashboard"
                and neg.get("state") == NegotiationState.AWAITING_APPROVAL
                and not neg.get("done")):
            _state["approved"] = True
            await resume_after_admin_decision(ctx, rid, "approve")
            return


@front.on_interval(period=0.5)
async def _watch(ctx: Context):
    _state["ticks"] += 1
    seen = {ev for ev, _ in captured}
    if EXPECTED.issubset(seen):
        artifacts = len(glob.glob(str(trace_store.TRACES_DIR / "*.json")))
        print("  events captured :", sorted(seen))
        print("  json artifacts  :", artifacts)
        print("ARIZE TRACE CHECK SUCCESS: full crisis trace chain fired + artifacts written")
        sys.stdout.flush()
        os._exit(0)
    if _state["ticks"] > 90:
        _fail(f"timeout; captured only {sorted(seen)} (missing {sorted(EXPECTED - seen)})")


if __name__ == "__main__":
    bureau = Bureau()
    for a in (front, hb, hc):
        bureau.add(a)
    bureau.run()
