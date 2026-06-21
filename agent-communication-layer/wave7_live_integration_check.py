"""wave7_live_integration_check.py — offline E2E harness for the live integration path.

Verifies the full camera(mock) -> mock-inventory -> negotiate -> settle(fallback/simulated)
-> narration chain with ZERO external services (no Redis, no ASI:One, no real FET payment).

Also covers C3.1/C3.2/C3.3: registers the direct settlement path so settle_via_direct_transfer
runs (in simulated mode since wallets are unfunded offline). The settlement ref must be a
"simulated-..." or "stub-..." string (not a real tx hash).

Pass 1 — "approve" branch:
  Scenario: "IV fluids" split (Hospital A short, B + C each contribute part).
  Expected milestone states include: shortfall_detected, evaluating, confirmed.
  AWAITING_APPROVAL gate is surfaced (source='dashboard') and auto-approved.

Pass 2 — "order" branch (mock external supplier):
  Scenario: same IV fluids shortfall, but admin chooses "order" at the gate.
  Expected milestone states include: shortfall_detected, ordering, ordered.
  NEGOTIATIONS[rid]["order"].confirmation_ref must start "MOCK-PO-".
  BAYMAX_BROWSERBASE is unset -> deterministic mock order_from_supplier().

Both passes must PASS for exit 0.

Exit codes:
  0 = PASS (both passes)
  1 = FAIL (assertion failed in either pass)
  3 = watchdog timeout

Run:  python wave7_live_integration_check.py
"""

from __future__ import annotations

import os
import sys
import types as _types

# --- Set ALL env vars BEFORE importing agent_base ---
# Use os.environ[] (not setdefault) so these survive load_dotenv() in agent_base,
# which can override setdefault values from a .env file (e.g. BAYMAX_ITEM=Saline).
os.environ["BAYMAX_REDIS"] = "0"           # mock inventory, no Redis dependency
os.environ["BAYMAX_DEMO_MODE"] = "1"       # small demo quantities
os.environ["BAYMAX_OFFER_TIMEOUT"] = "3"   # fast offer window for offline test
os.environ["BAYMAX_SPARSE_NARRATION"] = "0"  # see all milestones
os.environ["BAYMAX_ITEM"] = "IV fluids"    # split scenario (B + C each contribute)
# Set a large dashboard approval timeout so our explicit gate handler fires before
# the auto-approve watchdog in baymax_agents triggers (especially for pass 2 "order").
os.environ["BAYMAX_DASHBOARD_APPROVAL_TIMEOUT"] = "120"
os.environ.pop("BAYMAX_EXIT_WHEN_DONE", None)  # harness owns exit
os.environ.pop("BAYMAX_BROWSERBASE", None)     # force deterministic mock order

# --- In-process dashboard_bus stub (mirrors the real surface) ---
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


def _pop_crisis():
    return None


def _publish_narration(payload: dict):
    _narration_log.append(payload)
    print(f"[NARRATION] {payload['state']}: {payload['detail']}")


_bus_module.push_trigger = _push_trigger
_bus_module.pop_trigger = _pop_trigger
_bus_module.push_crisis = lambda *a, **kw: None
_bus_module.pop_crisis = _pop_crisis
_bus_module.push_decision = _push_decision
_bus_module.pop_decision = _pop_decision
_bus_module.publish_narration = _publish_narration
sys.modules["dashboard_bus"] = _bus_module

# --- import agent_base FIRST (installs Python 3.14 event loop) ---
import agent_base  # noqa: F401,E402
from agent_base import build_chat_protocol, build_hospital_agent, create_text_chat, REQUESTER  # noqa: E402
from uagents import Bureau, Context  # noqa: E402

import settlement  # noqa: E402
import baymax_agents as sp  # noqa: E402
from baymax_agents import (  # noqa: E402
    NEGOTIATIONS,
    attach_front_handlers,
    attach_hospital_handlers,
    register_narration_sink,
    resume_after_admin_decision,
    start_negotiation,
)
from protocol import NegotiationState  # noqa: E402
from front_agent import on_intent  # noqa: E402
from interfaces import SupplierOrder  # noqa: E402

ITEM = os.getenv("BAYMAX_ITEM", "IV fluids")

# The narration sink (append every payload and print it).
milestones: list[dict] = _narration_log
register_narration_sink(_publish_narration)


