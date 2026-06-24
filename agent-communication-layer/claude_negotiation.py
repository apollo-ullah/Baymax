"""claude_negotiation.py — the FRONT/coordinator agent + orchestration.

The Anthropic multi-agent rewrite of the old uAgents `baymax_agents.py`. There is
NO uAgents, NO cosmpy, NO Bureau, NO message passing here: the FRONT/coordinator
is a plain async orchestrator that calls the surplus-facility Claude agents
(`hospital_agent.decide_offer`) directly, in-process, and concurrently.

Flow (PRD §10, unchanged vocabulary):

    crisis -> FRONT researches (interfaces.research_crisis, Claude)
           -> derive the shortfall item from Redis/vision inventory
           -> ask Hospital B agent + Hospital C agent (each Claude, concurrent)
           -> rank + compose the split (interfaces.rank_offers, Claude)
           -> awaiting_approval -> approve (or 45s auto-approve)
           -> SIMULATED settlement (simulated_settlement.settle)
           -> confirmed

The ONLY coupling to the rest of the stack is Redis, via the frozen seam:
  * IN  — `baymax:crisis` / `baymax:trigger` / `baymax:decision` (the run loop
          in run_claude_demo.py LPOPs these and calls the entry points below).
  * OUT — every milestone is handed to the registered narration sink
          (run_claude_demo registers dashboard_bus.publish_narration), which
          publishes `{req_id, state, detail, final}` onto `baymax:narration`
          with the FROZEN lowercase `state` vocabulary the web UI keys on.

Public async entry points (ctx-free — no uAgents Context):
    start_crisis(crisis_text, *, requester, region, source) -> req_id | None
    start_negotiation(item, *, requester, need, req_id, source) -> req_id
    resume_after_admin_decision(req_id, decision)              -> None
    check_approval_timeouts()                                  -> None  (watchdog tick)

Everything is fail-soft: a narration/Redis/Claude error never breaks the flow
(the inventory/ranking/offer seams already fall back to deterministic mocks).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from uuid import uuid4

import interfaces
import simulated_settlement
from hospital_agent import decide_offer
from interfaces import OfferView, SupplyNeed, rank_offers, research_crisis

_log = logging.getLogger("baymax.claude")

# Requester + surplus topology. Hospital A is the FRONT/requester; B and C are the
# surplus facilities whose Claude agents we poll. Override via env for other demos.
REQUESTER = os.getenv("BAYMAX_REQUESTER", "Hospital A")
ALL_FACILITIES = ("Hospital A", "Hospital B", "Hospital C")


def _surplus_facilities(requester: str) -> list[str]:
    return [f for f in ALL_FACILITIES if f != requester]


# Seconds to wait for the admin's approve/order/reject before the watchdog acts.
# Dashboard runs auto-APPROVE the inter-facility trade so an unattended pitch never
# stalls; a chat/other source would auto-FAIL. Mirrors the old DASHBOARD timeout.
DASHBOARD_APPROVAL_TIMEOUT_S = float(os.getenv("BAYMAX_DASHBOARD_APPROVAL_TIMEOUT", "45"))
APPROVAL_TIMEOUT_S = float(os.getenv("BAYMAX_APPROVAL_TIMEOUT", "300"))


# ---------------------------------------------------------------------------
# Frozen narration vocabulary. These string values are the contract with the
# web UI (web/app/api/crisis/route.ts maps on them) — they are exactly the old
# protocol.NegotiationState.value strings, kept here as plain constants so this
# module has ZERO dependency on protocol.py / uagents.
# ---------------------------------------------------------------------------
class S:
    IDLE = "idle"
    RESEARCHING = "researching"
    RESEARCHED = "researched"
    SCANNING = "scanning"
    SCANNED = "scanned"
    SHORTFALL_DETECTED = "shortfall_detected"
    REQUESTING = "requesting"
    COLLECTING_OFFERS = "collecting_offers"
    EVALUATING = "evaluating"
    AWAITING_APPROVAL = "awaiting_approval"
    PROPOSING = "proposing"
    SETTLING = "settling"
    CONFIRMED = "confirmed"
    ORDERING = "ordering"
    ORDERED = "ordered"
    FAILED = "failed"


# In-process negotiation state, keyed by request_id. (One process; a multi-process
# deploy would move this into Redis. The run loop serialises to one at a time.)
NEGOTIATIONS: dict[str, dict] = {}


# --- Narration sink (dashboard bus) ----------------------------------------
# run_claude_demo registers dashboard_bus.publish_narration here. Off by default
# so an offline import / harness is silent. A sink error never breaks the flow.
_NARRATION_SINK = None


def register_narration_sink(fn) -> None:
    """Register a sink fn(payload: dict) -> None called on each milestone."""
    global _NARRATION_SINK
    _NARRATION_SINK = fn


# --- Observability hook (Arize/Phoenix) — one-way, optional, no-op default --
_TRACE_HOOK = None


def register_trace_hook(fn) -> None:
    """Register an observability emitter fn(event_type: str, attrs: dict) -> None."""
    global _TRACE_HOOK
    _TRACE_HOOK = fn


def _trace(event_type: str, **attrs) -> None:
    if _TRACE_HOOK is None:
        return
    try:
        _TRACE_HOOK(event_type, attrs)
    except Exception:  # noqa: BLE001 — tracing must never break the negotiation
        pass


async def _emit(req_id: str | None, state: str, detail: str, *, final: bool = False) -> None:
    """Log one milestone and push it to the narration sink as
    `{req_id, state, detail, final}` (the frozen web-UI contract)."""
    _log.info("[%s] %s", state.upper(), detail)
    if _NARRATION_SINK is not None:
        try:
            _NARRATION_SINK({"req_id": req_id, "state": state,
                             "detail": detail, "final": final})
        except Exception:  # noqa: BLE001 — a narration sink must never break the flow
            pass


# ---------------------------------------------------------------------------
# Entry point 1 — crisis flow (the front-of-funnel). A stated crisis ("wildfires
# near Hospital A") -> research (Claude) -> ranked at-risk supplies -> pick the
# top at-risk item that is actually short -> drive start_negotiation.
# ---------------------------------------------------------------------------

def _select_crisis_item(brief, requester: str) -> str | None:
    """Top at-risk item that is present AND below safety threshold; else first
    present; else the top-ranked item, so the demo always negotiates something."""
    for a in brief.at_risk:                        # 1) present + currently short
        try:
            inv = interfaces.get_inventory(requester, a.item)
        except Exception:  # noqa: BLE001
            continue
        if inv.present and inv.shortfall > 0:
            return a.item
    for a in brief.at_risk:                        # 2) present at all
        try:
            inv = interfaces.get_inventory(requester, a.item)
        except Exception:  # noqa: BLE001
            continue
        if inv.present:
            return a.item
    return brief.top_item                          # 3) top-ranked regardless


async def start_crisis(crisis_text: str, *, requester: str = REQUESTER,
                       region: str = "san_francisco", source: str = "dashboard") -> str | None:
    """Research a stated crisis, pick an at-risk item, and drive the negotiation.
    Returns the negotiation request_id, or None if research yielded no item."""
    await _emit(None, S.RESEARCHING,
                f"Researching the crisis to identify at-risk supplies for {requester}…")
    try:
        brief = await asyncio.to_thread(research_crisis, crisis_text, region)
    except Exception as exc:  # noqa: BLE001 — never crash the run loop
        await _emit(None, S.FAILED, f"Crisis research failed ({exc}). No action taken.", final=True)
        return None

    # Publish the brief for the dashboard crisis card (fail-soft; no-op offline).
    try:
        import redis_inventory  # lazy
        redis_inventory.write_crisis_active({
            "crisis_text": crisis_text, "crisis_type": brief.crisis_type,
            "region": region, "rationale": brief.rationale, "status": "researched",
            "requester": requester,
            "at_risk": [{"item": a.item, "risk": a.risk, "rationale": a.rationale}
                        for a in brief.at_risk],
        })
    except Exception:  # noqa: BLE001
        pass

    at_risk_str = ", ".join(f"{a.item} ({a.risk})" for a in brief.at_risk) or "none identified"
    await _emit(None, S.RESEARCHED,
                f"Crisis type: {brief.crisis_type}. At-risk supplies: {at_risk_str}. "
                f"{brief.rationale}")
    _trace("crisis_research", crisis_type=brief.crisis_type, region=region,
           hospital_id=requester, rationale=brief.rationale,
           at_risk=[a.item for a in brief.at_risk])

    item = _select_crisis_item(brief, requester)
    if not item:
        await _emit(None, S.FAILED, "Research surfaced no actionable item to source. "
                    "Escalate to manual review.", final=True)
        return None

    # Optional live shelf scan: re-read inventory from Claude Vision for THIS item.
    try:
        import vision_inventory  # lazy — no uagents dep
        if vision_inventory.vision_on_crisis_enabled():
            await _emit(None, S.SCANNING, f"Scanning the shelf for {item} at {requester}…")
            try:
                inv = await asyncio.to_thread(
                    vision_inventory.refresh_vision_inventory, requester, item)
                await _emit(None, S.SCANNED,
                            f"Vision count: {inv.qty} {item} on hand at {requester} "
                            f"(target {inv.safety_threshold}, shortfall {inv.shortfall}).")
            except Exception as exc:  # noqa: BLE001
                await _emit(None, S.SCANNED,
                            f"Vision scan skipped ({exc}); using last known inventory.")
    except Exception:  # noqa: BLE001 — vision module optional
        pass

    await _emit(None, S.RESEARCHED,
                f"Acting on {item} for {requester} — checking the network now.")
    return await start_negotiation(item, requester=requester, source=source)


# ---------------------------------------------------------------------------
# Entry point 2 — negotiation. Detect the shortfall, ask the surplus Claude
# agents concurrently, rank, halt for approval.
# ---------------------------------------------------------------------------

async def start_negotiation(item: str, *, requester: str = REQUESTER,
                            need: int | None = None, req_id: str | None = None,
                            source: str = "dashboard") -> str:
    """Detect the shortfall, poll the surplus Claude agents, rank, and halt for
    the admin decision. Returns the request_id."""
    inv = await asyncio.to_thread(interfaces.get_inventory, requester, item)
    need = need if need is not None else inv.shortfall
    req_id = req_id or uuid4().hex[:8]
    neg = NEGOTIATIONS[req_id] = {
        "req_id": req_id, "source": source, "item": item, "requester": requester,
        "need": int(need), "offers": {}, "plan": None, "order": None,
        "state": S.IDLE, "done": False, "decided": False, "covered": 0,
    }

    if need <= 0:
        await _emit(req_id, S.IDLE,
                    f"No shortfall: {requester} has {inv.qty} {item} (safety {inv.safety_threshold}).",
                    final=True)
        neg["done"] = True
        return req_id

    neg["state"] = S.SHORTFALL_DETECTED
    await _emit(req_id, S.SHORTFALL_DETECTED,
                f"{requester} is short {need} {item} (on hand {inv.qty}, safety {inv.safety_threshold}).")
    _trace("inventory_low", req_id=req_id, hospital_id=requester, item=item,
           current_qty=inv.qty, threshold=inv.safety_threshold, shortfall=need)

    surplus = _surplus_facilities(requester)
    neg["state"] = S.REQUESTING
    await _emit(req_id, S.REQUESTING,
                f"Broadcasting the need for {need} {item} to {len(surplus)} surplus facilities: "
                f"{', '.join(surplus)}.")

    # Ask every surplus facility's Claude agent concurrently — the multi-agent core.
    neg["state"] = S.COLLECTING_OFFERS
    offers = await asyncio.gather(
        *(decide_offer(f, item, need, requester) for f in surplus),
        return_exceptions=True,
    )
    for facility, offer in zip(surplus, offers):
        if isinstance(offer, Exception):  # decide_offer is fail-closed, but be safe
            _log.warning("[claude] decide_offer raised for %s (%s)", facility, offer)
            continue
        neg["offers"][offer.offerer] = offer
        if offer.can_offer:
            status = (f"can spare {offer.quantity} (≈{offer.distance_km:.0f} km, "
                      f"~{offer.eta_minutes} min, exp {offer.expiry})")
        else:
            status = "declines (no spare)"
        await _emit(req_id, S.COLLECTING_OFFERS, f"{offer.offerer} {status}. {offer.rationale}")

    await _evaluate(req_id)
    return req_id


async def _evaluate(req_id: str) -> None:
    """Rank the collected offers and halt for the admin's decision."""
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg["done"] or neg["state"] != S.COLLECTING_OFFERS:
        return

    offers = [o for o in neg["offers"].values() if o.can_offer and o.quantity > 0]
    declines = [o.offerer for o in neg["offers"].values() if not o.can_offer]
    neg["state"] = S.EVALUATING
    await _emit(req_id, S.EVALUATING,
                f"Evaluating {len(offers)} offer(s)"
                + (f"; declines: {', '.join(declines)}" if declines else "") + ".")

    views = [OfferView(o.offerer, o.quantity, o.distance_km, o.eta_minutes, o.expiry)
             for o in offers]
    plan = await asyncio.to_thread(
        rank_offers,
        SupplyNeed(item=neg["item"], quantity_needed=neg["need"], requester=neg["requester"]),
        views,
    )
    neg["plan"] = plan
    await _emit(req_id, S.EVALUATING, plan.rationale)
    _trace("reasoning_decision", req_id=req_id, item=neg["item"],
           recommended_quantity=neg["need"], actual_quantity=plan.total_covered,
           rationale=plan.rationale,
           legs=[(a.offerer, a.quantity) for a in plan.allocations])

    if plan.allocations and not plan.fully_covered:
        if len(plan.allocations) > 1:
            await _emit(req_id, S.EVALUATING,
                        f"Pooled spare across {len(plan.allocations)} facilities covers only "
                        f"{plan.total_covered}/{neg['need']} {neg['item']} "
                        f"({plan.shortfall_remaining} would remain short).")
        else:
            await _emit(req_id, S.EVALUATING,
                        f"No single facility covers {neg['need']} {neg['item']}; best is "
                        f"{plan.total_covered}/{neg['need']} ({plan.shortfall_remaining} short).")

    await _request_admin_decision(req_id)


