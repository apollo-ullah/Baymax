"""baymax_agents.py — the canonical 3-agent supply negotiation.

This is the negotiation core all Wave 1 streams build on. It runs the full PRD
§10 chain across three uAgents:

    Hospital A (FRONT / requester)  -- detects a shortfall, broadcasts a request,
                                       ranks offers, composes a (possibly split)
                                       transfer, settles it, narrates the result.
    Hospital B, Hospital C          -- surplus facilities; respond with
                                       constrained offers and accept/reject the
                                       proposed transfer legs.

Run modes:
  * Bureau (this file's __main__): all three agents in one process for local
    verification — `python baymax_agents.py`. Importing this module has NO
    side effects: the Bureau, agents, and demo kickoff live inside
    run_bureau_demo(), guarded by `if __name__ == "__main__"`. FRONT and SHIP
    import the seams below WITHOUT triggering the demo.
  * Separate processes / Mailbox: the SHIP stream wraps each agent with
    build_hospital_agent(..., mailbox=True) in its own runner.

Clean seams for the other streams (import these; they never trigger the demo):
  * start_negotiation(ctx, item, *, requester, quantity_needed, reply_to, source)
  * start_order(ctx, item, quantity, *, requester, reply_to)
  * settle_transfer(ctx, req_id, plan)
  * resume_after_admin_decision(ctx, req_id, decision)
  * attach_front_handlers(agent)
  * attach_hospital_handlers(agent, facility)

  * FRONT  -> calls start_negotiation(ctx, item, reply_to=<chat sender>) from a
              chat handler; set reply_to so progress + result stream back to
              ASI:One as ChatMessages (narration is automatic when reply_to is set).
  * PAY    -> replaces settle_transfer() with the real Payment Protocol handshake.
  * B/C    -> rank_offers / get_inventory stay behind interfaces.py (B/C streams).

Env knobs (demo runner / hardening — all optional):
  * BAYMAX_ITEM           item to simulate a shortfall of (default "IV fluids").
  * BAYMAX_NEED           override the requester's need quantity (drives the
                             partial/insufficient case when > total spare).
  * BAYMAX_FORCE_REJECT   facility name (e.g. "Hospital B") that will reject
                             the FIRST transfer leg it is proposed, to exercise
                             the leg-reject re-plan path.
  * BAYMAX_OFFER_TIMEOUT  seconds to wait for offers before evaluating (4.0).
  * BAYMAX_MAX_REPLANS    max bounded re-propose attempts on leg reject (3).
  * BAYMAX_EXIT_WHEN_DONE exit the process once a negotiation ends (1/true).
  * BAYMAX_APPROVAL_TIMEOUT seconds to wait for admin decision before auto-fail (300).
"""

from __future__ import annotations

import asyncio
import os
import time
from uuid import uuid4

# Import agent_base FIRST: it installs the Python 3.14 event-loop before any
# Agent is constructed. (Importing it here is a pure module import — no Agent is
# built at import time; construction is deferred into run_bureau_demo().)
from agent_base import (
    REQUESTER,
    SURPLUS_ADDRESSES,
    build_hospital_agent,
    create_text_chat,
)
from uagents import Bureau, Context

from interfaces import (
    OfferView,
    SupplyNeed,
    approve_release,
    distance_between,
    eta_minutes_for,
    expiry_for,
    get_inventory,
    order_from_supplier,
    rank_offers,
)
from protocol import (
    NegotiationState,
    SupplyOffer,
    SupplyRequest,
    TransferAccept,
    TransferProposal,
    TransferReject,
    Urgency,
)

# Seconds to wait for offers before evaluating with whatever arrived.
# Bureau/local: 4s is fine. Mailbox (separate processes): use 30s+ — Agentverse
# round-trips are much slower than in-process Bureau messaging.
OFFER_TIMEOUT_S = float(os.getenv("BAYMAX_OFFER_TIMEOUT", "4.0"))
# When set, only narrate key milestones back to ASI:One (avoids 429 rate limits).
SPARSE_NARRATION = os.getenv("BAYMAX_SPARSE_NARRATION", "").lower() in ("1", "true", "yes")
_NARRATE_STATES = frozenset({
    NegotiationState.SHORTFALL_DETECTED,
    NegotiationState.EVALUATING,
    NegotiationState.PROPOSING,
    NegotiationState.SETTLING,
    NegotiationState.RE_PLANNING,
    NegotiationState.FAILED,
    NegotiationState.CONFIRMED,
    NegotiationState.AWAITING_APPROVAL,
    NegotiationState.ORDERING,
    NegotiationState.ORDERED,
})
# Bounded re-plan: how many times we try to re-home a dropped (rejected) leg
# before giving up and settling what was accepted. Prevents infinite re-propose.
MAX_REPLAN_ATTEMPTS = int(os.getenv("BAYMAX_MAX_REPLANS", "3"))
# For the one-shot Bureau demo/test: exit the process once a negotiation ends.
EXIT_WHEN_DONE = os.getenv("BAYMAX_EXIT_WHEN_DONE", "").lower() in ("1", "true", "yes")
# Seconds the admin has to reply (approve/order/reject) before the watchdog auto-fails.
APPROVAL_TIMEOUT_S = float(os.getenv("BAYMAX_APPROVAL_TIMEOUT", "300"))
# Default order quantity when none is stated and there's no shortfall.
DEFAULT_ORDER_QTY = int(os.getenv("BAYMAX_DEFAULT_ORDER_QTY", "100"))

# Two-tap handshake: when enabled, a provider facility (Hospital B/C) does NOT
# auto-accept a proposed transfer leg — it holds the leg and waits for ITS OWN
# doctor to approve releasing the stock (tap 2; tap 1 is the requester's
# AWAITING_APPROVAL). Default off so existing harnesses keep their auto-accept.
PROVIDER_APPROVAL = os.getenv("BAYMAX_PROVIDER_APPROVAL", "").lower() in ("1", "true", "yes")
RELEASE_TIMEOUT_S = float(os.getenv("BAYMAX_RELEASE_TIMEOUT",
                                    os.getenv("BAYMAX_APPROVAL_TIMEOUT", "300")))

