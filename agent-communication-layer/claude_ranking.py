"""claude_ranking.py — the REAL implementation of the offer-ranking seam
(Workstream B), behind interfaces.rank_offers().

interfaces.rank_offers() ships a deterministic nearest-first greedy allocator so
the negotiation network is never blocked on an LLM. This module is the live
backend the mock stands in for: it asks Claude to reason over ALL the constraints
of a shortfall — quantity available, distance/ETA, stock expiry, and urgency —
and to choose which surplus facilities to draw from and how much from each,
returning the SAME `RankedPlan` shape with a genuinely intelligent natural-language
`rationale`. That rationale is streamed straight into the live ASI:One chat at the
EVALUATING step, so Claude's reasoning becomes the visible "judgment" of the system.

interfaces.rank_offers() delegates here when BAYMAX_CLAUDE_RANKING=1 and falls
back to the mock on ANY failure (missing key, API error, malformed output, lib
absent) — so the offline harnesses keep working with zero network and the
negotiation never hangs (the fail-closed contract).

Design notes mirroring redis_inventory.py / supplier_order.py:
  * Dataclasses are imported FROM interfaces — never redefined (they are frozen
    contracts). This module, like interfaces, has NO uagents dependency.
  * Heavy import (`anthropic`) is lazy, inside the function, so the module loads
    cleanly even when the optional package is absent.
  * SYNC entrypoint. The negotiation core calls rank_offers() (and thus this)
    synchronously inside its evaluate step today; if a future deploy needs it off
    the event loop it can wrap with asyncio.to_thread() like the supplier seam.
  * Structured output via Claude tool-use (forced tool_choice) — we never parse
    free text. Claude must emit a valid allocation array; we then re-validate and
    recompute the invariants ourselves so the RankedPlan is always internally
    consistent regardless of what the model returns.
  * On anything malformed or any API error we RAISE, so interfaces.rank_offers()
    falls back to the mock.

Env:
    BAYMAX_CLAUDE_RANKING   1/true -> interfaces.rank_offers() uses this backend
    ANTHROPIC_API_KEY       Anthropic credentials (loaded from .env by the runners)
    BAYMAX_RANKING_MODEL    override the model id (default claude-sonnet-4-6)
"""

from __future__ import annotations

import json
import os
from typing import List

# Safe top-level import: interfaces imports THIS module only lazily (inside
# rank_offers), so interfaces is fully initialised by the time we are first
# imported (mirrors redis_inventory / supplier_order). NEVER redefine these.
from interfaces import Allocation, OfferView, RankedPlan, SupplyNeed

# A real, current model id. Forced tool-use for structured output; one call.
DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024

# The single tool Claude must call. Its schema is the structured-output contract:
# a list of {offerer, quantity} legs plus a short natural-language rationale.
_ALLOCATION_TOOL = {
    "name": "submit_allocation_plan",
    "description": (
        "Submit the chosen transfer plan: which surplus facilities to draw the "
        "needed item from, and how much from each, plus a concise rationale that "
        "explains the reasoning to a hospital supply manager."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "allocations": {
                "type": "array",
                "description": (
                    "The legs of the transfer. Each leg takes `quantity` units "
                    "from `offerer`. Only include facilities you are drawing from; "
                    "omit any you take nothing from. May be empty if no facility "
                    "should contribute."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "offerer": {
                            "type": "string",
                            "description": "Facility name, exactly as given in the offers.",
                        },
                        "quantity": {
                            "type": "integer",
                            "description": "Units to take from this facility (>= 1, <= its available quantity).",
                        },
                    },
                    "required": ["offerer", "quantity"],
                    "additionalProperties": False,
                },
            },
            "rationale": {
                "type": "string",
                "description": (
                    "1-3 sentences explaining the plan: why these facilities, the "
                    "tradeoff between distance/ETA, expiry (prefer using "
                    "soonest-expiring surplus first to reduce waste), urgency, and "
                    "how much of the need is covered."
                ),
            },
        },
        "required": ["allocations", "rationale"],
        "additionalProperties": False,
    },
}


def claude_ranking_enabled() -> bool:
    """True when the live Claude ranking backend should be used (opt-in)."""
    return os.getenv("BAYMAX_CLAUDE_RANKING", "").strip().lower() in ("1", "true", "yes")