async def _request_admin_decision(req_id: str) -> None:
    """Halt at AWAITING_APPROVAL for the admin's approve/order/reject. The run
    loop's watchdog auto-approves a dashboard trade after the timeout. A
    non-dashboard source (offline harness) auto-resolves immediately so it never
    hangs without an operator."""
    neg = NEGOTIATIONS[req_id]
    plan = neg.get("plan")
    has_trade = bool(plan and plan.allocations)

    # Headless / non-dashboard: no operator can click — resolve right away.
    if neg.get("source") != "dashboard":
        if has_trade:
            await _begin_trade(req_id)
        else:
            await _emit(req_id, S.FAILED,
                        f"No facility can spare {neg['item']}. Shortfall of {neg['need']} "
                        f"unresolved — escalate to manual procurement.", final=True)
            neg["done"] = True
        return

    timeout_s = DASHBOARD_APPROVAL_TIMEOUT_S if neg["source"] == "dashboard" else APPROVAL_TIMEOUT_S
    neg["approval_deadline"] = time.monotonic() + timeout_s
    neg["state"] = S.AWAITING_APPROVAL
    if has_trade:
        legs = "; ".join(f"{a.quantity} from {a.offerer}" for a in plan.allocations)
        detail = (f"Decision needed. Best inter-facility trade: {legs} "
                  f"(covers {plan.total_covered}/{neg['need']} {neg['item']}). "
                  f"Approve to authorize the transfer, order to buy externally, or reject.")
    else:
        detail = (f"No facility can spare {neg['item']} (need {neg['need']}). "
                  f"Order to buy from an external supplier, or reject.")
    await _emit(req_id, S.AWAITING_APPROVAL, detail)