# In-process negotiation state, keyed by request_id. (Bureau runs one process;
# a multi-process deployment would move this into ctx.storage / Redis.)
NEGOTIATIONS: dict[str, dict] = {}

# Narration sinks: callables registered by run_front/run_dashboard_demo to
# receive each milestone payload (e.g. dashboard_bus.publish_narration).
_NARRATION_SINKS: list = []


def register_narration_sink(fn) -> None:
    """Register a callable fn(payload: dict) to receive every milestone."""
    _NARRATION_SINKS.append(fn)


def _publish_event(payload: dict) -> None:
    """Fan a payload out to every narration sink; swallow errors so a sink can
    never break the negotiation. Used by _step (FRONT milestones) and by the
    provider release gate (Hospital B/C release-approval events)."""
    for sink in _NARRATION_SINKS:
        try:
            sink(payload)
        except Exception:
            pass


def _emit(state: str, detail: str, req_id: str = "") -> None:
    """Publish a granular narration event (inter-agent FETCH traffic)."""
    _publish_event({"state": state, "detail": detail, "final": False,
                    "req_id": req_id, "source": "agent"})


# Provider-side held legs awaiting a doctor's release approval (tap 2), keyed by
# proposal_id. Shared module-global (Bureau is one process). Each entry carries
# what resume_release_decision needs to answer the FRONT.
PENDING_RELEASES: dict[str, dict] = {}



# ---------------------------------------------------------------------------
# Narration: log every step; if a negotiation has a `reply_to` (the ASI:One
# chat sender, set by FRONT), also stream the milestone back as a ChatMessage.
# ---------------------------------------------------------------------------

async def _step(ctx: Context, neg: dict, state: NegotiationState, detail: str,
                narrate: bool = False, final: bool = False):
    neg["state"] = state
    ctx.logger.info(f"[{state.value.upper()}] {detail}")
    reply_to = neg.get("reply_to")
    should_narrate = narrate or final
    if SPARSE_NARRATION and not final:
        should_narrate = narrate and state in _NARRATE_STATES
    if reply_to and should_narrate:
        await ctx.send(reply_to, create_text_chat(f"**{state.value}** — {detail}",
                                                  end_session=final))
    # Publish to narration sinks (dashboard SSE, etc.) — always, regardless of
    # reply_to / SPARSE_NARRATION; swallow errors so sinks never break the core.
    req_id = neg.get("req_id", "")
    payload = {
        "state": state.value,
        "detail": detail,
        "final": final,
        "req_id": req_id,
        "source": neg.get("source", "chat"),
    }
    _publish_event(payload)


# ---------------------------------------------------------------------------
# FRONT (Hospital A) — orchestration
# ---------------------------------------------------------------------------

async def start_negotiation(ctx: Context, item: str, *, requester: str = REQUESTER,
                            quantity_needed: int | None = None,
                            reply_to: str | None = None,
                            source: str = "chat") -> str:
    """Detect the shortfall and broadcast a SupplyRequest. Returns request_id.

    FRONT calls this from its chat handler with reply_to=<sender> so the whole
    negotiation narrates back into the ASI:One conversation.

    quantity_needed overrides the inventory-derived shortfall — used to drive the
    partial/insufficient case (need larger than the network's total spare).
    source="dashboard" marks negotiations triggered by the scan dashboard (no FET
    payment, stub settlement).
    """
    inv = get_inventory(requester, item)
    need = quantity_needed if quantity_needed is not None else inv.shortfall
    req_id = uuid4().hex[:8]
    neg = NEGOTIATIONS[req_id] = {
        "req_id": req_id,
        "item": item, "requester": requester, "need": need,
        "offers": {}, "expected": set(SURPLUS_ADDRESSES.values()),
        "plan": None, "pending": set(), "accepts": set(), "rejects": set(),
        "deadline": time.monotonic() + OFFER_TIMEOUT_S, "done": False,
        "reply_to": reply_to, "state": NegotiationState.IDLE,
        "source": source,
        # Hardening bookkeeping ------------------------------------------------
        "evaluated": False,          # idempotence guard for _evaluate
        "settled": False,            # idempotence guard for _settle
        "decided": False,            # idempotence guard for resume_after_admin_decision
        # committed[facility] = quantity already locked in by an accepted leg.
        "committed": {},
        # leg[pid] = {"offerer", "quantity", "eta"} for each live/settled leg.
        "leg": {},
        # rejected facilities (excluded from re-planning).
        "rejected_facilities": set(),
        "replans": 0,                # bounded re-plan attempt counter
        "covered": 0,                # running total of accepted quantity
        # Wave 3 admin gate
        "approval_deadline": None,
        "awaiting_payment": False,
    }

    if need <= 0:
        await _step(ctx, neg, NegotiationState.IDLE,
                    f"No shortfall: {requester} has {inv.qty} {item} (safety {inv.safety_threshold}).",
                    narrate=True, final=True)
        neg["done"] = True
        return req_id

    await _step(ctx, neg, NegotiationState.SHORTFALL_DETECTED,
                f"{requester} is short {need} {item} (on hand {inv.qty}, safety {inv.safety_threshold}).",
                narrate=True)
    req = SupplyRequest(request_id=req_id, requester=requester, item=item,
                        quantity_needed=need, urgency=Urgency.CRITICAL)
    await _step(ctx, neg, NegotiationState.REQUESTING,
                f"Broadcasting request {req_id} for {need} {item} to {len(SURPLUS_ADDRESSES)} facilities.",
                narrate=True)
    for facility_name, addr in SURPLUS_ADDRESSES.items():
        ctx.logger.info(
            "[FETCH] action=send_supply_request from=%s to=%s to_addr=%s "
            "message_type=SupplyRequest item=%s quantity=%d req_id=%s",
            requester, facility_name, addr, item, need, req_id,
        )
        _emit("request_sent",
              f"📤 {requester} → {facility_name}: requesting {need} {item}", req_id)
        await ctx.send(addr, req)
    neg["state"] = NegotiationState.COLLECTING_OFFERS
    return req_id


