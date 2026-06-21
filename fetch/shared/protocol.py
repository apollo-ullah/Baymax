"""
Agent negotiation + settlement protocol (PRD Section 10).

The wire contract between hospital uAgents. Both the requesting agent and the
offering agents import these exact models, so the negotiation is real
message-passing, not a scripted hand-off. Lock this before feature code.

Negotiation:   SupplyRequest -> SupplyOffer -> TransferProposal -> TransferAccept/Reject
Settlement:    RequestPayment -> CommitPayment/RejectPayment -> CompletePayment/CancelPayment
"""

from enum import Enum
from typing import Optional

from uagents import Model


# --- Negotiation messages -------------------------------------------------

class SupplyRequest(Model):
    """Broadcast on shortfall."""
    item: str
    quantity_needed: int
    urgency: str           # e.g. "critical" | "high" | "normal"
    needed_by: str         # ISO timestamp
    requester: str         # hospital id


class SupplyOffer(Model):
    """Response from a surplus facility."""
    item: str
    quantity_available: int
    distance: float        # km
    eta: str               # ISO timestamp / duration
    expiry: str            # ISO timestamp of the offered stock
    offerer: str           # hospital id


class TransferProposal(Model):
    """Requester selects an offer (or composition) and proposes the move."""
    item: str
    quantity: int
    from_: str             # hospital id  (from is reserved; keep the underscore)
    to: str                # hospital id
    eta: str


class TransferAccept(Model):
    item: str
    quantity: int
    from_: str
    to: str


class TransferReject(Model):
    item: str
    from_: str
    to: str
    reason: Optional[str] = None


# --- Settlement messages (Fetch Payment Protocol) -------------------------

class RequestPayment(Model):
    transfer_ref: str
    amount: float          # testnet FET
    payer: str
    payee: str


class CommitPayment(Model):
    transfer_ref: str


class RejectPayment(Model):
    transfer_ref: str
    reason: Optional[str] = None


class CompletePayment(Model):
    transfer_ref: str
    tx_hash: str


class CancelPayment(Model):
    transfer_ref: str
    reason: Optional[str] = None


# --- Agent state machine (PRD Section 10) ---------------------------------

class AgentState(str, Enum):
    IDLE = "idle"
    SHORTFALL_DETECTED = "shortfall_detected"
    REQUESTING = "requesting"
    COLLECTING_OFFERS = "collecting_offers"
    EVALUATING = "evaluating"
    RE_PLANNING = "re_planning"      # no single offer satisfies the need
    PROPOSING = "proposing"
    SETTLING = "settling"
    CONFIRMED = "confirmed"
