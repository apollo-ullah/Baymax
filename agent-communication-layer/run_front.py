"""run_front.py — per-agent Mailbox runner for the FRONT agent (Hospital A).

This is the ASI:One-facing entrypoint, deployed INDEPENDENTLY (its own process +
Mailbox) alongside run_hospital_b.py / run_hospital_c.py. It builds Hospital A
EXACTLY the way front_agent.py does (it reuses front_agent.build_front_agent, so
there is a single construction path — no drift), and then layers the PAY stream's
Payment Protocol on top so a confirmed transfer can trigger a real testnet FET
RequestPayment in the same ASI:One conversation.

What this runner attaches to the FRONT agent:
  * the ASI:One Chat Protocol            (publish_manifest=True)  — via
        front_agent.build_front_agent(), which calls
        agent.include(build_chat_protocol(on_intent), publish_manifest=True).
  * the negotiation handlers             — also via build_front_agent()
        (attach_front_handlers): SupplyOffer / TransferAccept / TransferReject +
        the offer-timeout tick.
  * the PAY Payment Protocol (seller)    (publish_manifest=True)  — added HERE
        via settlement.build_payment_protocol(); registers the on_commit /
        on_reject handlers so the user's CommitPayment is verified on-chain and
        answered with CompletePayment / CancelPayment.
  * Dashboard bus wiring (optional)      — if dashboard_bus is importable (Redis
        is up), narration events are forwarded to the scan dashboard's SSE feed,
        and trigger/decision queues are polled on intervals.

Contract notes:
  * agent_base is imported FIRST (transitively, via front_agent / settlement)
    BEFORE any Agent is constructed — the hard event-loop rule. We also import
    agent_base explicitly to make the ordering obvious.
  * network is pinned to testnet inside build_hospital_agent (guardrail).
  * Both protocols are attached with agent.include(proto, publish_manifest=True).

──────────────────────────────────────────────────────────────────────────────
SETTLEMENT WIRING (Wave 2 — direct seam)
──────────────────────────────────────────────────────────────────────────────
The negotiation core ends a successful deal at baymax_agents.settle_transfer().
This runner registers the real handler on the core via
baymax_agents.register_settlement_hook(settle_via_payment_protocol), so the
instant a deal settles, settle_transfer() delegates to the Payment Protocol —
sending a RequestPayment to the ASI:One chat user (the negotiation's stored
`reply_to`) for the FINAL settled plan (post re-plan). The user's wallet replies
CommitPayment, which the Payment Protocol's on_commit handler (attached here)
verifies on-chain and answers with CompletePayment / CancelPayment. No polling,
no double-fire. Per-transfer pricing is honored via BAYMAX_PAYMENT_PER_UNIT_FET
(falls back to the flat BAYMAX_PAYMENT_AMOUNT_FET).

Run:  ./.venv/bin/python run_front.py
Then complete the one-time Agentverse Mailbox connect (see the printed banner)
and chat the agent on ASI:One.
"""

from __future__ import annotations

import os

# Mailbox runners need a longer offer window and lighter chat narration — set
# BEFORE importing baymax_agents (reads OFFER_TIMEOUT at import time).
os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "30")
os.environ.setdefault("BAYMAX_SPARSE_NARRATION", "1")

# Use the live Redis inventory backend for the real demo. Safe by default:
# redis_inventory.py falls back to the deterministic mock if Redis is unreachable,
# so this never breaks the live run. Override with BAYMAX_REDIS=0.
os.environ.setdefault("BAYMAX_REDIS", "1")

# Import agent_base FIRST so the Python 3.14 event-loop workaround is installed
# before ANY Agent is constructed. front_agent and settlement both import it too,
# but we name it explicitly to make the ordering contract obvious.
import agent_base  # noqa: F401  (side-effect: installs current event loop)

# Live ASI:One narration is rate-limited by the chat relay (429s when every
# milestone is streamed). Default to sparse narration (only the key states). Must
# be set BEFORE importing baymax_agents, which reads it at import. Override with
# BAYMAX_SPARSE_NARRATION=0.
os.environ.setdefault("BAYMAX_SPARSE_NARRATION", "1")

from agent_base import REQUESTER
from uagents import Context

# Reuse the EXACT FRONT construction path (chat protocol + negotiation handlers)
# so run_front.py and front_agent.py can never drift apart.
from front_agent import build_front_agent

# The negotiation core — so we can register the real settlement handler on it.
import baymax_agents as sp

# PAY stream: the seller-side Payment Protocol + the settlement entrypoints.
from settlement import (
    build_payment_protocol,
    register_recipient_wallet,
    settle_via_payment_protocol,
    settle_order_via_payment_protocol,
)


