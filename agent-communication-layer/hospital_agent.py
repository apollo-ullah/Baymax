"""hospital_agent.py — a surplus facility (Hospital B / C) as a Claude agent.

This is the heart of the Anthropic multi-agent rewrite: each surplus hospital is
its OWN reasoning agent. When the FRONT/coordinator broadcasts a need, every
surplus facility's agent looks at its own stock, decides how much it is willing
to spare (balancing helping the network against protecting its own patients), and
returns an offer with a short rationale. The coordinator then ranks the offers.

Mirrors the conventions of claude_ranking.py / claude_research.py:
  * Reuses the frozen dataclasses from interfaces — never redefines them.
  * Heavy `anthropic` import is lazy (keeps the SDK optional).
  * Structured output via forced tool-use (`submit_offer`); we re-validate and
    clamp the result ourselves, never trusting the model's arithmetic.
  * Fail-CLOSED to a cooperative default (offer the full spare) on ANY error —
    a missing key, API failure, or malformed output must never hang the demo.

Env:
    BAYMAX_HOSPITAL_LLM   1/true -> ask Claude per facility (else offer full spare)
    ANTHROPIC_API_KEY     Anthropic credentials (loaded from .env by the runners)
    BAYMAX_HOSPITAL_MODEL override the model id (default claude-sonnet-4-6)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import interfaces

_log = logging.getLogger("baymax.hospital")

DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 512


@dataclass
class HospitalOffer:
    """One facility's response to a broadcast need. `quantity` is what it will
    actually release (0 == declines). Converted to an interfaces.OfferView for
    ranking; `rationale` is narrated at the collecting_offers step."""

    offerer: str
    quantity: int
    distance_km: float
    eta_minutes: int
    expiry: Optional[str]
    spare_capacity: int
    can_offer: bool
    rationale: str

    def to_offer_view(self) -> interfaces.OfferView:
        return interfaces.OfferView(
            offerer=self.offerer,
            quantity_available=max(0, self.quantity),
            distance_km=self.distance_km,
            eta_minutes=self.eta_minutes,
            expiry=self.expiry,
        )


def hospital_llm_enabled() -> bool:
    """True when each facility should reason via Claude (opt-in)."""
    return os.getenv("BAYMAX_HOSPITAL_LLM", "").strip().lower() in ("1", "true", "yes")


_OFFER_TOOL = {
    "name": "submit_offer",
    "description": (
        "Submit how many units of the requested supply this facility will release "
        "to the hospital in need, plus a one-sentence rationale a supply manager "
        "would give."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "offer_quantity": {
                "type": "integer",
                "description": (
                    "Units to release now (>= 0, and never more than the spare "
                    "capacity stated in the prompt). 0 means decline."
                ),
            },
            "rationale": {
                "type": "string",
                "description": "One sentence explaining the decision to a supply manager.",
            },
        },
        "required": ["offer_quantity", "rationale"],
        "additionalProperties": False,
    },
}


def _claude_decide(hospital: str, item: str, qty_requested: int,
                   inv: interfaces.InventoryState, spare: int) -> tuple[int, str]:
    """Ask this facility's Claude agent how much to spare. Raises on any failure
    so the caller falls back to the cooperative default."""
    import anthropic  # lazy

    model = os.getenv("BAYMAX_HOSPITAL_MODEL", "").strip() or DEFAULT_MODEL
    client = anthropic.Anthropic()

    system = (
        f"You are the supply manager AI for {hospital}, one facility in a network "
        "of hospitals that share surplus medical supplies during crises. Another "
        "facility is short and has broadcast a request. Decide how many units of "
        "your spare stock to release. Be genuinely cooperative — the network only "
        "works if facilities help each other — but never drop below your own "
        "safety reserve. You may offer anywhere from 0 up to your spare capacity. "
        "Always respond by calling the submit_offer tool."
    )
    prompt = (
        f"Requested item: \"{item}\".\n"
        f"Units the other facility needs: {qty_requested}.\n\n"
        f"Your stock of {item}:\n"
        f"  - on hand: {inv.qty}\n"
        f"  - safety reserve you must keep: {inv.safety_threshold}\n"
        f"  - spare capacity you could release without going below reserve: {spare}\n\n"
        f"Decide how many units (0..{spare}) to offer, then call submit_offer."
    )

    response = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=system,
        tools=[_OFFER_TOOL],
        tool_choice={"type": "tool", "name": "submit_offer"},
        messages=[{"role": "user", "content": prompt}],
    )
    block = next(
        (b for b in response.content
         if getattr(b, "type", None) == "tool_use" and b.name == "submit_offer"),
        None,
    )
    if block is None:
        raise ValueError("Claude did not return a submit_offer tool call")
    data = block.input
    if isinstance(data, str):
        data = json.loads(data)
    qty = data.get("offer_quantity")
    if not isinstance(qty, int) or isinstance(qty, bool):
        raise ValueError(f"non-integer offer_quantity: {qty!r}")
    rationale = (data.get("rationale") or "").strip()
    return qty, rationale


async def decide_offer(hospital: str, item: str, qty_requested: int,
                       requester: str) -> HospitalOffer:
    """This facility's agent decides its offer for a broadcast need.

    Always returns a HospitalOffer (never raises): on any Claude error it falls
    back to offering the full spare capacity (the cooperative default), so the
    negotiation never stalls.
    """
    inv = await asyncio.to_thread(interfaces.get_inventory, hospital, item)
    spare = inv.spare_capacity
    distance = interfaces.distance_between(hospital, requester)
    eta = interfaces.eta_minutes_for(distance)
    expiry = interfaces.expiry_for(hospital, item)

    if not inv.present or spare <= 0:
        return HospitalOffer(
            offerer=hospital, quantity=0, distance_km=distance, eta_minutes=eta,
            expiry=expiry, spare_capacity=max(0, spare), can_offer=False,
            rationale=f"{hospital} has no spare {item} to release.",
        )

    quantity = spare
    rationale = (
        f"{hospital} can spare {spare} {item} while staying above its safety reserve."
    )
    if hospital_llm_enabled():
        try:
            qty, why = await asyncio.to_thread(
                _claude_decide, hospital, item, qty_requested, inv, spare)
            quantity = max(0, min(qty, spare))  # clamp — never trust the model
            if why:
                rationale = why
            _log.info("[hospital] backend=claude %s/%s spare=%s -> offer=%s",
                      hospital, item, spare, quantity)
        except Exception as exc:  # noqa: BLE001 — fail-closed to full spare
            _log.warning("[hospital] backend=claude FAILED for %s/%s (%s) -> "
                         "offer full spare %s", hospital, item, exc, spare)
            quantity = spare

    return HospitalOffer(
        offerer=hospital, quantity=quantity, distance_km=distance, eta_minutes=eta,
        expiry=expiry, spare_capacity=spare, can_offer=quantity > 0,
        rationale=rationale,
    )