def _build_prompt(need: SupplyNeed, offers: List[OfferView]) -> str:
    """Render the need + offers into a compact, deterministic prompt body."""
    lines = [
        f"A hospital needs {need.quantity_needed} units of \"{need.item}\".",
        f"Requesting facility: {need.requester}.",
        f"Urgency: {need.urgency}.",
    ]
    if need.needed_by:
        lines.append(f"Needed by: {need.needed_by}.")
    lines.append("")
    lines.append("Surplus offers available (choose which to take and how much):")
    for o in offers:
        exp = o.expiry if o.expiry else "unknown"
        lines.append(
            f"  - {o.offerer}: up to {o.quantity_available} units available; "
            f"{o.distance_km:.0f} km away (~{o.eta_minutes} min); "
            f"stock expiry {exp}."
        )
    lines.append("")
    lines.append(
        "Decide how to cover the need. Reason over ALL constraints: take from "
        "nearer facilities to minimise ETA, but prefer drawing down stock that "
        "expires soonest to reduce waste; respect each facility's available "
        "quantity; and weigh the urgency. You do not have to fully cover the need "
        "if not enough spare exists — take as much as is sensible. Then call "
        "submit_allocation_plan with your chosen legs and a short rationale."
    )
    return "\n".join(lines)


def _plan_from_allocations(
    need: SupplyNeed,
    offers: List[OfferView],
    raw_allocations: list,
    rationale: str,
) -> RankedPlan:
    """Validate Claude's allocations against the offers and recompute every
    invariant ourselves. Raises ValueError on anything malformed so the caller
    falls back to the mock."""
    by_offerer = {o.offerer: o for o in offers}

    allocations: List[Allocation] = []
    seen = set()
    for entry in raw_allocations:
        if not isinstance(entry, dict):
            raise ValueError(f"allocation entry not an object: {entry!r}")
        offerer = entry.get("offerer")
        qty = entry.get("quantity")
        if offerer not in by_offerer:
            raise ValueError(f"allocation references unknown offerer: {offerer!r}")
        if offerer in seen:
            raise ValueError(f"duplicate allocation for offerer: {offerer!r}")
        if not isinstance(qty, int) or isinstance(qty, bool):
            raise ValueError(f"non-integer quantity for {offerer!r}: {qty!r}")
        if qty <= 0:
            # Treat a zero/negative leg as "don't take from this facility".
            continue
        o = by_offerer[offerer]
        if qty > o.quantity_available:
            raise ValueError(
                f"over-allocation: {qty} > {o.quantity_available} available at {offerer!r}"
            )
        seen.add(offerer)
        allocations.append(Allocation(
            offerer=offerer, quantity=qty,
            distance_km=o.distance_km, eta_minutes=o.eta_minutes, expiry=o.expiry,
        ))

    # Recompute invariants from the validated legs — never trust the model's math.
    total_covered = sum(a.quantity for a in allocations)
    need_qty = max(0, need.quantity_needed)
    shortfall_remaining = max(0, need_qty - total_covered)
    fully_covered = total_covered >= need_qty and need_qty > 0

    rationale = (rationale or "").strip()
    if not rationale:
        raise ValueError("Claude returned an empty rationale")

    return RankedPlan(
        allocations=allocations,
        total_covered=total_covered,
        shortfall_remaining=shortfall_remaining,
        fully_covered=fully_covered,
        rationale=rationale,
    )


def rank_offers_via_claude(need: SupplyNeed, offers: List[OfferView]) -> RankedPlan:
    """Ask Claude to choose the transfer plan, returning a validated RankedPlan.

    Raises on ANY failure (no key, lib missing, API error, malformed/invalid
    output) so interfaces.rank_offers() falls back to the deterministic mock.
    """
    import anthropic  # lazy: keeps the SDK optional

    usable = [o for o in offers if o.quantity_available > 0]
    model = os.getenv("BAYMAX_RANKING_MODEL", "").strip() or DEFAULT_MODEL

    client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY from the env

    system = (
        "You are the logistics planner for a network of hospitals that share "
        "surplus medical supplies. Given a facility's shortfall and the surplus "
        "other facilities can spare, choose which facilities to draw from and how "
        "much from each to best cover the need. Optimise for fast delivery (lower "
        "ETA), minimal waste (prefer soonest-expiring stock), and the stated "
        "urgency. Always respond by calling the submit_allocation_plan tool."
    )

    response = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=system,
        tools=[_ALLOCATION_TOOL],
        tool_choice={"type": "tool", "name": "submit_allocation_plan"},
        messages=[{"role": "user", "content": _build_prompt(need, usable)}],
    )

    tool_block = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"
         and b.name == "submit_allocation_plan"),
        None,
    )
    if tool_block is None:
        raise ValueError("Claude did not return a submit_allocation_plan tool call")

    data = tool_block.input
    if isinstance(data, str):  # defensive — forced tool-use yields a dict
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError(f"tool input was not an object: {data!r}")

    raw_allocations = data.get("allocations")
    if not isinstance(raw_allocations, list):
        raise ValueError(f"allocations missing or not a list: {raw_allocations!r}")

    return _plan_from_allocations(need, usable, raw_allocations, data.get("rationale", ""))
