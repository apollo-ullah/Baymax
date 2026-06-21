"""claude_research.py — the REAL implementation of the crisis-research seam,
behind interfaces.research_crisis().

interfaces.research_crisis() ships a deterministic keyword mock so the two demo
surfaces (dashboard + ASI:One) are never blocked on an LLM. This module is the
live backend the mock stands in for: it asks Claude to read a natural-language
crisis statement ("wildfires near Hospital A") and produce the inferred crisis
type plus a RANKED list of at-risk supplies — constrained to the items the
inventory layer actually knows (interfaces.KNOWN_ITEMS) — with a chat-ready
rationale that streams straight into ASI:One / the dashboard at the research step.

interfaces.research_crisis() delegates here when BAYMAX_CLAUDE_RESEARCH=1 and
falls back to the mock on ANY failure (missing key, API error, malformed output,
lib absent) — the fail-closed contract, identical to claude_ranking.py.

Design notes (mirroring claude_ranking.py / redis_inventory.py / supplier_order.py):
  * Dataclasses (AtRiskSupply, CrisisBrief) and KNOWN_ITEMS are imported FROM
    interfaces — never redefined. This module has NO uagents dependency.
  * The heavy import (`anthropic`) is lazy, inside the function.
  * Structured output via forced tool-use — we never parse free text. Claude must
    emit a crisis_type + an at_risk array clamped to KNOWN_ITEMS; we re-validate
    and clamp ourselves so the CrisisBrief is always internally consistent.
  * On anything malformed / any API error we RAISE, so the seam falls back to the
    mock.

Env:
    BAYMAX_CLAUDE_RESEARCH   1/true -> interfaces.research_crisis() uses this backend
    ANTHROPIC_API_KEY        Anthropic credentials (loaded from .env by the runners)
    BAYMAX_RESEARCH_MODEL    override the model id (default claude-sonnet-4-6)
"""

from __future__ import annotations

import json
import os

# Safe top-level import: interfaces imports THIS module only lazily (inside
# research_crisis), so interfaces is fully initialised by the time we are first
# imported (mirrors claude_ranking). NEVER redefine these.
from interfaces import KNOWN_ITEMS, AtRiskSupply, CrisisBrief

DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024

_RISK_LEVELS = ("high", "elevated", "moderate")

# The single tool Claude must call. Its schema IS the structured-output contract.
_BRIEF_TOOL = {
    "name": "submit_crisis_brief",
    "description": (
        "Submit the supply-risk brief for a stated crisis: the inferred crisis "
        "type and a ranked list (highest risk first) of which medical supplies "
        "are most at risk of running short, drawn ONLY from the allowed items."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "crisis_type": {
                "type": "string",
                "description": (
                    "Short snake_case label for the crisis, e.g. wildfire, heatwave, "
                    "flu_surge, earthquake, storm, or unknown."
                ),
            },
            "at_risk": {
                "type": "array",
                "description": (
                    "Ranked at-risk supplies, highest risk first. Use ONLY the "
                    "allowed items; omit items that are not at risk. At least one."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {
                            "type": "string",
                            "enum": list(KNOWN_ITEMS),
                            "description": "One of the allowed inventory items.",
                        },
                        "risk": {
                            "type": "string",
                            "enum": list(_RISK_LEVELS),
                            "description": "Risk level for this item under this crisis.",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "One clause on why this item is at risk for this crisis.",
                        },
                    },
                    "required": ["item", "risk", "rationale"],
                    "additionalProperties": False,
                },
            },
            "rationale": {
                "type": "string",
                "description": "1-2 sentences a supply manager can read, summarising the call.",
            },
        },
        "required": ["crisis_type", "at_risk", "rationale"],
        "additionalProperties": False,
    },
}


def claude_research_enabled() -> bool:
    """True when the live Claude research backend should be used (opt-in)."""
    return (
        os.getenv("BAYMAX_CLAUDE_RESEARCH", "").strip().lower() in ("1", "true", "yes", "on")
        and bool(os.getenv("ANTHROPIC_API_KEY"))
    )


def _brief_from_tool(crisis_text: str, region: str, data: dict) -> CrisisBrief:
    """Validate + clamp Claude's tool output into a CrisisBrief. Raises on
    anything malformed so the caller falls back to the mock."""
    crisis_type = (data.get("crisis_type") or "unknown").strip() or "unknown"
    raw_at_risk = data.get("at_risk")
    if not isinstance(raw_at_risk, list) or not raw_at_risk:
        raise ValueError(f"at_risk missing or empty: {raw_at_risk!r}")

    at_risk: list[AtRiskSupply] = []
    seen = set()
    for entry in raw_at_risk:
        if not isinstance(entry, dict):
            raise ValueError(f"at_risk entry not an object: {entry!r}")
        item = entry.get("item")
        if item not in KNOWN_ITEMS:          # clamp: ignore anything off-menu
            continue
        if item in seen:
            continue
        seen.add(item)
        risk = entry.get("risk")
        if risk not in _RISK_LEVELS:
            risk = "elevated"
        at_risk.append(AtRiskSupply(
            item=item, risk=risk, rationale=(entry.get("rationale") or "").strip(),
        ))

    if not at_risk:
        raise ValueError("no at_risk items mapped onto KNOWN_ITEMS")

    rationale = (data.get("rationale") or "").strip()
    if not rationale:
        raise ValueError("Claude returned an empty rationale")

    return CrisisBrief(
        crisis_text=crisis_text, crisis_type=crisis_type, region=region,
        at_risk=at_risk, rationale=rationale,
    )


def research_crisis_via_claude(crisis_text: str, region: str = "san_francisco") -> CrisisBrief:
    """Ask Claude to classify the crisis + rank at-risk supplies, returning a
    validated CrisisBrief. Raises on ANY failure so interfaces.research_crisis()
    falls back to the deterministic mock."""
    import anthropic  # lazy: keeps the SDK optional

    model = os.getenv("BAYMAX_RESEARCH_MODEL", "").strip() or DEFAULT_MODEL
    client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY from the env

    allowed = ", ".join(f'"{i}"' for i in KNOWN_ITEMS)
    system = (
        "You are a hospital supply-chain risk analyst for a regional hospital "
        "network. Given a short statement of an unfolding crisis, infer the crisis "
        "type and decide which consumable medical supplies are most at risk of "
        "running short over the next 7 days. You may ONLY choose from these "
        f"inventory items: {allowed}. Rank them highest-risk first. This is "
        "operational logistics only — never clinical advice. Always respond by "
        "calling the submit_crisis_brief tool."
    )

    response = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=system,
        tools=[_BRIEF_TOOL],
        tool_choice={"type": "tool", "name": "submit_crisis_brief"},
        messages=[{"role": "user", "content": f"Crisis (region={region}): {crisis_text}"}],
    )

    tool_block = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"
         and b.name == "submit_crisis_brief"),
        None,
    )
    if tool_block is None:
        raise ValueError("Claude did not return a submit_crisis_brief tool call")

    data = tool_block.input
    if isinstance(data, str):  # defensive — forced tool-use yields a dict
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError(f"tool input was not an object: {data!r}")

    return _brief_from_tool(crisis_text, region, data)