async def resume_after_admin_decision(req_id: str, decision: str) -> None:
    """Continue a halted negotiation per the admin's decision
    ({approve, order, reject}). Idempotent via neg["decided"]."""
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg.get("done") or neg.get("decided"):
        return
    if neg.get("state") != S.AWAITING_APPROVAL:
        return
    plan = neg.get("plan")
    has_trade = bool(plan and plan.allocations)

    if decision == "order":
        neg["decided"] = True
        await _order_path(req_id)
        return
    if decision == "reject":
        neg["decided"] = True
        await _emit(req_id, S.FAILED,
                    f"Admin rejected the resolution for {neg['need']} {neg['item']}. "
                    f"Shortfall unresolved — no transfer or order placed.", final=True)
        neg["done"] = True
        return
    # decision == "approve"
    if not has_trade:
        await _emit(req_id, S.AWAITING_APPROVAL,
                    "There is no inter-facility trade to approve. Order to buy externally, "
                    "or reject to cancel.")
        return
    neg["decided"] = True
    await _begin_trade(req_id)


async def _begin_trade(req_id: str) -> None:
    """Narrate the approved plan's legs (the surplus agents already committed this
    capacity in their offers, so legs are accepted by construction), then settle."""
    neg = NEGOTIATIONS[req_id]
    plan = neg["plan"]
    n = len(plan.allocations)
    neg["state"] = S.PROPOSING
    await _emit(req_id, S.PROPOSING, f"Admin approved. Composing transfer: {n} leg(s).")
    for i, al in enumerate(plan.allocations):
        await _emit(req_id, S.PROPOSING,
                    f"Leg {i + 1}/{n}: move {al.quantity} {neg['item']} from {al.offerer} "
                    f"-> {neg['requester']} (~{al.eta_minutes} min).")
    await _settle(req_id)


