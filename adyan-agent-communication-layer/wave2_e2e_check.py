"""wave2_e2e_check.py — Wave 2 end-to-end integration proof (offline).

Builds the FULLY WIRED system in one Bureau and drives the entire chain from a
natural-language chat intent through to a completed Payment Protocol settlement —
proving the Wave 2 seam (register_settlement_hook -> settle_transfer delegation)
end to end. On-chain verification is stubbed (PAYMENT_VERIFY_ONCHAIN=false) since
the sandbox has no ASI:One wallet or live testnet RPC.

    buyer  --ChatMessage("Hospital A is short on IV fluids")-->  FRONT
    FRONT  : negotiate with B + C  ->  split 150 + 50  ->  CONFIRMED
    FRONT.settle_transfer  --(hook)-->  settlement.settle_via_payment_protocol
    FRONT  --RequestPayment----------->  buyer
    buyer  --CommitPayment(tx)-------->  FRONT
    FRONT  : verify (skipped) -> CompletePayment --> buyer   => E2E SUCCESS

The FRONT is built via run_front.build_agent() — the REAL deployment path — so
this also proves run_front registers the settlement hook and carries both the
Chat and Payment protocols. Exits 0 on success, non-zero on cancel/timeout.
"""

from __future__ import annotations

import os

# Offline test config: skip the on-chain query, let the BUYER drive process exit,
# keep the offer window short. Must be set before importing the agent modules.
os.environ["PAYMENT_VERIFY_ONCHAIN"] = "false"
os.environ.pop("BAYMAX_EXIT_WHEN_DONE", None)  # don't exit at CONFIRMED; pay first
os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3.0")

import agent_base  # noqa: F401,E402  (installs the Python 3.14 event loop first)

from uagents import Agent, Bureau, Context, Protocol  # noqa: E402

import run_front  # noqa: E402  (real deployment construction path for FRONT)
from agent_base import build_hospital_agent, create_text_chat, now  # noqa: E402
from baymax_agents import attach_hospital_handlers  # noqa: E402
from protocol import (  # noqa: E402
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    chat_protocol_spec,
    payment_protocol_spec,
    RequestPayment,
    CommitPayment,
    CompletePayment,
    CancelPayment,
)

INTENT = os.getenv("BAYMAX_E2E_INTENT", "Hospital A is short on IV fluids")

# --- FRONT (Hospital A): chat + payment(seller) + negotiation + settlement hook.
#     run_front.build_agent() is the real deployment path: it registers the
#     settlement hook on the negotiation core and the FET recipient wallet.
front, front_wallet = run_front.build_agent()

# --- Surplus facilities B and C (same seeds => addresses FRONT broadcasts to).
hospital_b = build_hospital_agent("Hospital B")
hospital_c = build_hospital_agent("Hospital C")
attach_hospital_handlers(hospital_b, "Hospital B")
attach_hospital_handlers(hospital_c, "Hospital C")

# --- BUYER: stands in for the ASI:One user (chat sender + the payer wallet).
buyer = Agent(name="asi_one_user", seed="baymax-wave2-e2e-buyer-seed",
              port=8200, network="testnet")

_ticks = {"n": 0}

# Buyer chat side: receive + ack FRONT's narration milestones.
buyer_chat = Protocol(spec=chat_protocol_spec)


@buyer_chat.on_message(ChatMessage)
async def _buyer_on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=now(),
                                               acknowledged_msg_id=msg.msg_id))
    for item in msg.content:
        if isinstance(item, TextContent):
            ctx.logger.info(f"[buyer<-chat] {item.text}")


@buyer_chat.on_message(ChatAcknowledgement)
async def _buyer_on_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


# Buyer payment side (role="buyer"): approve RequestPayment, react to the result.
buyer_pay = Protocol(spec=payment_protocol_spec, role="buyer")


@buyer_pay.on_message(RequestPayment)
async def _buyer_on_request(ctx: Context, sender: str, msg: RequestPayment):
    funds = msg.accepted_funds[0]
    ctx.logger.info(f"[buyer] RequestPayment: pay {funds.amount} {funds.currency} "
                    f"-> {msg.recipient} (ref={msg.reference}). Approving (simulated).")
    await ctx.send(sender, CommitPayment(
        funds=funds, recipient=msg.recipient,
        transaction_id="0xWAVE2E2ETESTPLACEHOLDER", reference=msg.reference,
    ))


@buyer_pay.on_message(CompletePayment)
async def _buyer_on_complete(ctx: Context, sender: str, msg: CompletePayment):
    ctx.logger.info(f"[buyer] CompletePayment tx={msg.transaction_id} — "
                    f"WAVE2 E2E SUCCESS: chat -> negotiate -> settle -> pay complete.")
    os._exit(0)


@buyer_pay.on_message(CancelPayment)
async def _buyer_on_cancel(ctx: Context, sender: str, msg: CancelPayment):
    ctx.logger.error(f"[buyer] CancelPayment: {msg.reason} — WAVE2 E2E FAIL.")
    os._exit(1)


buyer.include(buyer_chat)
buyer.include(buyer_pay)


@buyer.on_event("startup")
async def _buyer_kick(ctx: Context):
    ctx.logger.info(f"[buyer] Sending intent to FRONT {front.address}: {INTENT!r}")
    await ctx.send(front.address, create_text_chat(INTENT))


@buyer.on_interval(period=1.0)
async def _watchdog(ctx: Context):
    _ticks["n"] += 1
    if _ticks["n"] > 25:
        ctx.logger.error("[buyer] watchdog timeout — no CompletePayment within 25s.")
        os._exit(3)


bureau = Bureau()
for _a in (front, hospital_b, hospital_c, buyer):
    bureau.add(_a)


if __name__ == "__main__":
    print("=" * 70)
    print("WAVE 2 E2E: chat -> negotiate -> settle -> Payment Protocol handshake")
    print(f"  FRONT  : {front.address}  (wallet {front_wallet})")
    print(f"  BUYER  : {buyer.address}")
    print(f"  intent : {INTENT!r}   PAYMENT_VERIFY_ONCHAIN=false (sandbox)")
    print("=" * 70)
    bureau.run()