async def start_order(ctx: Context, item: str, quantity: int | None = None,
                      *, requester: str = REQUESTER,
                      reply_to: str | None = None) -> str:
    """Proactive external-order entrypoint — bypasses the shortfall guard.

    Used when the admin sends "order N <item>" directly into the chat without a
    prior shortfall. Creates a minimal negotiation record and jumps straight to
    the order path.
    """
    qty = quantity if quantity is not None else DEFAULT_ORDER_QTY
    req_id = uuid4().hex[:8]
    neg = NEGOTIATIONS[req_id] = {
        "req_id": req_id,
        "item": item, "requester": requester, "need": qty,
        "offers": {}, "expected": set(), "plan": None,
        "pending": set(), "accepts": set(), "rejects": set(),
        "deadline": time.monotonic(), "done": False,
        "reply_to": reply_to, "state": NegotiationState.IDLE,
        "source": "chat",
        "evaluated": True, "settled": False, "decided": True,
        "committed": {}, "leg": {}, "rejected_facilities": set(),
        "replans": 0, "covered": 0, "approval_deadline": None,
        "awaiting_payment": False,
    }
    await _step(ctx, neg, NegotiationState.ORDERING,
                f"Proactive order: sourcing {qty} {item} from an external supplier.",
                narrate=True)
    await _order_path(ctx, req_id)
    return req_id


async def _evaluate(ctx: Context, req_id: str):
    """Rank the collected offers, then halt at AWAITING_APPROVAL for the admin.
    Idempotent: only the first call for a given negotiation runs."""
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg["done"] or neg["evaluated"]:
        return
    if neg["state"] not in (NegotiationState.COLLECTING_OFFERS,
                            NegotiationState.RE_PLANNING):
        return
    neg["evaluated"] = True

    offers = [o for o in neg["offers"].values() if o.can_offer and o.quantity_available > 0]
    declines = [o.offerer for o in neg["offers"].values() if not o.can_offer]
    await _step(ctx, neg, NegotiationState.EVALUATING,
                f"Evaluating {len(offers)} offer(s)"
                + (f"; declines: {', '.join(declines)}" if declines else "") + ".",
                narrate=True)

    views = [OfferView(o.offerer, o.quantity_available, o.distance_km, o.eta_minutes, o.expiry)
             for o in offers]
    plan = rank_offers(SupplyNeed(item=neg["item"], quantity_needed=neg["need"], requester=neg["requester"]),
                       views)
    neg["plan"] = plan
    await _step(ctx, neg, NegotiationState.EVALUATING, plan.rationale, narrate=True)

    if not plan.allocations:
        # No offers at all — give the admin the option to order instead.
        neg["approval_deadline"] = time.monotonic() + APPROVAL_TIMEOUT_S
        await _step(ctx, neg, NegotiationState.AWAITING_APPROVAL,
                    f"No facility can spare {neg['item']}. "
                    f"Reply **approve** (re-try later), **order** (buy externally), or **reject** (cancel). "
                    f"Request ID: {req_id}",
                    narrate=True)
        return

    # We have a plan — pause and let the admin decide.
    from settlement import _amount_for_total  # lazy
    total = sum(a.quantity for a in plan.allocations)
    amount = _amount_for_total(total)
    cover_note = "full cover" if plan.fully_covered else f"partial ({plan.total_covered}/{neg['need']})"
    neg["approval_deadline"] = time.monotonic() + APPROVAL_TIMEOUT_S
    await _step(ctx, neg, NegotiationState.AWAITING_APPROVAL,
                f"Plan ready ({cover_note}, ~{amount} FET): {plan.rationale} "
                f"Reply **approve** to execute the trade, **order** to buy externally, "
                f"or **reject** to cancel. Request ID: {req_id}",
                narrate=True)


async def resume_after_admin_decision(ctx: Context, req_id: str, decision: str) -> None:
    """Resume a halted negotiation after the admin's approve/order/reject reply.

    decision must be one of "approve", "order", "reject". Idempotent via
    neg["decided"] — double-fire from the chat + dashboard paths is a no-op.
    """
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg["done"]:
        return
    if neg["decided"]:
        return
    neg["decided"] = True

    if decision == "approve":
        plan = neg.get("plan")
        if not plan or not plan.allocations:
            await _step(ctx, neg, NegotiationState.FAILED,
                        f"No plan to approve for {req_id}; shortfall unresolved.",
                        narrate=True, final=True)
            neg["done"] = True
            _maybe_exit(ctx)
            return
        await _step(ctx, neg, NegotiationState.PROPOSING,
                    f"Admin approved. Composing transfer: {len(plan.allocations)} leg(s).",
                    narrate=True)
        n = len(plan.allocations)
        for i, al in enumerate(plan.allocations):
            pid = f"{req_id}-{i}"
            await _propose_leg(ctx, neg, req_id, pid, al.offerer, al.quantity, al.eta_minutes,
                               leg_index=i, leg_count=n)

    elif decision == "order":
        await _step(ctx, neg, NegotiationState.ORDERING,
                    f"Admin chose external order for {neg['need']} {neg['item']}.",
                    narrate=True)
        await _order_path(ctx, req_id)

    else:  # reject
        await _step(ctx, neg, NegotiationState.FAILED,
                    f"Admin rejected the negotiation {req_id}. Transfer cancelled.",
                    narrate=True, final=True)
        neg["done"] = True
        _maybe_exit(ctx)


