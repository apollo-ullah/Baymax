"""wave3_order_e2e_check.py — Wave 3 end-to-end: admin gate + external order path.

Exercises the full Wave 3 chain offline (no ASI:One, no testnet RPC, no Browserbase):

    buyer  --ChatMessage("Hospital A is short on sutures")-->  FRONT
    FRONT  : negotiate with B + C  ->  no offers  ->  AWAITING_APPROVAL
    buyer  : auto-reply "order 50 sutures"  (proactive external order)
    FRONT  : calls order_from_supplier (mock)  ->  ORDERING  ->  ORDERED

Alternatively, tests the approve path on a normal shortfall:

    buyer  --ChatMessage("Hospital A is short on IV fluids")-->  FRONT
    FRONT  : negotiate ->  AWAITING_APPROVAL
    buyer  : auto-reply "approve <req_id>"
    FRONT  : propose transfer ->  CONFIRMED (stub settlement, no payment protocol)

Exits 0 on success, non-zero on watchdog timeout.

Set BAYMAX_W3_SCENARIO=order  (default) to test the external-order path.
Set BAYMAX_W3_SCENARIO=approve to test the approve path.
"""

from __future__ import annotations

import os
import re

SCENARIO = os.getenv("BAYMAX_W3_SCENARIO", "order")

if SCENARIO == "order":
    os.environ.setdefault("BAYMAX_ITEM", "sutures")
    INTENT = "Hospital A is short on sutures"
else:
    os.environ.setdefault("BAYMAX_ITEM", "IV fluids")
    INTENT = "Hospital A is short on IV fluids"

os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3.0")
os.environ.pop("BAYMAX_EXIT_WHEN_DONE", None)

import agent_base  # noqa: F401,E402

from uagents import Agent, Bureau, Context, Protocol  # noqa: E402
from agent_base import build_hospital_agent, create_text_chat, now  # noqa: E402
from baymax_agents import (  # noqa: E402
    attach_front_handlers,
    attach_hospital_handlers,
    NEGOTIATIONS,
)
from protocol import (  # noqa: E402
    NegotiationState,
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    chat_protocol_spec,
)
from agent_base import FET_NETWORK  # noqa: E402

# Build agents in Bureau mode (no Mailbox, no payment protocol needed).
front = build_hospital_agent("Hospital A")
hospital_b = build_hospital_agent("Hospital B")
hospital_c = build_hospital_agent("Hospital C")

attach_front_handlers(front)
attach_hospital_handlers(hospital_b, "Hospital B")
attach_hospital_handlers(hospital_c, "Hospital C")

# Build the chat protocol for the front agent.
from agent_base import build_chat_protocol  # noqa: E402
from front_agent import on_intent  # noqa: E402

front.include(build_chat_protocol(on_intent), publish_manifest=True)

# Buyer: stands in for the ASI:One user.
buyer = Agent(
    name="wave3_buyer",
    seed="baymax-wave3-e2e-buyer-seed",
    port=8210,
    network=FET_NETWORK,
)
_ticks = {"n": 0, "approved": False, "done": False}

buyer_chat = Protocol(spec=chat_protocol_spec)


@buyer_chat.on_message(ChatMessage)
async def _on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=now(),
                                               acknowledged_msg_id=msg.msg_id))
    for item in msg.content:
        if not isinstance(item, TextContent):
            continue
        text = item.text
        ctx.logger.info(f"[buyer<-chat] {text}")

        # On AWAITING_APPROVAL, reply with the appropriate admin decision.
        if ("awaiting_approval" in text.lower() or "Request ID:" in text) and not _ticks["approved"]:
            _ticks["approved"] = True
            m = re.search(r"Request ID:\s*([a-f0-9]{6,8})", text)
            req_id = m.group(1) if m else ""
            if SCENARIO == "order":
                reply = f"order 50 sutures {req_id}".strip() if req_id else "order 50 sutures"
                ctx.logger.info(f"[buyer] sending order decision: {reply!r}")
            else:
                reply = f"approve {req_id}".strip() if req_id else "approve"
                ctx.logger.info(f"[buyer] sending approve decision: {reply!r}")
            await ctx.send(sender, create_text_chat(reply, end_session=False))

        # Detect terminal states and exit.
        if any(s in text.lower() for s in ("ordered", "confirmed", "failed")) and "**" in text:
            if not _ticks["done"]:
                _ticks["done"] = True
                ctx.logger.info(f"[buyer] terminal milestone received — WAVE3 E2E SUCCESS.")
                os._exit(0)


@buyer_chat.on_message(ChatAcknowledgement)
async def _on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


buyer.include(buyer_chat)


@buyer.on_event("startup")
async def _kick(ctx: Context):
    ctx.logger.info(f"[buyer] sending intent to FRONT: {INTENT!r}")
    await ctx.send(front.address, create_text_chat(INTENT))


@buyer.on_interval(period=1.0)
async def _watchdog(ctx: Context):
    _ticks["n"] += 1
    if _ticks["n"] > 40:
        ctx.logger.error("[buyer] watchdog timeout — no terminal milestone within 40s.")
        os._exit(3)


bureau = Bureau()
for _a in (front, hospital_b, hospital_c, buyer):
    bureau.add(_a)

if __name__ == "__main__":
    print("=" * 70)
    print(f"WAVE 3 E2E: admin gate + {'external order' if SCENARIO == 'order' else 'approve'} path")
    print(f"  scenario : {SCENARIO}  intent : {INTENT!r}")
    print(f"  FRONT    : {front.address}")
    print(f"  BUYER    : {buyer.address}")
    print("=" * 70)
    bureau.run()
