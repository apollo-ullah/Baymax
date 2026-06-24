"""wave3_order_e2e_check.py — Wave 3 end-to-end proof (offline): the admin
approval gate + external supplier order, settled over the Payment Protocol.

    buyer  --ChatMessage("Hospital A is short on IV fluids")--> FRONT
    FRONT  : negotiate B+C -> rank -> AWAITING_APPROVAL (narrates decision)
    buyer  --ChatMessage("order <req_id>")---------------------> FRONT   (admin decides)
    FRONT  : order_from_supplier (mock) -> settle_order -> RequestPayment
    buyer  --CommitPayment(tx)---------------------------------> FRONT
    FRONT  : verify (skipped) -> CompletePayment -> ORDERED      => SUCCESS

FRONT is built via run_front.build_agent() (the real deployment path), so this
also proves the order settlement hook (register_order_settlement_hook ->
settle_order_via_payment_protocol) is registered and fires the Payment Protocol
the instant the admin chooses to buy externally. On-chain verification is stubbed
(PAYMENT_VERIFY_ONCHAIN=false) since the sandbox has no ASI:One wallet or live RPC.

Env is set BEFORE importing any agent module (import-time reads). The admin reply
carries the narrated Request ID when present (robust against the ASI:One echo
loop), falling back to a bare "order". Exits 0 on success, non-zero on timeout.
"""

from __future__ import annotations

import os
import re

os.environ["PAYMENT_VERIFY_ONCHAIN"] = "false"     # no live RPC in the sandbox
os.environ["BAYMAX_REDIS"] = "0"                    # deterministic mock inventory
os.environ["BAYMAX_SPARSE_NARRATION"] = "0"        # see AWAITING_APPROVAL narration
os.environ.pop("BAYMAX_EXIT_WHEN_DONE", None)      # buyer drives exit (pay first)
os.environ.pop("BAYMAX_BROWSERBASE", None)         # force mock path; this harness is offline
os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3.0")
os.environ.setdefault("BAYMAX_APPROVAL_TIMEOUT", "60")

import agent_base  # noqa: F401,E402  (installs the Python 3.14 event loop)

from uagents import Agent, Bureau, Context, Protocol  # noqa: E402

import run_front  # noqa: E402  (real construction path; registers both hooks)
from agent_base import build_hospital_agent, create_text_chat, now  # noqa: E402
from baymax_agents import attach_hospital_handlers  # noqa: E402
from protocol import (  # noqa: E402
    ChatMessage, ChatAcknowledgement, TextContent, chat_protocol_spec,
    payment_protocol_spec, RequestPayment, CommitPayment, CompletePayment,
    CancelPayment,
)

INTENT = os.getenv("BAYMAX_E2E_INTENT", "Hospital A is short on IV fluids")

front, front_wallet = run_front.build_agent()
hospital_b = build_hospital_agent("Hospital B")
hospital_c = build_hospital_agent("Hospital C")
attach_hospital_handlers(hospital_b, "Hospital B")
attach_hospital_handlers(hospital_c, "Hospital C")

buyer = Agent(name="asi_one_user", seed="baymax-wave3-e2e-buyer-seed",
              port=8300, network="testnet")
_state = {"ordered": False, "ticks": 0}

buyer_chat = Protocol(spec=chat_protocol_spec)


@buyer_chat.on_message(ChatMessage)
async def _buyer_on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=now(),
                                               acknowledged_msg_id=msg.msg_id))
    for item in msg.content:
        if not isinstance(item, TextContent):
            continue
        ctx.logger.info(f"[buyer<-chat] {item.text}")
        # At the admin gate, choose to ORDER externally (once). Carry the narrated
        # Request ID when present so the decision routes even if the front layer
        # can't otherwise correlate it to this sender.
        if ("awaiting_approval" in item.text.lower() or "Request ID:" in item.text) \
                and not _state["ordered"]:
            _state["ordered"] = True
            m = re.search(r"Request ID:\s*([a-f0-9]{6,8})", item.text)
            req_id = m.group(1) if m else ""
            reply = f"order {req_id}".strip()
            ctx.logger.info(f"[buyer] Admin decision -> {reply!r} (purchase externally).")
            await ctx.send(sender, create_text_chat(reply, end_session=False))


@buyer_chat.on_message(ChatAcknowledgement)
async def _buyer_on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


buyer_pay = Protocol(spec=payment_protocol_spec, role="buyer")


@buyer_pay.on_message(RequestPayment)
async def _buyer_on_request(ctx: Context, sender: str, msg: RequestPayment):
    funds = msg.accepted_funds[0]
    ctx.logger.info(f"[buyer] RequestPayment: {funds.amount} {funds.currency} -> "
                    f"{msg.recipient} (ref={msg.reference}). Approving (simulated).")
    await ctx.send(sender, CommitPayment(
        funds=funds, recipient=msg.recipient,
        transaction_id="0xWAVE3ORDERTEST", reference=msg.reference))


@buyer_pay.on_message(CompletePayment)
async def _buyer_on_complete(ctx: Context, sender: str, msg: CompletePayment):
    ctx.logger.info(f"[buyer] CompletePayment tx={msg.transaction_id} — "
                    f"WAVE3 ORDER E2E SUCCESS: chat -> negotiate -> approve(order) -> "
                    f"supplier -> settle.")
    os._exit(0)


@buyer_pay.on_message(CancelPayment)
async def _buyer_on_cancel(ctx: Context, sender: str, msg: CancelPayment):
    ctx.logger.error(f"[buyer] CancelPayment: {msg.reason} — WAVE3 E2E FAIL.")
    os._exit(1)


buyer.include(buyer_chat)
buyer.include(buyer_pay)


@buyer.on_event("startup")
async def _buyer_kick(ctx: Context):
    ctx.logger.info(f"[buyer] Sending intent to FRONT {front.address}: {INTENT!r}")
    await ctx.send(front.address, create_text_chat(INTENT))


@buyer.on_interval(period=1.0)
async def _watchdog(ctx: Context):
    _state["ticks"] += 1
    if _state["ticks"] > 40:
        ctx.logger.error("[buyer] watchdog timeout — no CompletePayment within 40s.")
        os._exit(3)


bureau = Bureau()
for _a in (front, hospital_b, hospital_c, buyer):
    bureau.add(_a)


if __name__ == "__main__":
    print("=" * 70)
    print("WAVE 3 E2E: chat -> negotiate -> AWAITING_APPROVAL -> admin ORDER -> "
          "supplier -> settle")
    print(f"  FRONT  : {front.address}  (wallet {front_wallet})")
    print(f"  BUYER  : {buyer.address}")
    print(f"  intent : {INTENT!r}   PAYMENT_VERIFY_ONCHAIN=false (sandbox)")
    print("=" * 70)
    bureau.run()