async def _order_path(ctx: Context, req_id: str) -> None:
    """Execute the external-supplier order branch."""
    neg = NEGOTIATIONS[req_id]
    item = neg["item"]
    quantity = neg["need"]
    hospital = neg["requester"]
    try:
        order = await asyncio.to_thread(order_from_supplier, item, quantity, hospital=hospital)
        ctx.logger.info(
            "[order] vendor=%s total=%s ref=%s status=%s",
            order.vendor, order.total_price, order.confirmation_ref, order.status,
        )
        await _step(ctx, neg, NegotiationState.ORDERING,
                    f"Order prepared: {quantity} {item} from {order.vendor} "
                    f"(~${order.total_price} {order.currency}, ref {order.confirmation_ref}).",
                    narrate=True)
    except Exception as exc:
        await _step(ctx, neg, NegotiationState.FAILED,
                    f"Supplier order failed: {exc}. Shortfall unresolved.",
                    narrate=True, final=True)
        neg["done"] = True
        _maybe_exit(ctx)
        return

    ref = await settle_order(ctx, req_id, order)
    if _ORDER_SETTLEMENT_HOOK is not None and neg.get("reply_to"):
        # Payment is async — terminal ORDERED milestone fires in finalize_after_order_payment.
        neg["awaiting_payment"] = True
        return

    await _step(ctx, neg, NegotiationState.ORDERED,
                f"External order settled: {quantity} {item} from {order.vendor}. Ref: {ref}.",
                narrate=True, final=True)
    neg["done"] = True
    _maybe_exit(ctx)


async def _propose_leg(ctx: Context, neg: dict, req_id: str, pid: str,
                       offerer: str, quantity: int, eta_minutes: int,
                       *, leg_index: int, leg_count: int):
    """Send one TransferProposal and record it as pending/committed."""
    neg["pending"].add(pid)
    neg["leg"][pid] = {"offerer": offerer, "quantity": quantity, "eta": eta_minutes}
    neg["committed"][offerer] = neg["committed"].get(offerer, 0) + quantity
    prop = TransferProposal(request_id=req_id, proposal_id=pid, item=neg["item"],
                            quantity=quantity, from_facility=offerer,
                            to_facility=neg["requester"], eta_minutes=eta_minutes,
                            leg_index=leg_index, leg_count=leg_count)
    await _step(ctx, neg, NegotiationState.PROPOSING,
                f"Leg {pid}: move {quantity} {neg['item']} from {offerer} "
                f"-> {neg['requester']} (~{eta_minutes} min).")
    ctx.logger.info(
        "[FETCH] action=send_transfer_proposal from=%s to=%s "
        "message_type=TransferProposal pid=%s item=%s quantity=%d eta_minutes=%d",
        neg["requester"], offerer, pid, neg["item"], quantity, eta_minutes,
    )
    _emit("proposal_sent",
          f"📤 {neg['requester']} → {offerer}: proposing transfer of {quantity} "
          f"{neg['item']} (~{eta_minutes} min)", req_id)
    await ctx.send(SURPLUS_ADDRESSES[offerer], prop)


async def _replan_rejected_leg(ctx: Context, req_id: str, dropped_quantity: int):
    """Re-rank the remaining offers (excluding rejecting facilities and capacity
    already committed by accepted legs) and try to re-home `dropped_quantity` on
    another facility. Bounded by MAX_REPLAN_ATTEMPTS. Returns True if a fresh
    proposal was sent, False if no facility can take it."""
    neg = NEGOTIATIONS[req_id]
    if neg["replans"] >= MAX_REPLAN_ATTEMPTS:
        await _step(ctx, neg, NegotiationState.RE_PLANNING,
                    f"Re-plan budget exhausted ({MAX_REPLAN_ATTEMPTS} attempts); cannot re-home "
                    f"{dropped_quantity} {neg['item']}.", narrate=True)
        return False
    neg["replans"] += 1

    candidates = []
    for offerer, offer in neg["offers"].items():
        if not offer.can_offer or offer.quantity_available <= 0:
            continue
        if offerer in neg["rejected_facilities"]:
            continue
        remaining = offer.quantity_available - neg["committed"].get(offerer, 0)
        if remaining <= 0:
            continue
        candidates.append(OfferView(offerer, remaining, offer.distance_km,
                                    offer.eta_minutes, offer.expiry))

    if not candidates:
        await _step(ctx, neg, NegotiationState.RE_PLANNING,
                    f"No alternative facility has spare {neg['item']} for the dropped "
                    f"{dropped_quantity}.", narrate=True)
        return False

    plan = rank_offers(SupplyNeed(item=neg["item"], quantity_needed=dropped_quantity,
                                  requester=neg["requester"]), candidates)
    if not plan.allocations:
        await _step(ctx, neg, NegotiationState.RE_PLANNING,
                    f"Re-rank found no taker for the dropped {dropped_quantity} {neg['item']}.",
                    narrate=True)
        return False

    await _step(ctx, neg, NegotiationState.RE_PLANNING,
                f"Re-plan attempt {neg['replans']}/{MAX_REPLAN_ATTEMPTS}: {plan.rationale}",
                narrate=True)
    base = len(neg["leg"])
    total_new = len(plan.allocations)
    for j, al in enumerate(plan.allocations):
        pid = f"{req_id}-r{neg['replans']}-{j}"
        await _propose_leg(ctx, neg, req_id, pid, al.offerer, al.quantity, al.eta_minutes,
                           leg_index=base + j, leg_count=base + total_new)
    return True


async def _maybe_finish(ctx: Context, req_id: str):
    """Called whenever a leg resolves. If nothing is pending, settle whatever was
    accepted (which may be partial). Idempotent via neg["settled"]."""
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg["done"]:
        return
    if neg["pending"]:
        return
    if neg["accepts"]:
        await _settle(ctx, req_id)
    else:
        await _step(ctx, neg, NegotiationState.FAILED,
                    f"All proposed legs were rejected and none could be re-homed. Shortfall of "
                    f"{neg['need']} {neg['item']} unresolved — escalate to manual procurement.",
                    narrate=True, final=True)
        neg["done"] = True
        _maybe_exit(ctx)


