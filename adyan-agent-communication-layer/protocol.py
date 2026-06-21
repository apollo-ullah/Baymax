"""protocol.py — FROZEN Wave 0 contract for the Baymax agent network.

This module is the SINGLE SOURCE OF TRUTH for every cross-agent message model
and the negotiation state machine. All Wave 1 streams (FRONT, NEGOTIATE, PAY,
SHIP) import from here and must NOT redefine these models elsewhere. Changing a
field after Wave 0 is a contract change and must be coordinated across every
stream (the brief's "zero model drift" rule).

Three categories live here:

  1. Official Fetch protocol surfaces, RE-EXPORTED unchanged
     ------------------------------------------------------
     The Chat Protocol and Payment Protocol models are matched by *schema
     digest* across ASI:One and Agentverse. We must use Fetch's exact classes,
     never local copies, or discoverability/compatibility breaks. We re-export
     them so every stream has one import surface.

  2. Baymax negotiation messages (PRD §10) — defined here
     -------------------------------------------------------
     SupplyRequest, SupplyOffer, TransferProposal, TransferAccept, TransferReject.

  3. The negotiation state machine (PRD §10)
     ---------------------------------------
     NegotiationState + Urgency enums.

Two seams are deliberately NOT wire messages here; they live in interfaces.py:
  * inventory state -> interfaces.get_inventory  (Redis seam, Workstream C)
  * offer ranking   -> interfaces.rank_offers    (Claude seam, Workstream B)
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from uagents import Model

# ---------------------------------------------------------------------------
# 1. Official Fetch protocol surfaces — RE-EXPORTED UNCHANGED.
#    Verified against installed uagents-core 0.4.7:
#      chat_protocol_spec    = AgentChatProtocol   v0.3.0
#      payment_protocol_spec = AgentPaymentProtocol v0.1.0
#    NEVER redefine these locally — ASI:One/Agentverse match them by schema
#    digest, so a local copy would be a different, incompatible protocol.
# ---------------------------------------------------------------------------
from uagents_core.contrib.protocols.chat import (  # noqa: F401  (re-exported)
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    EndSessionContent,
    StartSessionContent,
    MetadataContent,
    ResourceContent,
    Resource,
    StartStreamContent,
    EndStreamContent,
    AgentContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.payment import (  # noqa: F401  (re-exported)
    Funds,
    RequestPayment,
    CommitPayment,
    CompletePayment,
    CancelPayment,
    RejectPayment,
    payment_protocol_spec,
)


# ---------------------------------------------------------------------------
# 3. Negotiation state machine (PRD §10)
# ---------------------------------------------------------------------------

class NegotiationState(str, Enum):
    """Lifecycle of a single shortfall resolution (PRD §10).

    idle -> shortfall_detected -> requesting -> collecting_offers ->
    evaluating -> proposing -> settling -> confirmed,
    with re_planning when no single offer satisfies the need, and the terminal
    failed when the network cannot cover the shortfall at all.
    """

    IDLE = "idle"
    SHORTFALL_DETECTED = "shortfall_detected"
    REQUESTING = "requesting"
    COLLECTING_OFFERS = "collecting_offers"
    EVALUATING = "evaluating"
    RE_PLANNING = "re_planning"
    PROPOSING = "proposing"
    SETTLING = "settling"
    CONFIRMED = "confirmed"
    FAILED = "failed"          # terminal: no/insufficient offers (extension)


class Urgency(str, Enum):
    ROUTINE = "routine"
    URGENT = "urgent"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# 2. Negotiation wire messages (PRD §10)
#    Each is a uagents.Model (pydantic-backed). Both ends of every exchange
#    import the SAME class from here so messages route by matching schema.
#    NOTE: 'from' is a Python keyword, so transfer source/destination use
#    from_facility / to_facility.
# ---------------------------------------------------------------------------

class SupplyRequest(Model):
    """Broadcast by the requesting facility on a detected shortfall."""

    request_id: str
    requester: str                       # human-readable facility name
    item: str
    quantity_needed: int
    urgency: Urgency = Urgency.URGENT
    needed_by: Optional[str] = None      # ISO-8601 deadline, or None = ASAP


class SupplyOffer(Model):
    """A surplus facility's response to a SupplyRequest. can_offer=False is an
    explicit decline (no spare capacity for this item)."""

    request_id: str
    offerer: str                         # human-readable facility name
    item: str
    quantity_available: int              # amount this facility can spare now
    distance_km: float = 0.0
    eta_minutes: int = 0
    expiry: Optional[str] = None         # ISO date of the offered stock's nearest expiry
    can_offer: bool = True               # False => decline


class TransferProposal(Model):
    """One leg of a (possibly split) transfer. The requester sends one proposal
    per chosen offerer; leg_index/leg_count describe its place in the split so
    a facility knows it is part of a multi-facility resolution."""

    request_id: str
    proposal_id: str
    item: str
    quantity: int
    from_facility: str                   # offerer (source) facility name
    to_facility: str                     # requester (destination) facility name
    eta_minutes: int = 0
    leg_index: int = 0                   # 0-based position within the split
    leg_count: int = 1                   # total legs in the resolution


class TransferAccept(Model):
    """An offerer confirms it will fulfil a proposed transfer leg."""

    request_id: str
    proposal_id: str
    accepted_by: str                     # offerer facility name
    note: Optional[str] = None


class TransferReject(Model):
    """An offerer declines a proposed transfer leg (e.g. stock changed)."""

    request_id: str
    proposal_id: str
    rejected_by: str                     # offerer facility name
    reason: Optional[str] = None


__all__ = [
    # Chat protocol (re-exported)
    "ChatMessage", "ChatAcknowledgement", "TextContent", "EndSessionContent",
    "StartSessionContent", "MetadataContent", "ResourceContent", "Resource",
    "StartStreamContent", "EndStreamContent", "AgentContent", "chat_protocol_spec",
    # Payment protocol (re-exported)
    "Funds", "RequestPayment", "CommitPayment", "CompletePayment",
    "CancelPayment", "RejectPayment", "payment_protocol_spec",
    # Negotiation state
    "NegotiationState", "Urgency",
    # Negotiation messages
    "SupplyRequest", "SupplyOffer", "TransferProposal",
    "TransferAccept", "TransferReject",
]