async def _settle(req_id: str) -> None:
    """Settle the approved legs via the SIMULATED settlement and finalize."""
    neg = NEGOTIATIONS[req_id]
    plan = neg["plan"]
    covered = plan.total_covered
    neg["covered"] = covered
    short = max(0, neg["need"] - covered)

    neg["state"] = S.SETTLING
    await _emit(req_id, S.SETTLING,
                f"Settling {len(plan.allocations)} leg(s) for {covered}/{neg['need']} "
                f"{neg['item']} (simulated inter-facility transfer).")
    ref = simulated_settlement.settle(req_id, plan)
    _log_confirmed_transfers(neg, plan, ref)
    _trace("transfer_recommendation", req_id=req_id, item=neg["item"],
           transfer_quantity=covered, recommended_quantity=neg["need"],
           settlement_ref=ref, legs=[(a.offerer, a.quantity) for a in plan.allocations])

    legs = "; ".join(f"{a.quantity} {neg['item']} from {a.offerer}" for a in plan.allocations)
    if short <= 0:
        detail = (f"Transfer confirmed ({legs}) to {neg['requester']} — full need of "
                  f"{neg['need']} {neg['item']} met. Settlement: {ref}.")
    else:
        detail = (f"Transfer confirmed ({legs}) to {neg['requester']} — covered {covered}/"
                  f"{neg['need']} {neg['item']}; {short} STILL SHORT, escalate the residual to "
                  f"manual procurement. Settlement: {ref}.")
    await _emit(req_id, S.CONFIRMED, detail, final=True)
    neg["done"] = True


