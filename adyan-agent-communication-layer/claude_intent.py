"""claude_intent.py — Claude-backed natural-language intent parser (the NLU seam).

This is the *optional* LLM parser that drops in behind front_agent._resolve_parser()
when BAYMAX_CLAUDE_INTENT is truthy AND ANTHROPIC_API_KEY is set. It uses the
Anthropic SDK with Claude tool-use / strict structured output to force a JSON
object that matches the EXACT return contract of front_agent.parse_intent():

    {"kind": "greeting"}
    {"kind": "request", "item": <canonical>, "requester": <facility>, "quantity_needed": int|None}
    {"kind": "order",   "item": <canonical>, "requester": <facility>, "quantity": int|None}
    {"kind": "unknown"}
    {"kind": "ignored", "reason": <str>}

The canonical items / facilities and the default requester are imported from
front_agent so this module can never drift from the deterministic parser it
augments. Claude does the *understanding* (typos, synonyms, urgency, multi-phrasing,
echo detection); this module does the *validation* — any unknown item, bad kind,
API error, or malformed output RAISES so the resolver fails closed to the
deterministic parser (a parser error must never drop a user's message).

Model: claude-haiku-4-5 (fast/cheap, right for a per-message classifier).
"""

from __future__ import annotations

import json
import os

# Reuse the single source of truth for items/facilities/default requester so
# the LLM parser and the deterministic parser canonicalise identically.
from front_agent import (
    KNOWN_ITEMS,
    REQUESTER,
    _ITEM_SYNONYMS,
    _KNOWN_FACILITIES,
    _match_item,
)

# claude-haiku-4-5: fast and cheap, which is the right tradeoff for a parser that
# runs once per inbound chat message. Override via env for experimentation.
_MODEL = os.getenv("BAYMAX_CLAUDE_INTENT_MODEL", "claude-haiku-4-5")

_ALLOWED_KINDS = {"greeting", "request", "order", "unknown", "ignored"}

# The strict tool schema — Claude MUST emit a tool call whose input validates
# against this. additionalProperties:false + required is mandatory for strict.
_INTENT_TOOL = {
    "name": "record_intent",
    "description": (
        "Record the structured interpretation of a hospital supply-manager chat "
        "message. Always call this exactly once."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "item", "requester", "quantity", "reason"],
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["greeting", "request", "order", "unknown", "ignored"],
                "description": (
                    "greeting = a hello / help / capability probe with no supply item. "
                    "request = a facility is short on / needs / running low on an item "
                    "(reactive shortfall). "
                    "order = an explicit external/proactive purchase ('order 500 saline', "
                    "'buy more sutures') with no shortfall framing. "
                    "unknown = a message we can't map to any known supply item. "
                    "ignored = the agent's own narration echoed back (a long, multi-line "
                    "recap with emoji, supplier lists, prices, phone numbers, or "
                    "milestone headers) — NOT something a human supply manager would type."
                ),
            },
            "item": {
                "type": ["string", "null"],
                "description": (
                    "The supply item, mapped to EXACTLY one canonical value: "
                    f"{', '.join(KNOWN_ITEMS)}. Map synonyms/typos: e.g. 'IV bags', "
                    "'drip', 'intravenous fluids' -> 'IV fluids'; 'normal saline', "
                    "'NaCl', 'saline solution' -> 'saline'; 'stitches', 'suture', "
                    "'surgical thread' -> 'sutures'. Null for greeting/unknown/ignored, "
                    "or when no known item is identifiable."
                ),
            },
            "requester": {
                "type": ["string", "null"],
                "description": (
                    "The facility that is short / ordering, one of: "
                    f"{', '.join(_KNOWN_FACILITIES)}. Default to '{REQUESTER}' when a "
                    "request/order names no facility. Null for greeting/unknown/ignored."
                ),
            },
            "quantity": {
                "type": ["integer", "null"],
                "description": (
                    "The stated quantity of the item, if any (e.g. '200 units' -> 200). "
                    "Null when no quantity is stated, or for greeting/unknown/ignored. "
                    "Used as quantity_needed for a request and quantity for an order."
                ),
            },
            "reason": {
                "type": ["string", "null"],
                "description": (
                    "Only for kind='ignored': a short snake_case reason "
                    "(e.g. 'echo_chatter', 'milestone_echo', 'asi1_meta'). "
                    "Null for every other kind."
                ),
            },
        },
    },
}