async def _settle(ctx: Context, req_id: str):
    """Settle the accepted legs. Idempotent: guarded so it can never double-fire."""
    neg = NEGOTIATIONS[req_id]
    if neg["settled"] or neg["done"]:
        return
    neg["settled"] = True

    accepted_legs = [neg["leg"][pid] for pid in neg["accepts"] if pid in neg["leg"]]
    covered = sum(leg["quantity"] for leg in accepted_legs)
    neg["covered"] = covered
    short = max(0, neg["need"] - covered)

    await _step(ctx, neg, NegotiationState.SETTLING,
                f"Settling {len(accepted_legs)} accepted leg(s) for {covered}/{neg['need']} "
                f"{neg['item']}.", narrate=True)
    tx = await settle_transfer(ctx, req_id, _SettlementPlan(accepted_legs, covered, short))

    legs = "; ".join(f"{leg['quantity']} {neg['item']} from {leg['offerer']}"
                     for leg in accepted_legs)
    if short <= 0:
        detail = (f"Transfer confirmed ({legs}) to {neg['requester']} — full need of "
                  f"{neg['need']} {neg['item']} met. Settlement: {tx}.")
    else:
        detail = (f"Transfer confirmed ({legs}) to {neg['requester']} — covered {covered}/"
                  f"{neg['need']} {neg['item']}; {short} STILL SHORT, escalate the residual to "
                  f"manual procurement. Settlement: {tx}.")

    if _SETTLEMENT_HOOK is not None and neg.get("reply_to"):
        from settlement import _amount_for_total
        amount = _amount_for_total(covered)
        neg["awaiting_payment"] = True
        await _step(
            ctx, neg, NegotiationState.SETTLING,
            f"Negotiation complete — approve **{amount} FET** on testnet to finalize "
            f"(ref {tx}). In ASI:One, open your **wallet** (top bar) if no payment "
            f"prompt appears in chat.",
            narrate=True, final=False,
        )
        return

    await _step(ctx, neg, NegotiationState.CONFIRMED, detail, narrate=True, final=True)
    neg["done"] = True
    _maybe_exit(ctx)


async def finalize_after_payment(
    ctx: Context, req_id: str, settlement_ref: str, *, tx_id: str | None = None,
) -> None:
    """Send the terminal CONFIRMED chat milestone after on-chain payment succeeds."""
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg.get("done"):
        return
    accepted_legs = [neg["leg"][pid] for pid in neg["accepts"] if pid in neg["leg"]]
    covered = neg.get("covered") or sum(leg["quantity"] for leg in accepted_legs)
    short = max(0, neg["need"] - covered)
    legs = "; ".join(f"{leg['quantity']} {neg['item']} from {leg['offerer']}"
                     for leg in accepted_legs)
    tx_note = f" On-chain tx: `{tx_id}`." if tx_id else ""
    if short <= 0:
        detail = (f"Transfer confirmed ({legs}) to {neg['requester']} — full need of "
                  f"{neg['need']} {neg['item']} met. Settlement: {settlement_ref}.{tx_note}")
    else:
        detail = (f"Transfer confirmed ({legs}) to {neg['requester']} — covered {covered}/"
                  f"{neg['need']} {neg['item']}; {short} still short. "
                  f"Settlement: {settlement_ref}.{tx_note}")
    await _step(ctx, neg, NegotiationState.CONFIRMED, detail, narrate=True, final=True)
    neg["awaiting_payment"] = False
    neg["done"] = True
    _maybe_exit(ctx)


async def finalize_after_order_payment(
    ctx: Context, req_id: str, settlement_ref: str, *, tx_id: str | None = None,
) -> None:
    """Terminal ORDERED milestone after on-chain order-payment succeeds."""
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg.get("done"):
        return
    tx_note = f" On-chain tx: `{tx_id}`." if tx_id else ""
    detail = (f"External order settled for {neg['need']} {neg['item']}. "
              f"Ref: {settlement_ref}.{tx_note}")
    await _step(ctx, neg, NegotiationState.ORDERED, detail, narrate=True, final=True)
    neg["awaiting_payment"] = False
    neg["done"] = True
    _maybe_exit(ctx)


async def fail_after_payment(ctx: Context, req_id: str, reason: str) -> None:
    """Close the chat session when payment verification fails."""
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg.get("done"):
        return
    await _step(
        ctx, neg, NegotiationState.FAILED,
        f"Payment could not be verified on testnet ({reason}). The transfer legs "
        f"were accepted but settlement was not confirmed on-chain.",
        narrate=True, final=True,
    )
    neg["awaiting_payment"] = False
    neg["done"] = True
    _maybe_exit(ctx)


class _SettlementPlan:
    """Lightweight allocation carrier passed to settle_transfer()."""

    class _Leg:
        def __init__(self, offerer: str, quantity: int, eta_minutes: int):
            self.offerer = offerer
            self.quantity = quantity
            self.eta_minutes = eta_minutes
            self.distance_km = 0.0
            self.expiry = None

    def __init__(self, legs: list[dict], total_covered: int, shortfall_remaining: int):
        self.allocations = [self._Leg(l["offerer"], l["quantity"], l.get("eta", 0))
                            for l in legs]
        self.total_covered = total_covered
        self.shortfall_remaining = shortfall_remaining
        self.fully_covered = shortfall_remaining <= 0


# --- Settlement hooks -------------------------------------------------------

_SETTLEMENT_HOOK = None
_ORDER_SETTLEMENT_HOOK = None


def register_settlement_hook(fn) -> None:
    """Register the real trade settlement handler."""
    global _SETTLEMENT_HOOK
    _SETTLEMENT_HOOK = fn


def register_order_settlement_hook(fn) -> None:
    """Register the real order settlement handler."""
    global _ORDER_SETTLEMENT_HOOK
    _ORDER_SETTLEMENT_HOOK = fn


async def settle_transfer(ctx: Context, req_id: str, plan) -> str:
    """Settle a resolved inter-facility transfer."""
    neg = NEGOTIATIONS.get(req_id, {})
    user_address = neg.get("reply_to")
    if _SETTLEMENT_HOOK is not None and user_address:
        return await _SETTLEMENT_HOOK(
            ctx, req_id, plan, user_address=user_address, reply_to=user_address,
        )
    ref = f"stub-settlement-{req_id}"
    ctx.logger.info(
        f"[settlement-stub] Settled {len(plan.allocations)} leg(s) on "
        f"{os.getenv('FETCH_NETWORK', 'testnet')} -> {ref} "
        f"(no payment hook / no chat user; register_settlement_hook wires real FET)."
    )
    return ref


