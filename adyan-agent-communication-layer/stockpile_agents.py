"""stockpile_agents.py — the canonical 3-agent supply negotiation.

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
    verification — `python stockpile_agents.py`.
  * Separate processes / Mailbox: the SHIP stream wraps each agent with
    build_hospital_agent(..., mailbox=True) in its own runner.

Clean seams for the other streams (so they don't edit this file's internals):
  * FRONT  -> calls start_negotiation(ctx, item, reply_to=<chat sender>) from a
              chat handler; set reply_to so progress + result stream back to
              ASI:One as ChatMessages (narration is automatic when reply_to is set).
  * PAY    -> replaces settle_transfer() with the real Payment Protocol handshake.
  * B/C    -> rank_offers / get_inventory stay behind interfaces.py (B/C streams).
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

# Import agent_base FIRST: it installs the Python 3.14 event-loop before any
# Agent is constructed.
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
OFFER_TIMEOUT_S = float(os.getenv("STOCKPILE_OFFER_TIMEOUT", "4.0"))
# For the one-shot Bureau demo/test: exit the process once a negotiation ends.
EXIT_WHEN_DONE = os.getenv("STOCKPILE_EXIT_WHEN_DONE", "").lower() in ("1", "true", "yes")

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
    if reply_to and (narrate or final):
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
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg["done"] or neg["state"] not in (
        NegotiationState.COLLECTING_OFFERS, NegotiationState.RE_PLANNING):
        return

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
        # The single-offer case can't cover the need: re-plan into a split using
        # every available facility (the PRD's "no single offer satisfies" moment).
        await _step(ctx, neg, NegotiationState.RE_PLANNING,
                    f"No single facility covers {neg['need']} {neg['item']}; composing a split "
                    f"({plan.total_covered}/{neg['need']} achievable, {plan.shortfall_remaining} would remain).",
                    narrate=True)

    # Propose each leg of the (possibly split) transfer.
    neg["pending"] = set()
    n = len(plan.allocations)
    await _step(ctx, neg, NegotiationState.PROPOSING,
                f"Composing transfer: {n} leg(s).", narrate=True)
    for i, al in enumerate(plan.allocations):
        pid = f"{req_id}-{i}"
        neg["pending"].add(pid)
        prop = TransferProposal(request_id=req_id, proposal_id=pid, item=neg["item"],
                                quantity=al.quantity, from_facility=al.offerer,
                                to_facility=neg["requester"], eta_minutes=al.eta_minutes,
                                leg_index=i, leg_count=n)
        await _step(ctx, neg, NegotiationState.PROPOSING,
                    f"Leg {i + 1}/{n}: move {al.quantity} {neg['item']} from {al.offerer} "
                    f"-> {neg['requester']} (~{al.eta_minutes} min).")
        await ctx.send(SURPLUS_ADDRESSES[al.offerer], prop)


async def _settle(ctx: Context, req_id: str):
    neg = NEGOTIATIONS[req_id]
    plan = neg["plan"]
    await _step(ctx, neg, NegotiationState.SETTLING,
                "All legs accepted — settling the transfer.", narrate=True)
    tx = await settle_transfer(ctx, req_id, plan)
    legs = "; ".join(f"{a.quantity} {neg['item']} from {a.offerer}" for a in plan.allocations)
    await _step(ctx, neg, NegotiationState.CONFIRMED,
                f"Transfer confirmed ({legs}) to {neg['requester']}. Settlement: {tx}.",
                narrate=True, final=True)
    neg["done"] = True
    _maybe_exit(ctx)


async def settle_transfer(ctx: Context, req_id: str, plan) -> str:
    """SETTLEMENT HOOK — Wave 0 stub.

    PAY stream replaces this with the real testnet FET Payment Protocol handshake
    (RequestPayment -> CommitPayment -> CompletePayment), surfacing the
    CommitPayment as the action in the ASI:One chat and returning the on-chain
    transaction hash. The Wave 0 stub just logs and returns a placeholder ref so
    the negotiation chain completes end to end.
    """
    ref = f"stub-settlement-{req_id}"
    ctx.logger.info(
        f"[settlement-stub] Would settle {len(plan.allocations)} leg(s) via the "
        f"Payment Protocol on {os.getenv('FETCH_NETWORK', 'testnet')} -> {ref}"
    )
    return ref


def attach_front_handlers(front):
    """Wire the requester (Hospital A) message handlers + offer-timeout tick."""

    @front.on_message(model=SupplyOffer)
    async def on_offer(ctx: Context, sender: str, msg: SupplyOffer):
        neg = NEGOTIATIONS.get(msg.request_id)
        if not neg or neg["done"]:
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
        neg["pending"].discard(msg.proposal_id)
        neg["accepts"].add(msg.proposal_id)
        total = len(neg["plan"].allocations) if neg["plan"] else 0
        await _step(ctx, neg, NegotiationState.PROPOSING,
                    f"{msg.accepted_by} accepted leg {msg.proposal_id}  "
                    f"[{len(neg['accepts'])}/{total}]")
        if not neg["pending"]:
            await _settle(ctx, msg.request_id)

    @front.on_message(model=TransferReject)
    async def on_reject(ctx: Context, sender: str, msg: TransferReject):
        neg = NEGOTIATIONS.get(msg.request_id)
        if not neg or neg["done"]:
            return
        neg["pending"].discard(msg.proposal_id)
        neg["rejects"].add(msg.proposal_id)
        await _step(ctx, neg, NegotiationState.RE_PLANNING,
                    f"{msg.rejected_by} rejected leg {msg.proposal_id} "
                    f"({msg.reason}). NEGOTIATE stream re-plans the dropped leg.", narrate=True)
        # Wave 0 baseline: if every other leg is in, settle what was accepted.
        if not neg["pending"] and neg["accepts"]:
            await _settle(ctx, msg.request_id)

    @front.on_interval(period=1.0)
    async def offer_timeout(ctx: Context):
        nowt = time.monotonic()
        for req_id, neg in list(NEGOTIATIONS.items()):
            if neg["done"]:
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
    """Wire a surplus facility's negotiation handlers."""

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
        ctx.logger.info("STOCKPILE_EXIT_WHEN_DONE set — shutting down after the demo run.")
        os._exit(0)


# ---------------------------------------------------------------------------
# Bureau: run all three agents in one process for local verification.
# ---------------------------------------------------------------------------

front = build_hospital_agent("Hospital A")
hospital_b = build_hospital_agent("Hospital B")
hospital_c = build_hospital_agent("Hospital C")

attach_front_handlers(front)
attach_hospital_handlers(hospital_b, "Hospital B")
attach_hospital_handlers(hospital_c, "Hospital C")


@front.on_event("startup")
async def _kickoff(ctx: Context):
    """Bureau demo trigger. FRONT replaces this entry point with a chat handler
    that calls start_negotiation() on an ASI:One intent."""
    item = os.getenv("STOCKPILE_ITEM", "IV fluids")
    ctx.logger.info(f"=== Stockpile demo: simulating a shortfall of {item} at {REQUESTER} ===")
    await start_negotiation(ctx, item)


bureau = Bureau()
bureau.add(front)
bureau.add(hospital_b)
bureau.add(hospital_c)


if __name__ == "__main__":
    bureau.run()