def main() -> None:
    front = build_hospital_agent("Hospital A")
    hb = build_hospital_agent("Hospital B")
    hc = build_hospital_agent("Hospital C")

    # --- C3 coverage: wire the direct settlement path (simulated mode offline) ---
    settlement.register_payer_wallet(front)
    settlement.register_recipient_wallet(hb, "Hospital B")
    settlement.register_recipient_wallet(hc, "Hospital C")
    sp.register_direct_settlement_hook(settlement.settle_via_direct_transfer)

    attach_front_handlers(front)
    attach_hospital_handlers(hb, "Hospital B")
    attach_hospital_handlers(hc, "Hospital C")
    front.include(build_chat_protocol(on_intent), publish_manifest=True)

    # State machine for two sequential passes inside the same Bureau.
    # pass_num: 1 = approve branch, 2 = order branch
    state = {
        "pass_num": 1,
        "triggered_p1": False,
        "triggered_p2": False,
        "decided_p1": False,
        "decided_p2": False,
        "rid_p1": None,
        "rid_p2": None,
        "milestones_p1": [],   # snapshot when pass 1 finishes
        "pass1_ok": None,      # True/False once pass 1 is assessed
        "ticks": 0,
    }

    # Filters milestones to only those belonging to a specific req_id.
    def _milestones_for(rid: str) -> list[dict]:
        return [m for m in milestones if m.get("req_id") == rid]

    @front.on_event("startup")
    async def _kick(ctx: Context):
        """Start pass 1 negotiation at startup."""
        ctx.logger.info(f"=== WAVE7 PASS 1: starting negotiation for {ITEM!r} (source=dashboard, branch=approve) ===")
        rid = await start_negotiation(
            ctx, ITEM, requester=REQUESTER, quantity_needed=None,
            reply_to=None, source="dashboard",
        )
        state["rid_p1"] = rid
        state["triggered_p1"] = True

    @front.on_interval(period=0.5)
    async def _gate_handler(ctx: Context):
        """Auto-drive decisions at the AWAITING_APPROVAL gate for each pass."""
        current_pass = state["pass_num"]

        # --- Pass 1: approve branch ---
        if current_pass == 1 and not state["decided_p1"]:
            for rid, neg in list(NEGOTIATIONS.items()):
                if (
                    neg.get("state") == NegotiationState.AWAITING_APPROVAL
                    and not neg.get("done")
                    and not neg.get("decided")
                    and rid == state.get("rid_p1")
                ):
                    state["decided_p1"] = True
                    ctx.logger.info(f"=== WAVE7 PASS 1: gate -> approve (rid={rid}) ===")
                    await resume_after_admin_decision(ctx, rid, "approve")
                    return

        # --- Pass 2: order branch ---
        if current_pass == 2 and not state["decided_p2"]:
            for rid, neg in list(NEGOTIATIONS.items()):
                if (
                    neg.get("state") == NegotiationState.AWAITING_APPROVAL
                    and not neg.get("done")
                    and not neg.get("decided")
                    and rid == state.get("rid_p2")
                ):
                    state["decided_p2"] = True
                    ctx.logger.info(f"=== WAVE7 PASS 2: gate -> order (rid={rid}) ===")
                    await resume_after_admin_decision(ctx, rid, "order")
                    return

    @front.on_interval(period=0.5)
    async def _watch(ctx: Context):
        """Watchdog + milestone asserter for both passes."""
        state["ticks"] += 1

        # Tick cap: 180 * 0.5s = 90s max (two passes, 45s each)
        if state["ticks"] > 180:
            seen_states_p1 = [m["state"] for m in state["milestones_p1"]]
            seen_states_now = [m["state"] for m in milestones]
            print("\n=== WAVE7 RESULT ===")
            print("  RESULT: FAIL (watchdog timeout)")
            print("  pass1 milestones:", seen_states_p1)
            print("  all milestones  :", seen_states_now)
            print(f"  current pass    : {state['pass_num']}")
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(3)

        current_pass = state["pass_num"]

        # --- Pass 1 assessment ---
        if current_pass == 1 and state.get("rid_p1"):
            rid_p1 = state["rid_p1"]
            neg_p1 = NEGOTIATIONS.get(rid_p1, {})
            p1_milestones = _milestones_for(rid_p1)
            p1_final = next((m for m in p1_milestones if m.get("final")), None)
            if not p1_final:
                return

            seen_p1 = {m["state"] for m in p1_milestones}
            required_p1 = {"shortfall_detected", "evaluating", "confirmed"}
            gate_p1 = "awaiting_approval" in seen_p1
            missing_p1 = required_p1 - seen_p1
            terminal_p1_ok = p1_final["state"] == "confirmed"

            confirmed_detail_p1 = p1_final.get("detail", "")
            settlement_invoked = (
                "Settlement:" in confirmed_detail_p1
                or "simulated-" in confirmed_detail_p1
                or "stub-settlement" in confirmed_detail_p1
            )

            pass1_ok = (
                not missing_p1
                and terminal_p1_ok
                and gate_p1
                and settlement_invoked
            )
            state["pass1_ok"] = pass1_ok

            settlement_ref_p1 = None
            if "Settlement:" in confirmed_detail_p1:
                settlement_ref_p1 = confirmed_detail_p1.split("Settlement:")[-1].strip().rstrip(".")
            elif "simulated-" in confirmed_detail_p1:
                settlement_ref_p1 = "[simulated]"
            elif "stub-settlement" in confirmed_detail_p1:
                settlement_ref_p1 = "[stub]"

            print("\n=== WAVE7 PASS 1 RESULT (approve branch) ===")
            print("  required milestones:", required_p1 - missing_p1 if not missing_p1 else f"MISSING {missing_p1}")
            print("  gate (awaiting_approval):", gate_p1)
            print("  terminal state          :", p1_final["state"])
            print("  settlement hook invoked :", settlement_invoked)
            print("  settlement ref          :", settlement_ref_p1)
            print("  all milestone states    :", sorted(seen_p1))
            print("  PASS 1 RESULT           :", "PASS" if pass1_ok else "FAIL")

            if not pass1_ok:
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(1)

            # Transition to pass 2: snapshot pass 1 milestones, start pass 2 negotiation.
            state["milestones_p1"] = list(p1_milestones)
            state["pass_num"] = 2

            print("\n=== WAVE7 PASS 2: starting negotiation for order branch ===")
            sys.stdout.flush()

            # Start a fresh negotiation for pass 2 (order branch).
            rid_p2 = await start_negotiation(
                ctx, ITEM, requester=REQUESTER, quantity_needed=None,
                reply_to=None, source="dashboard",
            )
            state["rid_p2"] = rid_p2
            ctx.logger.info(f"=== WAVE7 PASS 2: started rid={rid_p2} (branch=order) ===")
            return

        # --- Pass 2 assessment ---
        if current_pass == 2 and state.get("rid_p2"):
            rid_p2 = state["rid_p2"]
            p2_milestones = _milestones_for(rid_p2)
            p2_final = next((m for m in p2_milestones if m.get("final")), None)
            if not p2_final:
                return

            seen_p2 = {m["state"] for m in p2_milestones}
            # Pass 2 required states: ordering and ordered must both appear.
            required_p2 = {"shortfall_detected", "ordering", "ordered"}
            missing_p2 = required_p2 - seen_p2
            terminal_p2_ok = p2_final["state"] == "ordered"

            # Assert NEGOTIATIONS[rid_p2]["order"] is a SupplierOrder with MOCK-PO- ref.
            neg_p2 = NEGOTIATIONS.get(rid_p2, {})
            order_obj = neg_p2.get("order")
            supplier_order_ok = (
                order_obj is not None
                and isinstance(order_obj, SupplierOrder)
                and isinstance(order_obj.confirmation_ref, str)
                and order_obj.confirmation_ref.startswith("MOCK-PO-")
            )

            pass2_ok = (
                not missing_p2
                and terminal_p2_ok
                and supplier_order_ok
            )

            confirmation_ref = getattr(order_obj, "confirmation_ref", None) if order_obj else None

            print("\n=== WAVE7 PASS 2 RESULT (order branch) ===")
            print("  required milestones:", required_p2 - missing_p2 if not missing_p2 else f"MISSING {missing_p2}")
            print("  terminal state     :", p2_final["state"])
            print("  SupplierOrder type :", type(order_obj).__name__ if order_obj else "None")
            print("  confirmation_ref   :", confirmation_ref)
            print("  starts MOCK-PO-    :", supplier_order_ok)
            print("  all milestone states:", sorted(seen_p2))
            print("  PASS 2 RESULT      :", "PASS" if pass2_ok else "FAIL")

            # Final overall result
            overall_ok = state["pass1_ok"] and pass2_ok
            print("\n=== WAVE7 OVERALL RESULT ===")
            print("  Pass 1 (approve):", "PASS" if state['pass1_ok'] else "FAIL")
            print("  Pass 2 (order)  :", "PASS" if pass2_ok else "FAIL")
            print("  RESULT          :", "PASS" if overall_ok else "FAIL")
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0 if overall_ok else 1)

    bureau = Bureau()
    for a in (front, hb, hc):
        bureau.add(a)
    bureau.run()


if __name__ == "__main__":
    print("=" * 70)
    print("WAVE 7 E2E: camera(mock)->inventory(mock)->negotiate->settle(simulated)->narrate")
    print("  Pass 1 (approve branch): shortfall_detected, evaluating, awaiting_approval, confirmed")
    print("  Pass 2 (order branch) : shortfall_detected, ordering, ordered + MOCK-PO- ref")
    print("  C3 coverage: direct settlement path (simulated offline)")
    print("  (No Redis, no ASI:One, no real FET payment, no Browserbase)")
    print("=" * 70)
    main()