async def settle_order(ctx: Context, req_id: str, order) -> str:
    """Settle an external supplier order."""
    neg = NEGOTIATIONS.get(req_id, {})
    user_address = neg.get("reply_to")
    if _ORDER_SETTLEMENT_HOOK is not None and user_address:
        return await _ORDER_SETTLEMENT_HOOK(
            ctx, req_id, order, user_address=user_address, reply_to=user_address,
        )
    ref = f"stub-order-{req_id}"
    ctx.logger.info(
        f"[settlement-stub] Order settled stub -> {ref} "
        f"(no order hook / no chat user; register_order_settlement_hook wires real FET)."
    )
    return ref


def attach_front_handlers(front):
    """Wire the requester (Hospital A) message handlers + offer-timeout tick."""

    @front.on_message(model=SupplyOffer)
    async def on_offer(ctx: Context, sender: str, msg: SupplyOffer):
        neg = NEGOTIATIONS.get(msg.request_id)
        if not neg or neg["done"]:
            ctx.logger.debug(
                f"Ignoring SupplyOffer for unknown/done request {msg.request_id} "
                f"from {sender} ({msg.offerer})."
            )
            return
        if msg.offerer in neg["offers"]:
            return
        neg["offers"][msg.offerer] = msg
        ctx.logger.info(
            "[FETCH] action=receive_supply_offer from=%s can_offer=%s "
            "quantity_available=%d distance_km=%.1f eta_minutes=%d expiry=%s req_id=%s",
            msg.offerer, msg.can_offer, msg.quantity_available,
            msg.distance_km, msg.eta_minutes, msg.expiry, msg.request_id,
        )
        status = (f"can spare {msg.quantity_available} (≈{msg.distance_km:.0f} km, "
                  f"exp {msg.expiry})") if msg.can_offer else "declines (no spare)"
        await _step(ctx, neg, NegotiationState.COLLECTING_OFFERS,
                    f"{msg.offerer} {status}  [{len(neg['offers'])}/{len(neg['expected'])}]")
        if len(neg["offers"]) >= len(neg["expected"]):
            await _evaluate(ctx, msg.request_id)

    @front.on_message(model=TransferAccept)
    async def on_accept(ctx: Context, sender: str, msg: TransferAccept):
        neg = NEGOTIATIONS.get(msg.request_id)
        if not neg or neg["done"]:
            return
        if msg.proposal_id not in neg["pending"]:
            return
        neg["pending"].discard(msg.proposal_id)
        neg["accepts"].add(msg.proposal_id)
        total = len(neg["leg"])
        ctx.logger.info(
            "[FETCH] action=receive_transfer_accept from=%s pid=%s req_id=%s",
            msg.accepted_by, msg.proposal_id, msg.request_id,
        )
        await _step(ctx, neg, NegotiationState.PROPOSING,
                    f"{msg.accepted_by} accepted leg {msg.proposal_id}  "
                    f"[{len(neg['accepts'])}/{total}]")
        await _maybe_finish(ctx, msg.request_id)

    @front.on_message(model=TransferReject)
    async def on_reject(ctx: Context, sender: str, msg: TransferReject):
        neg = NEGOTIATIONS.get(msg.request_id)
        if not neg or neg["done"]:
            return
        if msg.proposal_id not in neg["pending"]:
            return
        neg["pending"].discard(msg.proposal_id)
        neg["rejects"].add(msg.proposal_id)

        ctx.logger.info(
            "[FETCH] action=receive_transfer_reject from=%s pid=%s req_id=%s reason=%s",
            msg.rejected_by, msg.proposal_id, msg.request_id, msg.reason,
        )
        leg = neg["leg"].get(msg.proposal_id, {})
        dropped_qty = leg.get("quantity", 0)
        rejecter = msg.rejected_by or leg.get("offerer")
        if rejecter:
            neg["rejected_facilities"].add(rejecter)
            if rejecter in neg["committed"]:
                neg["committed"][rejecter] = max(0, neg["committed"][rejecter] - dropped_qty)

        await _step(ctx, neg, NegotiationState.RE_PLANNING,
                    f"{rejecter} rejected leg {msg.proposal_id} ({msg.reason}); re-planning the "
                    f"dropped {dropped_qty} {neg['item']} onto another facility.", narrate=True)

        rehomed = await _replan_rejected_leg(ctx, msg.request_id, dropped_qty)
        if not rehomed:
            await _step(ctx, neg, NegotiationState.RE_PLANNING,
                        f"Could not re-home the dropped {dropped_qty} {neg['item']}; will settle "
                        f"the accepted legs and flag the residual shortfall.", narrate=True)
        await _maybe_finish(ctx, msg.request_id)

    @front.on_interval(period=1.0)
    async def offer_timeout(ctx: Context):
        """Offer-window guard + AWAITING_APPROVAL watchdog."""
        nowt = time.monotonic()
        for req_id, neg in list(NEGOTIATIONS.items()):
            if neg["done"]:
                continue
            # Offer window: evaluate once deadline passes while still collecting.
            if neg["state"] == NegotiationState.COLLECTING_OFFERS and not neg["evaluated"]:
                if nowt >= neg["deadline"]:
                    await _step(ctx, neg, NegotiationState.COLLECTING_OFFERS,
                                f"Offer window closed: {len(neg['offers'])}/{len(neg['expected'])} "
                                f"responded. Evaluating with what arrived.", narrate=True)
                    await _evaluate(ctx, req_id)
            # Admin gate watchdog: auto-fail if the admin never replied.
            elif neg["state"] == NegotiationState.AWAITING_APPROVAL:
                deadline = neg.get("approval_deadline")
                if deadline and nowt > deadline:
                    await _step(ctx, neg, NegotiationState.FAILED,
                                f"Admin decision timeout for {req_id} — no response within "
                                f"{APPROVAL_TIMEOUT_S:.0f}s. Negotiation cancelled.",
                                narrate=True, final=True)
                    neg["done"] = True
                    _maybe_exit(ctx)


# ---------------------------------------------------------------------------
# SURPLUS facilities (Hospital B, C) — offer + accept/reject
# ---------------------------------------------------------------------------

