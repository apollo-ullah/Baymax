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
  * start_negotiation(ctx, item, *, requester, quantity_needed, reply_to)
  * settle_transfer(ctx, req_id, plan)
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
"""

from __future__ import annotations

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
    distance_between,
    eta_minutes_for,
    expiry_for,
    get_inventory,
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
})
# Bounded re-plan: how many times we try to re-home a dropped (rejected) leg
# before giving up and settling what was accepted. Prevents infinite re-propose.
MAX_REPLAN_ATTEMPTS = int(os.getenv("BAYMAX_MAX_REPLANS", "3"))
# For the one-shot Bureau demo/test: exit the process once a negotiation ends.
EXIT_WHEN_DONE = os.getenv("BAYMAX_EXIT_WHEN_DONE", "").lower() in ("1", "true", "yes")

# In-process negotiation state, keyed by request_id. (Bureau runs one process;
# a multi-process deployment would move this into ctx.storage / Redis.)
NEGOTIATIONS: dict[str, dict] = {}


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


# ---------------------------------------------------------------------------
# FRONT (Hospital A) — orchestration
# ---------------------------------------------------------------------------

async def start_negotiation(ctx: Context, item: str, *, requester: str = REQUESTER,
                            quantity_needed: int | None = None,
                            reply_to: str | None = None) -> str:
    """Detect the shortfall and broadcast a SupplyRequest. Returns request_id.

    FRONT calls this from its chat handler with reply_to=<sender> so the whole
    negotiation narrates back into the ASI:One conversation.

    quantity_needed overrides the inventory-derived shortfall — used to drive the
    partial/insufficient case (need larger than the network's total spare).
    """
    inv = get_inventory(requester, item)
    need = quantity_needed if quantity_needed is not None else inv.shortfall
    req_id = uuid4().hex[:8]
    neg = NEGOTIATIONS[req_id] = {
        "item": item, "requester": requester, "need": need,
        "offers": {}, "expected": set(SURPLUS_ADDRESSES.values()),
        "plan": None, "pending": set(), "accepts": set(), "rejects": set(),
        "deadline": time.monotonic() + OFFER_TIMEOUT_S, "done": False,
        "reply_to": reply_to, "state": NegotiationState.IDLE,
        # Hardening bookkeeping ------------------------------------------------
        "evaluated": False,          # idempotence guard for _evaluate
        "settled": False,            # idempotence guard for _settle
        # committed[facility] = quantity already locked in by an accepted leg.
        "committed": {},
        # leg[pid] = {"offerer", "quantity", "eta"} for each live/settled leg.
        "leg": {},
        # rejected facilities (excluded from re-planning).
        "rejected_facilities": set(),
        "replans": 0,                # bounded re-plan attempt counter
        "covered": 0,                # running total of accepted quantity
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
    for addr in SURPLUS_ADDRESSES.values():
        await ctx.send(addr, req)
    neg["state"] = NegotiationState.COLLECTING_OFFERS
    return req_id


async def _evaluate(ctx: Context, req_id: str):
    """Rank the collected offers and propose the resulting (possibly split)
    transfer. Idempotent: only the first call for a given negotiation runs — the
    all-offers-arrived path and the offer-timeout path both call this, but the
    second is a no-op (guarded by neg["evaluated"])."""
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
        await _step(ctx, neg, NegotiationState.FAILED,
                    f"No facility can spare {neg['item']}. Shortfall of {neg['need']} unresolved — "
                    f"escalate to manual procurement.", narrate=True, final=True)
        neg["done"] = True
        _maybe_exit(ctx)
        return

    if not plan.fully_covered:
        if len(plan.allocations) > 1:
            # Multiple facilities pooled but still short: a genuine insufficiency.
            await _step(ctx, neg, NegotiationState.RE_PLANNING,
                        f"Pooled spare across {len(plan.allocations)} facilities still cannot meet "
                        f"{neg['need']} {neg['item']}: best achievable is {plan.total_covered} "
                        f"({plan.shortfall_remaining} will remain short). Proceeding to settle the "
                        f"covered amount and flagging the residual shortfall.",
                        narrate=True)
        else:
            # A single offer can't cover the need on its own.
            await _step(ctx, neg, NegotiationState.RE_PLANNING,
                        f"No single facility covers {neg['need']} {neg['item']}; composing a split "
                        f"({plan.total_covered}/{neg['need']} achievable, {plan.shortfall_remaining} would remain).",
                        narrate=True)

    # Record the planned legs and commitments, then propose each leg.
    neg["committed"] = {}
    neg["leg"] = {}
    neg["pending"] = set()
    n = len(plan.allocations)
    await _step(ctx, neg, NegotiationState.PROPOSING,
                f"Composing transfer: {n} leg(s).", narrate=True)
    for i, al in enumerate(plan.allocations):
        pid = f"{req_id}-{i}"
        await _propose_leg(ctx, neg, req_id, pid, al.offerer, al.quantity, al.eta_minutes,
                           leg_index=i, leg_count=n)


async def _propose_leg(ctx: Context, neg: dict, req_id: str, pid: str,
                       offerer: str, quantity: int, eta_minutes: int,
                       *, leg_index: int, leg_count: int):
    """Send one TransferProposal and record it as pending/committed. Used by both
    the initial composition and the leg-reject re-plan."""
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

    # Remaining spare per facility = original offer minus what it already
    # committed to via accepted/pending legs; exclude facilities that rejected.
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
        # Everything was rejected and nothing could be re-homed.
        await _step(ctx, neg, NegotiationState.FAILED,
                    f"All proposed legs were rejected and none could be re-homed. Shortfall of "
                    f"{neg['need']} {neg['item']} unresolved — escalate to manual procurement.",
                    narrate=True, final=True)
        neg["done"] = True
        _maybe_exit(ctx)


async def _settle(ctx: Context, req_id: str):
    """Settle the accepted legs. Idempotent: guarded so it can never double-fire
    even if accept/reject completion paths both reach it."""
    neg = NEGOTIATIONS[req_id]
    if neg["settled"] or neg["done"]:
        return
    neg["settled"] = True

    # Build the settled allocation list from accepted legs (re-planning means the
    # final set can differ from neg["plan"].allocations).
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

    # Live ASI:One + Payment Protocol: RequestPayment is async — do NOT send
    # CONFIRMED with end_session yet or ASI:One closes the chat before the wallet
    # UI can show the FET approval prompt. finalize_after_payment() runs on
    # CompletePayment (see settlement.py).
    if _SETTLEMENT_HOOK is not None and neg.get("reply_to"):
        from settlement import _amount_for_total  # lazy: avoids import cycle at load

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
    """Lightweight allocation carrier passed to settle_transfer().

    Mirrors interfaces.RankedPlan's `allocations` shape closely enough for the
    PAY stream (each item has .offerer/.quantity/.eta_minutes), while reflecting
    the FINAL accepted legs (after any re-planning), not the original ranked plan.
    """

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


# --- Settlement hook (Wave 2 integration seam) -----------------------------
# settle_transfer() delegates here when a handler is registered. The PAY/SHIP
# deployment registers settlement.settle_via_payment_protocol (see run_front.py)
# so a settled transfer fires a real testnet FET Payment Protocol RequestPayment
# to the ASI:One chat user. Keeping it a registered callback means this module
# never imports the settlement layer (clean one-way dependency: the deployment
# wires PAY -> the negotiation core, not the other way around).
_SETTLEMENT_HOOK = None


def register_settlement_hook(fn) -> None:
    """Register the real settlement handler.

    `fn` is an async callable fn(ctx, req_id, plan, user_address, reply_to) -> str
    returning a settlement reference / tx id. Call once at deployment wiring time.
    """
    global _SETTLEMENT_HOOK
    _SETTLEMENT_HOOK = fn


async def settle_transfer(ctx: Context, req_id: str, plan) -> str:
    """Settle a resolved transfer.

    If a settlement hook is registered (deployment) AND the negotiation has an
    ASI:One chat user to bill, delegate to it — the real testnet FET Payment
    Protocol handshake (RequestPayment -> CommitPayment -> CompletePayment),
    surfaced as the action in the chat, returning the settlement reference.

    Otherwise (local Bureau demo, or a negotiation with no chat user) return a
    stub reference so the negotiation chain still completes end to end.
    """
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
        # Idempotence: ignore duplicate offers from a facility already recorded.
        if msg.offerer in neg["offers"]:
            return
        neg["offers"][msg.offerer] = msg
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
        # Idempotence: ignore an accept for a leg we are no longer waiting on.
        if msg.proposal_id not in neg["pending"]:
            return
        neg["pending"].discard(msg.proposal_id)
        neg["accepts"].add(msg.proposal_id)
        total = len(neg["leg"])
        await _step(ctx, neg, NegotiationState.PROPOSING,
                    f"{msg.accepted_by} accepted leg {msg.proposal_id}  "
                    f"[{len(neg['accepts'])}/{total}]")
        await _maybe_finish(ctx, msg.request_id)

    @front.on_message(model=TransferReject)
    async def on_reject(ctx: Context, sender: str, msg: TransferReject):
        neg = NEGOTIATIONS.get(msg.request_id)
        if not neg or neg["done"]:
            return
        # Idempotence: ignore a reject for a leg we are no longer waiting on.
        if msg.proposal_id not in neg["pending"]:
            return
        neg["pending"].discard(msg.proposal_id)
        neg["rejects"].add(msg.proposal_id)

        leg = neg["leg"].get(msg.proposal_id, {})
        dropped_qty = leg.get("quantity", 0)
        rejecter = msg.rejected_by or leg.get("offerer")
        if rejecter:
            neg["rejected_facilities"].add(rejecter)
            # Release the committed quantity for the rejected leg so it can be
            # re-allocated elsewhere (and never re-proposed to the rejecter).
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
        # If nothing is pending now, finish (settle covered / fail if none).
        await _maybe_finish(ctx, msg.request_id)

    @front.on_interval(period=1.0)
    async def offer_timeout(ctx: Context):
        """Offer-window guard: a non-responding facility never stalls the flow.
        Once the deadline passes while still collecting, evaluate with whatever
        arrived. _evaluate is idempotent so this is safe alongside the
        all-offers-arrived path."""
        nowt = time.monotonic()
        for req_id, neg in list(NEGOTIATIONS.items()):
            if neg["done"] or neg["evaluated"]:
                continue
            if neg["state"] == NegotiationState.COLLECTING_OFFERS and nowt >= neg["deadline"]:
                await _step(ctx, neg, NegotiationState.COLLECTING_OFFERS,
                            f"Offer window closed: {len(neg['offers'])}/{len(neg['expected'])} "
                            f"responded. Evaluating with what arrived.", narrate=True)
                await _evaluate(ctx, req_id)


# ---------------------------------------------------------------------------
# SURPLUS facilities (Hospital B, C) — offer + accept/reject
# ---------------------------------------------------------------------------

def attach_hospital_handlers(agent, facility: str):
    """Wire a surplus facility's negotiation handlers.

    BAYMAX_FORCE_REJECT=<facility> makes that facility reject the FIRST
    transfer leg it is asked to fulfil (regardless of stock), to exercise the
    leg-reject re-plan path. It still makes a normal offer first, so the
    requester proposes to it and then sees the rejection."""

    force_reject = os.getenv("BAYMAX_FORCE_REJECT", "").strip()
    # Per-facility one-shot flag so the forced reject fires only once.
    _forced = {"done": False}

    @agent.on_message(model=SupplyRequest)
    async def on_request(ctx: Context, sender: str, msg: SupplyRequest):
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
            ctx.logger.info(f"[{facility}] Offering {spare} {msg.item} "
                            f"(have {inv.qty}, safety {inv.safety_threshold}).")
        else:
            offer = SupplyOffer(request_id=msg.request_id, offerer=facility,
                                item=msg.item, quantity_available=0, can_offer=False)
            ctx.logger.info(f"[{facility}] No spare {msg.item} — declining.")
        await ctx.send(sender, offer)

    @agent.on_message(model=TransferProposal)
    async def on_proposal(ctx: Context, sender: str, msg: TransferProposal):
        # Forced-reject knob: reject the first leg this facility is proposed.
        if force_reject == facility and not _forced["done"]:
            _forced["done"] = True
            ctx.logger.info(f"[{facility}] FORCING reject of leg {msg.proposal_id} "
                            f"(BAYMAX_FORCE_REJECT).")
            await ctx.send(sender, TransferReject(
                request_id=msg.request_id, proposal_id=msg.proposal_id,
                rejected_by=facility, reason="forced reject (demo)"))
            return

        inv = get_inventory(facility, msg.item)
        if inv.spare_capacity >= msg.quantity:
            ctx.logger.info(f"[{facility}] Accepting leg {msg.leg_index + 1}/{msg.leg_count}: "
                            f"{msg.quantity} {msg.item} -> {msg.to_facility}.")
            await ctx.send(sender, TransferAccept(request_id=msg.request_id,
                                                  proposal_id=msg.proposal_id, accepted_by=facility))
        else:
            ctx.logger.info(f"[{facility}] Rejecting leg {msg.proposal_id}: only "
                            f"{inv.spare_capacity} spare, asked for {msg.quantity}.")
            await ctx.send(sender, TransferReject(request_id=msg.request_id,
                                                  proposal_id=msg.proposal_id, rejected_by=facility,
                                                  reason=f"only {inv.spare_capacity} spare"))


def _maybe_exit(ctx: Context):
    if EXIT_WHEN_DONE:
        ctx.logger.info("BAYMAX_EXIT_WHEN_DONE set — shutting down after the demo run.")
        os._exit(0)


# ---------------------------------------------------------------------------
# Bureau demo: run all three agents in one process for local verification.
# Everything that constructs agents / a Bureau / kicks off a demo lives in here
# so that `import baymax_agents` has NO side effects (Task 1).
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
        """Bureau demo trigger. FRONT replaces this entry point with a chat
        handler that calls start_negotiation() on an ASI:One intent."""
        item = os.getenv("BAYMAX_ITEM", "IV fluids")
        need_override = os.getenv("BAYMAX_NEED")
        quantity_needed = int(need_override) if need_override else None
        msg = f"=== Baymax demo: simulating a shortfall of {item} at {REQUESTER}"
        if quantity_needed is not None:
            msg += f" (need override = {quantity_needed})"
        ctx.logger.info(msg + " ===")
        await start_negotiation(ctx, item, quantity_needed=quantity_needed)

    bureau = Bureau()
    bureau.add(front)
    bureau.add(hospital_b)
    bureau.add(hospital_c)
    bureau.run()


if __name__ == "__main__":
    run_bureau_demo()
