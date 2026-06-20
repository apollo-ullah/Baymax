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

Contract notes:
  * agent_base is imported FIRST (transitively, via front_agent / settlement)
    BEFORE any Agent is constructed — the hard event-loop rule. We also import
    agent_base explicitly to make the ordering obvious.
  * network is pinned to testnet inside build_hospital_agent (guardrail).
  * Both protocols are attached with agent.include(proto, publish_manifest=True).

──────────────────────────────────────────────────────────────────────────────
SETTLEMENT WIRING (Wave 2 — direct seam)
──────────────────────────────────────────────────────────────────────────────
The negotiation core ends a successful deal at stockpile_agents.settle_transfer().
This runner registers the real handler on the core via
stockpile_agents.register_settlement_hook(settle_via_payment_protocol), so the
instant a deal settles, settle_transfer() delegates to the Payment Protocol —
sending a RequestPayment to the ASI:One chat user (the negotiation's stored
`reply_to`) for the FINAL settled plan (post re-plan). The user's wallet replies
CommitPayment, which the Payment Protocol's on_commit handler (attached here)
verifies on-chain and answers with CompletePayment / CancelPayment. No polling,
no double-fire. Per-transfer pricing is honored via STOCKPILE_PAYMENT_PER_UNIT_FET
(falls back to the flat STOCKPILE_PAYMENT_AMOUNT_FET).

Run:  ./.venv/bin/python run_front.py
Then complete the one-time Agentverse Mailbox connect (see the printed banner)
and chat the agent on ASI:One.
"""

from __future__ import annotations

import os

# Mailbox runners need a longer offer window and lighter chat narration — set
# BEFORE importing stockpile_agents (reads OFFER_TIMEOUT at import time).
os.environ.setdefault("STOCKPILE_OFFER_TIMEOUT", "30")
os.environ.setdefault("STOCKPILE_SPARSE_NARRATION", "1")

# Import agent_base FIRST so the Python 3.14 event-loop workaround is installed
# before ANY Agent is constructed. front_agent and settlement both import it too,
# but we name it explicitly to make the ordering contract obvious.
import agent_base  # noqa: F401  (side-effect: installs current event loop)

# Live ASI:One narration is rate-limited by the chat relay (429s when every
# milestone is streamed). Default to sparse narration (only the key states). Must
# be set BEFORE importing stockpile_agents, which reads it at import. Override with
# STOCKPILE_SPARSE_NARRATION=0.
os.environ.setdefault("STOCKPILE_SPARSE_NARRATION", "1")

# Reuse the EXACT FRONT construction path (chat protocol + negotiation handlers)
# so run_front.py and front_agent.py can never drift apart.
from front_agent import build_front_agent

# The negotiation core — so we can register the real settlement handler on it.
import stockpile_agents as sp

# PAY stream: the seller-side Payment Protocol + the settlement entrypoint.
from settlement import (
    build_payment_protocol,
    register_recipient_wallet,
    settle_via_payment_protocol,
)


def build_agent():
    """Build the FRONT agent with chat + negotiation (reused) PLUS payment.

    Steps:
      1. build_front_agent()  -> Hospital A in Mailbox mode, chat protocol +
         negotiation handlers already attached (same as front_agent.py).
      2. include the PAY seller Payment Protocol (publish_manifest=True).
      3. register the agent's FET wallet so RequestPayment can name a fetch1...
         recipient from inside handlers (ctx.agent has no .wallet there).
      4. register the real settlement handler on the negotiation core so a
         settled transfer fires the Payment Protocol directly (no polling).
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
    # core so stockpile_agents.settle_transfer() delegates to it the instant a
    # deal settles. settle_transfer passes the FINAL accepted-leg plan and the
    # chat user (reply_to) it already holds — so billing reflects exactly what
    # settled (post re-plan), with no polling and no double-fire.
    sp.register_settlement_hook(settle_via_payment_protocol)

    return agent, wallet_addr


if __name__ == "__main__":
    agent, wallet_addr = build_agent()
    print("=" * 70)
    print("STOCKPILE FRONT agent (Hospital A) — Mailbox runner (chat + payment)")
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
    print("     CONFIRMED a RequestPayment is sent for the user to approve + sign.")
    print("=" * 70)
    agent.run()