async def _order_path(req_id: str) -> None:
    """Place an external-supplier order for the full need and settle it (simulated)."""
    neg = NEGOTIATIONS[req_id]
    neg["state"] = S.ORDERING
    await _emit(req_id, S.ORDERING,
                f"Ordering {neg['need']} {neg['item']} from an external supplier…")
    try:
        budget = float(os.getenv("BAYMAX_ORDER_TIMEOUT_S", "90")) + 10
        order = await asyncio.wait_for(
            asyncio.to_thread(interfaces.order_from_supplier, neg["item"], neg["need"],
                              hospital=neg["requester"]),
            timeout=budget,
        )
    except Exception as exc:  # noqa: BLE001
        await _emit(req_id, S.FAILED,
                    f"External order failed ({exc}). Shortfall unresolved.", final=True)
        neg["done"] = True
        return

    neg["order"] = order
    _trace("supplier_order", req_id=req_id, item=order.item, transfer_quantity=order.quantity,
           vendor=order.vendor, total_price=order.total_price,
           live_view_url=order.live_view_url, confirmation_ref=order.confirmation_ref)
    quote = f", vendor quote {order.total_price} {order.currency}" if order.total_price else ""
    view = f" View: {order.live_view_url}" if order.live_view_url else ""
    prepared = (f"Order prepared with {order.vendor}: {order.quantity} {order.item}"
                f"{quote} (ref {order.confirmation_ref}).{view}")
    await _emit(req_id, S.ORDERING, prepared)

    ref = simulated_settlement.settle(req_id, _OrderPlan(order))
    await _emit(req_id, S.ORDERED, f"{prepared} Settlement: {ref}.", final=True)
    neg["done"] = True