async def _send_leg_accept(ctx: Context, facility: str, front: str, request_id: str,
                           pid: str, item: str, quantity: int, to_facility: str,
                           leg_index: int = 0, leg_count: int = 1) -> None:
    """Send a TransferAccept for one leg back to the FRONT (shared by the
    auto-approve path and the resumed-after-doctor-approval path)."""
    ctx.logger.info(f"[{facility}] Accepting leg {leg_index + 1}/{leg_count}: "
                    f"{quantity} {item} -> {to_facility}.")
    ctx.logger.info(
        "[FETCH] action=send_transfer_accept from=%s to=%s state=supply_request_accepted "
        "item=%s quantity=%d pid=%s req_id=%s",
        facility, to_facility, item, quantity, pid, request_id,
    )
    await ctx.send(front, TransferAccept(request_id=request_id, proposal_id=pid,
                                         accepted_by=facility))


async def resume_release_decision(ctx: Context, pid: str, decision: str) -> None:
    """Tap 2: resume a held transfer leg after the provider's doctor decides.

    decision is "approve", "deny", or "timeout". Idempotent — the held leg is
    popped on the first call, so a double-fire (UI tap + watchdog) is a no-op.
    """
    pending = PENDING_RELEASES.pop(pid, None)
    if not pending:
        return
    facility = pending["facility"]
    front = pending["front"]
    if decision == "approve":
        inv = get_inventory(facility, pending["item"])
        if inv.spare_capacity >= pending["quantity"]:
            await _send_leg_accept(ctx, facility, front, pending["request_id"], pid,
                                   pending["item"], pending["quantity"], pending["to_facility"],
                                   pending["leg_index"], pending["leg_count"])
            _publish_event({"state": "release_approved", "facility": facility,
                            "req_id": pending["request_id"], "pid": pid,
                            "detail": f"{facility} approved release of "
                                      f"{pending['quantity']} {pending['item']}.",
                            "source": "provider_gate", "final": False})
        else:
            ctx.logger.info(f"[{facility}] Release approved but spare dropped to "
                            f"{inv.spare_capacity}; rejecting leg {pid}.")
            await ctx.send(front, TransferReject(
                request_id=pending["request_id"], proposal_id=pid, rejected_by=facility,
                reason=f"only {inv.spare_capacity} spare at release"))
    else:  # deny / timeout
        reason = ("release window timed out" if decision == "timeout"
                  else "provider doctor denied release")
        ctx.logger.info(f"[{facility}] Release {decision} for leg {pid}: {reason}.")
        await ctx.send(front, TransferReject(
            request_id=pending["request_id"], proposal_id=pid, rejected_by=facility,
            reason=reason))
        _publish_event({"state": "release_denied", "facility": facility,
                        "req_id": pending["request_id"], "pid": pid,
                        "detail": f"{facility} {decision}: {reason}.",
                        "source": "provider_gate", "final": False})