def build_agent():
    """Build the FRONT agent with chat + negotiation (reused) PLUS payment.

    Steps:
      1. build_front_agent()  -> Hospital A in Mailbox mode, chat protocol +
         negotiation handlers already attached (same as front_agent.py).
      2. include the PAY seller Payment Protocol (publish_manifest=True).
      3. register the agent's FET wallet so RequestPayment can name a fetch1...
         recipient from inside handlers (ctx.agent has no .wallet there).
      4. register the real settlement handlers on the negotiation core so a
         settled transfer / external order fires the Payment Protocol directly
         (no polling).
      5. wire the dashboard bus narration sink + trigger/decision polling.
    """
    agent = build_front_agent()

    # (2) Payment Protocol — the second protocol the FRONT must carry.
    payment_proto = build_payment_protocol()
    agent.include(payment_proto, publish_manifest=True)

    # (3) Record OUR FET wallet (fetch1...) as the payment recipient. Must be done
    # at construction time with the full Agent object (handlers see only an
    # AgentRepresentation without a .wallet).
    wallet_addr = register_recipient_wallet(agent)

    # (4) Wire settlement DIRECTLY: register the PAY handler on the negotiation
    # core so baymax_agents.settle_transfer() delegates to it the instant a
    # deal settles. settle_transfer passes the FINAL accepted-leg plan and the
    # chat user (reply_to) it already holds — so billing reflects exactly what
    # settled (post re-plan), with no polling and no double-fire.
    sp.register_settlement_hook(settle_via_payment_protocol)

    # (4b) Wire ORDER settlement: an admin who chooses to order externally settles
    # the purchase via the same Payment Protocol (FET) to a supplier wallet.
    sp.register_order_settlement_hook(settle_order_via_payment_protocol)

    # (5) Dashboard bus: publish every milestone for the scan dashboard, and poll
    # for dashboard-triggered ("Scan & Negotiate") negotiations. reply_to=None ->
    # the core auto-approves the trade and stub-settles (simulated); the real FET
    # path stays the ASI:One chat flow (reply_to=<chat sender>). Optional — skip
    # silently if dashboard_bus is unavailable (Redis down / lib missing).
    try:
        import dashboard_bus
    except Exception:
        dashboard_bus = None

    if dashboard_bus is not None:
        sp.register_narration_sink(dashboard_bus.publish_narration)

        @agent.on_interval(period=1.0)
        async def _poll_dashboard_trigger(ctx: Context):
            # One dashboard negotiation at a time; leave the trigger queued if busy.
            for neg in sp.NEGOTIATIONS.values():
                if neg.get("reply_to") is None and not neg.get("done"):
                    return
            trig = dashboard_bus.pop_trigger()
            if not trig:
                return
            item = trig.get("item") or os.getenv("BAYMAX_ITEM", "saline")
            requester = trig.get("requester") or REQUESTER
            qty = trig.get("quantity")
            ctx.logger.info(
                f"dashboard trigger -> start_negotiation({item!r}, "
                f"requester={requester!r}, qty={qty})")
            await sp.start_negotiation(
                ctx, item, requester=requester, quantity_needed=qty,
                reply_to=None, source="dashboard")

        @agent.on_interval(period=1.0)
        async def _poll_dashboard_crisis(ctx: Context):
            # The dashboard crisis box RPUSHes onto baymax:crisis; research the
            # crisis, pick an at-risk item, and negotiate (source="dashboard").
            if not hasattr(dashboard_bus, "pop_crisis"):
                return
            for neg in sp.NEGOTIATIONS.values():  # one dashboard run at a time
                if neg.get("reply_to") is None and not neg.get("done"):
                    return
            crisis = dashboard_bus.pop_crisis()
            if not crisis:
                return
            crisis_text = (crisis.get("crisis_text") or "").strip()
            if not crisis_text:
                return
            requester = crisis.get("requester") or REQUESTER
            region = crisis.get("region") or "san_francisco"
            ctx.logger.info(
                f"dashboard crisis -> start_crisis({crisis_text!r}, source=dashboard)")
            await sp.start_crisis(
                ctx, crisis_text, requester=requester, region=region,
                reply_to=None, source="dashboard")

        @agent.on_interval(period=0.5)
        async def _poll_dashboard_decision(ctx: Context):
            # The dashboard's Approve / Order externally / Reject buttons RPUSH onto
            # baymax:decision; resume the halted (AWAITING_APPROVAL) negotiation.
            dec = dashboard_bus.pop_decision()
            if not dec:
                return
            decision = dec.get("decision")
            if decision not in ("approve", "order", "reject"):
                return
            req_id = dec.get("req_id")
            if not req_id:  # robustness: resolve the single open dashboard gate
                req_id = next(
                    (rid for rid, n in sp.NEGOTIATIONS.items()
                     if n.get("source") == "dashboard" and not n.get("done")
                     and n.get("state") == sp.NegotiationState.AWAITING_APPROVAL),
                    None)
            if not req_id:
                return
            ctx.logger.info(f"dashboard decision: {req_id} -> {decision}")
            await sp.resume_after_admin_decision(ctx, req_id, decision)

    return agent, wallet_addr


if __name__ == "__main__":
    agent, wallet_addr = build_agent()
    print("=" * 70)
    print("Baymax FRONT agent (Hospital A) — Mailbox runner (chat + payment)")
    print(f"  name        : {agent.name}")
    print(f"  address     : {agent.address}")
    print(f"  FET wallet  : {wallet_addr}")
    print(f"  network     : {os.getenv('FETCH_NETWORK', 'testnet')} (TESTNET ONLY)")
    print(f"  protocols   : Chat Protocol + Payment Protocol (both publish_manifest)")
    print("-" * 70)
    print("Manual ASI:One steps (one-time, needs a browser login):")
    print("  1. Run this file:            ./.venv/bin/python run_front.py")
    print("  2. Open the Agent Inspector URL printed below by uAgents.")
    print("  3. In Agentverse: Connect -> Mailbox -> Finish (one-time).")
    print("  4. Find the agent on ASI:One (https://asi1.ai) and chat it, e.g.")
    print("       \"Hospital A is short on IV fluids\"")
    print("  5. Watch the negotiation milestones stream back into the chat; on")
    print("     AWAITING_APPROVAL reply 'approve', 'order' (full shortfall), or 'reject'.")
    print("     Or skip the shortfall entirely: \"order 500 saline\" / \"wildfires near Hospital A\" / \"ingest data\".")
    print("  6. On CONFIRMED a RequestPayment is sent for the user to approve + sign.")
    print("=" * 70)
    agent.run()