class _OrderPlan:
    """Adapt a SupplierOrder to the .allocations shape simulated_settlement reads."""

    class _Leg:
        def __init__(self, order):
            self.offerer = getattr(order, "vendor", "supplier")
            self.quantity = getattr(order, "quantity", 0)

    def __init__(self, order):
        self.item = getattr(order, "item", "")
        self.allocations = [self._Leg(order)]


def _log_confirmed_transfers(neg: dict, plan, settlement_ref: str) -> None:
    """Fail-soft: append each confirmed leg to the Redis `transfers` stream so the
    dashboard's transfer audit panel reflects the settlement. No-op offline."""
    try:
        from datetime import datetime, timezone
        import redis_inventory  # lazy
        ts = datetime.now(timezone.utc).isoformat()
        for al in plan.allocations:
            redis_inventory.write_transfer_record({
                "item": neg.get("item"), "quantity": al.quantity,
                "from_hospital": al.offerer, "to_hospital": neg.get("requester"),
                "eta_minutes": getattr(al, "eta_minutes", 0), "status": "confirmed",
                "settlement_status": settlement_ref, "tx_id": None,
                "created_at": ts, "req_id": neg.get("req_id"),
            })
    except Exception:  # noqa: BLE001
        pass
    _trace("decision_outcome", req_id=neg.get("req_id"), item=neg.get("item"),
           recommended_quantity=neg.get("need"), actual_quantity=plan.total_covered,
           settlement_ref=settlement_ref, outcome="confirmed")


# ---------------------------------------------------------------------------
# Approval watchdog — called every tick by the run loop. Auto-approves a
# dashboard trade left undecided past its deadline (so an unattended pitch never
# stalls at the gate); auto-fails anything else that times out.
# ---------------------------------------------------------------------------

async def check_approval_timeouts() -> None:
    now = time.monotonic()
    for req_id, neg in list(NEGOTIATIONS.items()):
        if neg.get("done") or neg.get("state") != S.AWAITING_APPROVAL:
            continue
        if now < neg.get("approval_deadline", float("inf")):
            continue
        plan = neg.get("plan")
        if neg.get("source") == "dashboard" and plan and plan.allocations:
            await _emit(req_id, S.AWAITING_APPROVAL,
                        f"No decision within {DASHBOARD_APPROVAL_TIMEOUT_S:.0f}s — "
                        f"auto-approving the inter-facility trade.")
            await resume_after_admin_decision(req_id, "approve")
        else:
            tmo = (DASHBOARD_APPROVAL_TIMEOUT_S
                   if neg.get("source") == "dashboard" else APPROVAL_TIMEOUT_S)
            await _emit(req_id, S.FAILED,
                        f"No admin decision within {tmo:.0f}s — timed out. "
                        f"No transfer or order placed.", final=True)
            neg["done"] = True


def has_active_negotiation() -> bool:
    """True while any negotiation is still in flight (not done). The run loop uses
    this to serialise to one negotiation at a time."""
    return any(not neg.get("done") for neg in NEGOTIATIONS.values())


def find_awaiting_approval(req_id: str | None = None) -> str | None:
    """Return the request_id of a negotiation awaiting approval. With no argument,
    returns the latest such dashboard negotiation (used to route a decision that
    arrives without an explicit req_id)."""
    if req_id is not None:
        neg = NEGOTIATIONS.get(req_id)
        if neg and not neg.get("done") and neg.get("state") == S.AWAITING_APPROVAL:
            return req_id
        return None
    return next((rid for rid, n in NEGOTIATIONS.items()
                 if not n.get("done") and n.get("state") == S.AWAITING_APPROVAL), None)