_SYSTEM = (
    "You are the natural-language understanding layer for Baymax, an autonomous "
    "hospital supply-negotiation system. A human supply manager types short, "
    "natural messages into a chat (e.g. \"Hospital A is short on IV fluids\", "
    "\"we ran outta IV bags at site A\", \"urgently need ~200 units of saline asap\", "
    "\"order 500 sutures\"). Classify each message and extract a structured intent "
    "by calling the record_intent tool exactly once.\n\n"
    "Rules:\n"
    f"1. Known supply items (canonical): {', '.join(KNOWN_ITEMS)}. Map any synonym, "
    "abbreviation, brand-ish phrasing, or typo to the closest canonical item. If the "
    "message references no recognizable supply item, use kind='unknown' with item=null.\n"
    f"2. Known facilities: {', '.join(_KNOWN_FACILITIES)}. If a request/order names no "
    f"facility, default requester to '{REQUESTER}'.\n"
    "3. kind='request' for a shortfall (short on / need / running low / out of / "
    "shortage). kind='order' for an explicit proactive purchase (order / buy / "
    "purchase / procure) without shortfall framing.\n"
    "4. kind='greeting' for hellos, 'help', 'what can you do', capability probes with "
    "no item.\n"
    "5. kind='ignored' ONLY for the system's own narration echoed back: long, "
    "multi-line recaps, emoji-laden supplier monologues, price/phone-number lists, or "
    "messages beginning with a **milestone** header. A real human intent is one short "
    "line. When in doubt between 'ignored' and a real intent for a short single line, "
    "prefer the real intent.\n"
    "6. Always populate the unused fields with null (item/requester/quantity/reason as "
    "appropriate for the kind)."
)


def _call_claude(text: str) -> dict:
    """Invoke Claude (Haiku) and return the raw tool-call input dict.

    Raises on any API/transport error or if no tool call is returned — the caller
    (front_agent._resolve_parser wrapper) treats a raise as 'fall back to the
    deterministic parser', so failing closed here is correct.
    """
    import anthropic  # local import: only needed when this parser is active

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env (.env preloaded)
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=_SYSTEM,
        tools=[_INTENT_TOOL],
        tool_choice={"type": "tool", "name": "record_intent"},
        messages=[{"role": "user", "content": text}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record_intent":
            data = block.input
            # Strict tool use guarantees a dict, but be defensive about odd SDK shapes.
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                raise ValueError(f"record_intent input was not an object: {type(data)!r}")
            return data
    raise ValueError("Claude returned no record_intent tool call")


def _canon_requester(value) -> str:
    """Coerce a model-returned facility to a known canonical facility name.

    Falls back to the default REQUESTER on anything unrecognised so an odd answer
    can never reach start_negotiation() with a bogus facility.
    """
    if isinstance(value, str):
        v = value.strip().lower()
        for fac in _KNOWN_FACILITIES:
            if v == fac.lower():
                return fac
        # Tolerate "hospital a"/"a"/"hosp a" style answers via the synonym-free letter.
        letter = v.replace("hospital", "").replace("hosp", "").strip(" .-")
        for fac in _KNOWN_FACILITIES:
            if letter and letter == fac.split()[-1].lower():
                return fac
    return REQUESTER


def _canon_quantity(value):
    """Coerce a model-returned quantity to a positive int, or None."""
    if value is None:
        return None
    try:
        q = int(value)
    except (TypeError, ValueError):
        return None
    return q if q > 0 else None


def parse_intent_llm(text: str) -> dict:
    """Claude-backed parser. Same (str -> dict) signature/contract as parse_intent.

    Drops into front_agent._resolve_parser() behind BAYMAX_CLAUDE_INTENT. Validates
    Claude's structured output hard: an unknown item degrades to {"kind":"unknown"};
    an invalid kind, missing key, or any API/parse error RAISES so the resolver's
    wrapper falls back to the deterministic parse_intent (fail-closed — never drop a
    user's message on a parser hiccup).
    """
    text = (text or "").strip()
    if not text:
        # Mirror parse_intent's empty-input behaviour without an API round-trip.
        return {"kind": "greeting"}

    data = _call_claude(text)  # raises on API error / no tool call

    kind = data.get("kind")
    if kind not in _ALLOWED_KINDS:
        raise ValueError(f"Claude returned an invalid kind: {kind!r}")

    if kind == "greeting":
        return {"kind": "greeting"}

    if kind == "ignored":
        reason = data.get("reason")
        return {"kind": "ignored", "reason": str(reason) if reason else "llm_ignored"}

    if kind == "unknown":
        return {"kind": "unknown"}

    # request / order both need a canonical item; canonicalise hard via _match_item
    # so a loose LLM answer ("IV", "saline soln", a typo it half-corrected) still maps,
    # and a genuinely unknown item degrades to "unknown" rather than reaching the core.
    raw_item = data.get("item")
    canon = None
    if isinstance(raw_item, str) and raw_item.strip():
        canon = raw_item if raw_item in KNOWN_ITEMS else _match_item(raw_item.lower())
    if canon is None:
        return {"kind": "unknown"}

    requester = _canon_requester(data.get("requester"))
    quantity = _canon_quantity(data.get("quantity"))

    if kind == "order":
        return {
            "kind": "order",
            "item": canon,
            "requester": requester,
            "quantity": quantity,
        }

    # kind == "request"
    return {
        "kind": "request",
        "item": canon,
        "requester": requester,
        "quantity_needed": quantity,
    }


# Touch the synonym map at import so a future refactor that drops it trips here,
# not at first chat. (No behavioural effect.)
assert _ITEM_SYNONYMS, "item synonym map missing"
