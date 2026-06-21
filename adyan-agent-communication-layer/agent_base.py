"""agent_base.py — shared building blocks for the Stockpile hospital agents.

Provides, for every Wave 1 stream:
  * the Python 3.14 event-loop workaround (uagents 0.25.2 needs a current loop
    before an Agent is constructed),
  * the facility registry (names, ports, seed env vars) and seed/address
    derivation so any agent can address any other in BOTH Bureau (one process)
    and Mailbox (separate processes) modes,
  * a testnet network guardrail,
  * the Phase-0 Chat Protocol shell (build_chat_protocol) for the FRONT stream,
  * chat helpers (create_text_chat, now) and the hospital Agent factory.

Frozen Wave 0 surface: streams import from here; they should not edit the
config/derivation logic (that would shift agent addresses out from under the
other streams).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

# Load `.env` before reading seed/network env vars so all runners share the
# same deterministic addresses (SURPLUS_ADDRESSES, etc.).
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# Python 3.14 removed the implicit current event loop; uagents 0.25.2 calls
# asyncio.get_event_loop() in Agent.__init__. Establish one before any Agent is
# built. (Same workaround as the Phase-0 hello_world_agent.py.)
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
from datetime import datetime, timezone
from typing import Callable, Awaitable, Optional
from uuid import uuid4

from uagents import Agent, Context, Protocol  # noqa: E402  (after loop setup)
from uagents_core.identity import Identity

from protocol import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    EndSessionContent,
    StartSessionContent,
    chat_protocol_spec,
)

# ---------------------------------------------------------------------------
# Guardrail: TESTNET ONLY. Never mainnet, never real funds. uagents' Agent
# defaults to network="mainnet", so we set it explicitly everywhere — and we
# FAIL CLOSED: any FETCH_NETWORK other than "testnet" raises rather than
# silently routing agents (or funds) to mainnet.
# ---------------------------------------------------------------------------
_REQUESTED_NETWORK = os.getenv("FETCH_NETWORK", "testnet").strip().lower()
if _REQUESTED_NETWORK not in ("", "testnet"):
    raise RuntimeError(
        f"Stockpile is TESTNET ONLY — refusing FETCH_NETWORK={_REQUESTED_NETWORK!r}. "
        f"Unset it or set FETCH_NETWORK=testnet."
    )
FET_NETWORK = "testnet"

README_PATH = os.path.join(os.path.dirname(__file__), "README.md")

# ---------------------------------------------------------------------------
# Facility registry. The requester (Hospital A) is the FRONT agent that carries
# the Chat Protocol; the surplus facilities (B, C) only speak the negotiation
# Models. Ports are used in separate-process / Mailbox mode.
# ---------------------------------------------------------------------------
REQUESTER = "Hospital A"

FACILITIES = {
    "Hospital A": {"agent_name": "stockpile_front",      "port": 8001, "seed_env": "STOCKPILE_FRONT_SEED",  "role": "requester"},
    "Hospital B": {"agent_name": "stockpile_hospital_b", "port": 8002, "seed_env": "STOCKPILE_HOSP_B_SEED", "role": "surplus"},
    "Hospital C": {"agent_name": "stockpile_hospital_c", "port": 8003, "seed_env": "STOCKPILE_HOSP_C_SEED", "role": "surplus"},
}

# Deterministic dev fallback seeds so the Bureau demo runs reproducibly with no
# .env. Real deployments MUST set the *_SEED env vars (see .env.example) — these
# fallbacks are public, hence only for local testing, never for Agentverse.
_DEV_SEEDS = {f: f"stockpile-dev-seed-{cfg['agent_name']}" for f, cfg in FACILITIES.items()}

SURPLUS_FACILITIES = [f for f, cfg in FACILITIES.items() if cfg["role"] == "surplus"]


def seed_for(facility: str) -> str:
    """The agent seed for a facility: env var if set, else the dev fallback."""
    return os.getenv(FACILITIES[facility]["seed_env"]) or _DEV_SEEDS[facility]


def address_for(facility: str) -> str:
    """The agent address for a facility, derived from its seed. Matches the
    address an Agent(seed=...) reports (verified in Wave 0)."""
    return Identity.from_seed(seed=seed_for(facility), index=0).address


# Address books (built once; stable as long as seeds are stable).
SURPLUS_ADDRESSES = {f: address_for(f) for f in SURPLUS_FACILITIES}   # name -> address
ADDRESS_TO_FACILITY = {address_for(f): f for f in FACILITIES}          # address -> name


# ---------------------------------------------------------------------------
# Chat helpers (Chat Protocol uses timezone-aware UTC timestamps).
# ---------------------------------------------------------------------------

def now() -> datetime:
    return datetime.now(timezone.utc)


def create_text_chat(text: str, end_session: bool = False) -> ChatMessage:
    """Build a ChatMessage carrying one text block; optionally end the session."""
    content = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(timestamp=now(), msg_id=uuid4(), content=content)


IntentHandler = Callable[[Context, str, str], Awaitable[None]]


def build_chat_protocol(on_intent: IntentHandler) -> Protocol:
    """The Phase-0 Chat Protocol shell, parameterized by an intent handler.

    Implements the ASI:One handshake exactly: acknowledge every ChatMessage
    first, extract text content, then hand the text to `on_intent(ctx, sender,
    text)`. The FRONT stream supplies on_intent to parse the request and kick
    off the negotiation. Attach with agent.include(proto, publish_manifest=True).
    """
    proto = Protocol(spec=chat_protocol_spec)

    @proto.on_message(ChatMessage)
    async def _on_chat(ctx: Context, sender: str, msg: ChatMessage):
        # 1) acknowledge immediately (ASI:One expects this)
        await ctx.send(
            sender,
            ChatAcknowledgement(timestamp=now(), acknowledged_msg_id=msg.msg_id),
        )
        # 2) collect text; note session-start
        text = " ".join(
            item.text for item in msg.content if isinstance(item, TextContent)
        ).strip()
        for item in msg.content:
            if isinstance(item, StartSessionContent):
                ctx.logger.info(f"Chat session started by {sender}")
        # 3) drive the flow
        if text:
            await on_intent(ctx, sender, text)

    @proto.on_message(ChatAcknowledgement)
    async def _on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass  # read receipts available via msg.acknowledged_msg_id

    return proto


# ---------------------------------------------------------------------------
# Hospital Agent factory.
# ---------------------------------------------------------------------------

def build_hospital_agent(
    facility: str,
    *,
    mailbox: bool = False,
    publish_details: Optional[bool] = None,
) -> Agent:
    """Construct the uAgent for a facility.

    mailbox=False (default): local/Bureau mode — agents talk in-process, no
        Agentverse connection needed.
    mailbox=True: Mailbox mode — reachable through Agentverse/ASI:One without a
        public inbound endpoint (used by the SHIP stream's per-agent runners).
    network is pinned to FET_NETWORK ("testnet") in all modes (guardrail).
    """
    cfg = FACILITIES[facility]
    kwargs = {
        "name": cfg["agent_name"],
        "port": cfg["port"],
        "seed": seed_for(facility),
        "network": FET_NETWORK,
    }
    if mailbox:
        kwargs["mailbox"] = True
        kwargs["publish_agent_details"] = True if publish_details is None else publish_details
        kwargs["readme_path"] = README_PATH
    elif publish_details:
        kwargs["publish_agent_details"] = True
    return Agent(**kwargs)