def attach_hospital_handlers(agent, facility: str):
    """Wire a surplus facility's negotiation handlers."""

    force_reject = os.getenv("BAYMAX_FORCE_REJECT", "").strip()
    _forced = {"done": False}

    @agent.on_message(model=SupplyRequest)
    async def on_request(ctx: Context, sender: str, msg: SupplyRequest):
        ctx.logger.info(
            "[FETCH] action=receive_supply_request at=%s from=%s "
            "item=%s quantity_needed=%d req_id=%s",
            facility, msg.requester, msg.item, msg.quantity_needed, msg.request_id,
        )
        _emit("request_received",
              f"📨 {facility} received request for {msg.quantity_needed} {msg.item}",
              msg.request_id)
        inv = get_inventory(facility, msg.item)
        spare = inv.spare_capacity
        if spare > 0:
            dist = distance_between(facility, msg.requester)
            offer = SupplyOffer(
                request_id=msg.request_id, offerer=facility, item=msg.item,
                quantity_available=spare, distance_km=dist,
                eta_minutes=eta_minutes_for(dist), expiry=expiry_for(facility, msg.item),
                can_offer=True,
            )
            ctx.logger.info(
                "[FETCH] action=send_supply_offer from=%s to=%s "
                "item=%s quantity=%d distance_km=%.1f eta_minutes=%d can_offer=True req_id=%s",
                facility, msg.requester, msg.item, spare, dist,
                eta_minutes_for(dist), msg.request_id,
            )
            ctx.logger.info(f"[{facility}] Offering {spare} {msg.item} "
                            f"(have {inv.qty}, safety {inv.safety_threshold}).")
            _emit("offer_made",
                  f"📦 {facility} offers {spare} {msg.item} (≈{eta_minutes_for(dist)} min)",
                  msg.request_id)
        else:
            offer = SupplyOffer(request_id=msg.request_id, offerer=facility,
                                item=msg.item, quantity_available=0, can_offer=False)
            ctx.logger.info(
                "[FETCH] action=send_supply_offer from=%s to=%s "
                "item=%s quantity=0 can_offer=False req_id=%s",
                facility, msg.requester, msg.item, msg.request_id,
            )
            ctx.logger.info(f"[{facility}] No spare {msg.item} — declining.")
            _emit("offer_declined",
                  f"🚫 {facility} has no spare {msg.item} — declining", msg.request_id)
        await ctx.send(sender, offer)

    @agent.on_message(model=TransferProposal)
    async def on_proposal(ctx: Context, sender: str, msg: TransferProposal):
        ctx.logger.info(
            "[FETCH] action=receive_transfer_proposal at=%s from=%s "
            "item=%s quantity=%d leg=%d/%d pid=%s req_id=%s",
            facility, msg.from_facility, msg.item, msg.quantity,
            msg.leg_index + 1, msg.leg_count, msg.proposal_id, msg.request_id,
        )
        _emit("proposal_received",
              f"📥 {facility} received transfer proposal: {msg.quantity} {msg.item} "
              f"→ {msg.to_facility}", msg.request_id)
        # Forced-reject knob.
        if force_reject == facility and not _forced["done"]:
            _forced["done"] = True
            ctx.logger.info(f"[{facility}] FORCING reject of leg {msg.proposal_id} "
                            f"(BAYMAX_FORCE_REJECT).")
            await ctx.send(sender, TransferReject(
                request_id=msg.request_id, proposal_id=msg.proposal_id,
                rejected_by=facility, reason="forced reject (demo)"))
            return

        # Reject up front if we simply do not have the spare — no human needed.
        inv = get_inventory(facility, msg.item)
        if inv.spare_capacity < msg.quantity:
            ctx.logger.info(f"[{facility}] Rejecting leg {msg.proposal_id}: only "
                            f"{inv.spare_capacity} spare, asked for {msg.quantity}.")
            await ctx.send(sender, TransferReject(request_id=msg.request_id,
                                                  proposal_id=msg.proposal_id, rejected_by=facility,
                                                  reason=f"only {inv.spare_capacity} spare"))
            return

        # Two-tap handshake (tap 2): hold the leg and wait for THIS facility's
        # doctor to approve the release. The UI texts the provider's doctor on the
        # release_pending event; the tapped link is drained by release_poll below.
        if PROVIDER_APPROVAL:
            PENDING_RELEASES[msg.proposal_id] = {
                "request_id": msg.request_id, "proposal_id": msg.proposal_id,
                "facility": facility, "item": msg.item, "quantity": msg.quantity,
                "to_facility": msg.to_facility, "leg_index": msg.leg_index,
                "leg_count": msg.leg_count, "front": sender,
                "deadline": time.monotonic() + RELEASE_TIMEOUT_S,
            }
            detail = (f"{facility}: approve releasing {msg.quantity} {msg.item} to "
                      f"{msg.to_facility}? (leg {msg.leg_index + 1}/{msg.leg_count})")
            ctx.logger.info(f"[{facility}] Release AWAITING provider-doctor approval "
                            f"for leg {msg.proposal_id} ({msg.quantity} {msg.item}).")
            _publish_event({
                "state": "release_pending", "facility": facility,
                "req_id": msg.request_id, "pid": msg.proposal_id,
                "detail": detail, "source": "provider_gate", "final": False,
            })
            return

        # Auto-approve path (handshake disabled): legacy facility-admin seam.
        if not approve_release(facility, msg.item, msg.quantity):
            ctx.logger.info(f"[{facility}] Release denied by facility admin for leg {msg.proposal_id}.")
            await ctx.send(sender, TransferReject(
                request_id=msg.request_id, proposal_id=msg.proposal_id,
                rejected_by=facility, reason="facility admin denied release"))
            return

        await _send_leg_accept(ctx, facility, sender, msg.request_id, msg.proposal_id,
                               msg.item, msg.quantity, msg.to_facility,
                               msg.leg_index, msg.leg_count)

    @agent.on_interval(period=1.0)
    async def release_poll(ctx: Context):
        """Tap-2 plumbing: drain this facility's release decisions (UI taps via
        the Redis queue) and auto-deny any held leg past its deadline."""
        if not PROVIDER_APPROVAL:
            return
        # Decisions tapped in the UI arrive on a per-facility Redis list.
        try:
            import dashboard_bus
            dec = dashboard_bus.pop_release_decision(facility)
            while dec:
                await resume_release_decision(ctx, dec.get("pid"), dec.get("decision", "deny"))
                dec = dashboard_bus.pop_release_decision(facility)
        except Exception:
            pass  # Redis / dashboard_bus unavailable (offline harness) — fine.
        # Watchdog: auto-deny this facility's held legs that timed out.
        nowt = time.monotonic()
        for pid, p in list(PENDING_RELEASES.items()):
            if p["facility"] == facility and nowt > p["deadline"]:
                await resume_release_decision(ctx, pid, "timeout")


def _maybe_exit(ctx: Context):
    if EXIT_WHEN_DONE:
        ctx.logger.info("BAYMAX_EXIT_WHEN_DONE set — shutting down after the demo run.")
        os._exit(0)


# ---------------------------------------------------------------------------
# Bureau demo: run all three agents in one process for local verification.
# ---------------------------------------------------------------------------

def run_bureau_demo():
    """Build the 3-agent Bureau, wire handlers, kick off one negotiation on
    startup, and run. Invoked only from `if __name__ == "__main__"`.

    Scenario is chosen by env:
      BAYMAX_ITEM   — saline (full-cover), IV fluids (split), sutures (no-offer)
      BAYMAX_NEED   — override the need to force the partial/insufficient case
    """
    front = build_hospital_agent("Hospital A")
    hospital_b = build_hospital_agent("Hospital B")
    hospital_c = build_hospital_agent("Hospital C")

    attach_front_handlers(front)
    attach_hospital_handlers(hospital_b, "Hospital B")
    attach_hospital_handlers(hospital_c, "Hospital C")

    @front.on_event("startup")
    async def _kickoff(ctx: Context):
        item = os.getenv("BAYMAX_ITEM", "IV fluids")
        need_override = os.getenv("BAYMAX_NEED")
        quantity_needed = int(need_override) if need_override else None
        msg = f"=== Baymax demo: simulating a shortfall of {item} at {REQUESTER}"
        if quantity_needed is not None:
            msg += f" (need override = {quantity_needed})"
        ctx.logger.info(msg + " ===")
        req_id = await start_negotiation(ctx, item, quantity_needed=quantity_needed)
        # Bureau demo: auto-approve at the gate.
        await asyncio.sleep(OFFER_TIMEOUT_S + 1)

    # Auto-approve the admin gate for the Bureau demo (no chat user present).
    # resume_after_admin_decision handles idempotency; do NOT set decided=True here.
    @front.on_interval(period=1.0)
    async def _auto_approve(ctx: Context):
        for req_id, neg in list(NEGOTIATIONS.items()):
            if (not neg.get("done") and not neg.get("decided")
                    and neg.get("state") == NegotiationState.AWAITING_APPROVAL):
                await resume_after_admin_decision(ctx, req_id, "approve")

    bureau = Bureau()
    bureau.add(front)
    bureau.add(hospital_b)
    bureau.add(hospital_c)
    bureau.run()


if __name__ == "__main__":
    run_bureau_demo()
