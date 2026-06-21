"""front_agent.py — Baymax's ASI:One-facing entrypoint (Hospital A / FRONT).

This is the standalone runnable agent that turns a natural-language chat intent
from ASI:One into a full inter-facility supply negotiation and streams every
milestone of that negotiation back into the same chat conversation.

Wiring (all of it imported from the frozen Wave 0 contract — nothing redefined):
  * agent_base.build_hospital_agent("Hospital A", mailbox=True)
        the requester agent, reachable through Agentverse/ASI:One via a Mailbox
        (no public inbound endpoint needed). network is pinned to testnet.
  * baymax_agents.attach_front_handlers(front)
        the negotiation message handlers (SupplyOffer / TransferAccept /
        TransferReject + the offer-timeout tick) that orchestrate the deal.
  * agent_base.build_chat_protocol(on_intent)
        the ASI:One Chat Protocol shell. It acks every ChatMessage first, then
        hands the joined text to our on_intent().

The flow:
  ASI:One ChatMessage --> chat shell acks --> on_intent(ctx, sender, text)
    --> parse_intent(text) --> start_negotiation(ctx, item, requester=...,
        quantity_needed=..., reply_to=sender)
    --> every NegotiationState milestone is sent back to `sender` as a
        ChatMessage automatically (because reply_to is set), ending the chat
        session on the terminal (CONFIRMED / FAILED / IDLE) step.

No API key is required: parse_intent() is a deterministic keyword/regex parser.
A clearly-marked seam (parse_intent_llm) shows how an ASI:One LLM parser would
drop in behind the SAME signature when BAYMAX_ASI1_API_KEY is set.

Run modes:
  * Live ASI:One:   `python front_agent.py`   (Mailbox; see __main__ banner /
                    the manual steps at the bottom of this file).
  * Local self-test: `BAYMAX_SELFTEST=1 python front_agent.py`
                    builds a 3-agent Bureau (A + surplus B + surplus C),
                    feeds a synthetic ChatMessage through the chat handler, and
                    confirms the negotiation completes + narrates — no Agentverse,
                    no ASI:One, no network needed.
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional

# Import agent_base FIRST so the Python-3.14 event-loop workaround is installed
# before ANY Agent is constructed. (baymax_agents also imports it, but we name
# it explicitly here to make the ordering contract obvious.)
from agent_base import (
    REQUESTER,
    build_chat_protocol,
    build_hospital_agent,
    create_text_chat,
)
from uagents import Context

from baymax_agents import (
    NEGOTIATIONS,
    attach_front_handlers,
    find_awaiting_approval,
    resume_after_admin_decision,
    start_negotiation,
    start_order,
)

# ---------------------------------------------------------------------------
# Known items + synonym map. The canonical items are the ones the inventory seam
# (interfaces.get_inventory / _MOCK_INVENTORY) actually knows about:
#     "IV fluids", "saline", "sutures".
# We map common phrasings/typos onto those exact strings so start_negotiation()
# always receives an item the inventory layer recognises.
# ---------------------------------------------------------------------------
KNOWN_ITEMS = ("IV fluids", "saline", "sutures")

# synonym (lowercase) -> canonical item. Order doesn't matter; we match the
# longest synonym first so "iv fluids" wins over a bare "iv".
_ITEM_SYNONYMS: dict[str, str] = {
    # IV fluids
    "iv fluids": "IV fluids",
    "iv fluid": "IV fluids",
    "i.v. fluids": "IV fluids",
    "iv bags": "IV fluids",
    "iv bag": "IV fluids",
    "intravenous fluids": "IV fluids",
    "intravenous fluid": "IV fluids",
    "drip": "IV fluids",
    "drips": "IV fluids",
    "iv": "IV fluids",
    # saline
    "saline": "saline",
    "saline solution": "saline",
    "normal saline": "saline",
    "saline bags": "saline",
    "nacl": "saline",
    # sutures
    "sutures": "sutures",
    "suture": "sutures",
    "stitches": "sutures",
    "surgical thread": "sutures",
}

# Known facility names (the registry's three hospitals). Matched case-insensitively.
_KNOWN_FACILITIES = ("Hospital A", "Hospital B", "Hospital C")

# Phrasings that are clearly a greeting / capability probe rather than a request.
_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|yo|hiya|good (morning|afternoon|evening)|"
    r"help|what can you do|who are you|capabilities|\?)\b",
    re.IGNORECASE,
)

# Our own milestone narration echoed back from ASI:One must NOT re-trigger a deal.
_MILESTONE_ECHO_RE = re.compile(
    r"^\s*\*\*(?:shortfall_detected|requesting|collecting_offers|evaluating|"
    r"proposing|settling|confirmed|failed|re_planning|idle|"
    r"awaiting_approval|ordering|ordered|"
    r"payment_confirmed|payment_failed)\*\*",
    re.IGNORECASE,
)

# ASI:One meta-replies / error strings — not user supply intents.
_ASI1_META_RE = re.compile(
    r"(sorry, something went wrong|want me to help|manual procurement it is|"
    r"no offer artifacts|sometimes the best offers|struck out|"
    r"failed to process payment|please try sending a message again|"
    r"draft (?:a )?follow-up|draft those procurement)",
    re.IGNORECASE,
)

# A real user intent names an item AND uses request language (not just a number
# in a recap sentence like "200 IV fluids short and the automated system...").
_REQUEST_CUE_RE = re.compile(
    r"\b(?:short|need|needs|require|requires|running low|low on|out of|"
    r"shortage|shortfall|restock|cover|we'?re short|is short)\b",
    re.IGNORECASE,
)

# Explicit "place an external order" phrasing (proactive restock / plan-ahead),
# distinct from a shortfall request. Checked before the request-cue gate.
_ORDER_CUE_RE = re.compile(
    r"\b(order|buy|purchase|procure)\b", re.IGNORECASE,
)

# Admin-decision keyword groups (for parse_decision at the AWAITING_APPROVAL gate).
_DECISION_APPROVE_RE = re.compile(
    r"\b(approve|approved|yes|confirm|go ahead|do it|trade|accept|proceed|ok|okay)\b",
    re.IGNORECASE,
)
_DECISION_ORDER_RE = re.compile(
    r"\b(order|buy|purchase|procure|supplier|external|externally)\b", re.IGNORECASE,
)
_DECISION_REJECT_RE = re.compile(
    r"\b(reject|cancel|no|nope|stop|abort|deny|decline)\b", re.IGNORECASE,
)

_DECISION_HELP = (
    "I didn't catch your decision. Reply with one of:\n"
    "  • `approve` — authorize the inter-facility trade\n"
    "  • `order`  — purchase from an external supplier instead\n"
    "  • `reject` — cancel"
)


def parse_decision(text: str) -> str:
    """Classify a chat reply at the AWAITING_APPROVAL gate.

    Returns "approve" | "order" | "reject" | "unclear". Requires EXACTLY one
    signal group to fire, so an echo of our prompt (which names all three) is
    "unclear" and re-prompts rather than triggering a wrong action."""
    t = text or ""
    has_approve = bool(_DECISION_APPROVE_RE.search(t))
    has_order = bool(_DECISION_ORDER_RE.search(t))
    has_reject = bool(_DECISION_REJECT_RE.search(t))
    if (has_approve + has_order + has_reject) != 1:
        return "unclear"
    if has_order:
        return "order"
    if has_reject:
        return "reject"
    return "approve"


# Distinctive markers of an ASI:One LLM recap/echo — never how a user types a
# one-line intent. The wrapping ASI:One agent parrots our narration back as long,
# multi-line, emoji/price/phone-laden "supplier" monologues; those must not
# re-trigger a deal even when they happen to contain "short"/"needs".
_CHATTER_RE = re.compile(
    r"(i found|suppliers?|distributors?|would you like|next steps?|options:|"
    r"in stock|per (?:bag|unit|case)|\$\d|\(\d{3}\)|\bstep\s+\d|"
    r"📞|🏥|🚑|🏭|📦|🎯|✅|🚨|🎉|💉|⏰|🤷)",
    re.IGNORECASE,
)


def _looks_like_echo_chatter(text: str) -> bool:
    """True if `text` reads like an ASI:One LLM recap, not a user supply intent.

    A genuine intent is one short line ("Hospital A is short on IV fluids"); the
    echoes are long, multi-line, emoji/price/phone-laden monologues. Any of those
    signals => treat as echo and ignore.
    """
    return (
        len(text) > 240
        or text.count("\n") >= 2
        or bool(_CHATTER_RE.search(text))
    )


# Per-sender cooldown. After a real intent kicks off a negotiation, the ASI:One
# LLM echoes it back many times within seconds; ignore further "requests" from the
# same sender for this window so the echo storm cannot spawn duplicate deals.
_INTENT_COOLDOWN_S = float(os.getenv("BAYMAX_INTENT_COOLDOWN", "20"))
_LAST_ACCEPTED_INTENT: dict[str, float] = {}

_CAPABILITIES = (
    "Baymax — autonomous hospital supply negotiation.\n\n"
    "Tell me which facility is short on what, and I'll broadcast the need to the "
    "network, rank the offers, and settle a (possibly split) inter-facility "
    "transfer — narrating each step back to you.\n\n"
    "I currently track: IV fluids, saline, sutures.\n\n"
    "Try phrasings like:\n"
    "  • \"Hospital A is short on IV fluids\"\n"
    "  • \"we're short 200 saline\"\n"
    "  • \"need sutures at Hospital A\"\n"
    "  • \"Hospital A needs 150 IV fluids\""
)

_UNPARSEABLE_HELP = (
    "Sorry — I couldn't work out the supply request from that.\n\n"
    "Tell me a facility and an item (one of: IV fluids, saline, sutures), e.g.:\n"
    "  • \"Hospital A is short on IV fluids\"\n"
    "  • \"we're short 200 saline\"\n"
    "  • \"need sutures at Hospital A\"\n"
    "  • \"Hospital A needs 150 IV fluids\""
)


# ---------------------------------------------------------------------------
# Intent parsing.
# ---------------------------------------------------------------------------

def _match_item(text_lower: str) -> Optional[str]:
    """Return the canonical item named in `text_lower`, or None.

    Longest synonym first so multi-word synonyms win over their substrings
    (e.g. "iv fluids" beats the bare "iv"). We use word-ish boundaries so we
    don't match "iv" inside "arrive".
    """
    for syn in sorted(_ITEM_SYNONYMS, key=len, reverse=True):
        # \b on both sides; the synonym may contain spaces/dots which are fine
        # inside a regex character run, but we escape it to be safe.
        if re.search(rf"(?<![a-z]){re.escape(syn)}(?![a-z])", text_lower):
            return _ITEM_SYNONYMS[syn]
    return None


def _match_facility(text: str) -> Optional[str]:
    """Return the canonical facility named in `text`, or None. Case-insensitive.

    Recognises "Hospital A/B/C" and the shorthand "hosp a" / bare trailing
    "... at A". Defaults are applied by the caller, not here.
    """
    tl = text.lower()
    for fac in _KNOWN_FACILITIES:
        # "hospital a", "hospital-a", "hosp a", "hospital a's"
        letter = fac.split()[-1].lower()  # a / b / c
        if re.search(rf"\bhosp(?:ital)?[\s\-]*{letter}\b", tl):
            return fac
    # "... at A" / "for B" shorthand (single trailing capital letter we know).
    m = re.search(r"\b(?:at|for|to)\s+([abc])\b", tl)
    if m:
        return {"a": "Hospital A", "b": "Hospital B", "c": "Hospital C"}[m.group(1)]
    return None


def _match_quantity(text: str, item_aliases: tuple[str, ...]) -> Optional[int]:
    """Pull a requested quantity out of the text, if one is stated.

    Looks for a number adjacent to "short"/"need"/"by" or directly before the
    item word. Returns None when no quantity is stated (start_negotiation then
    falls back to the inventory-derived shortfall)."""
    tl = text.lower()
    # 1) "short 200", "need 150", "short by 200", "needs 150"
    m = re.search(r"\b(?:short|need|needs|require|requires|down)\s+(?:by\s+)?(\d{1,6})\b", tl)
    if m:
        return int(m.group(1))
    # 2) "200 saline" / "150 iv fluids" — a number immediately before the item.
    for alias in item_aliases:
        m = re.search(rf"\b(\d{{1,6}})\s+{re.escape(alias)}\b", tl)
        if m:
            return int(m.group(1))
    # No lone-number fallback: a bare number in prose (e.g. "Step 1") is too easily
    # mis-read from an ASI:One recap. Unstated quantity => None, and
    # start_negotiation() falls back to the inventory-derived shortfall.
    return None


def parse_intent(text: str) -> dict:
    """Parse a natural-language supply intent into a structured request.

    Returns a dict with one of four `kind`s:
      {"kind": "greeting"}                         -> show capabilities
      {"kind": "request", "item": str,
       "requester": str, "quantity_needed": int|None}
      {"kind": "unknown"}                          -> show example phrasings
      {"kind": "ignored", "reason": str}           -> ASI:One echo / meta; no reply

    Recognised phrasings (case-insensitive), e.g.:
      * "Hospital A is short on IV fluids"
      * "we're short 200 saline"
      * "need sutures at Hospital A"
      * "Hospital A needs 150 IV fluids"
      * "running low on saline at hospital a"

    Item synonyms map onto the canonical KNOWN_ITEMS. Requester defaults to
    "Hospital A" (the FRONT facility) when not stated. quantity_needed is None
    when not stated, so start_negotiation() uses the inventory-derived shortfall.

    ── SEAM: this is the deterministic, no-API-key parser. An ASI:One LLM parser
       (parse_intent_llm below) can replace it behind this exact signature when a
       key is available; see _resolve_parser(). ──────────────────────────────
    """
    text = (text or "").strip()
    if not text:
        return {"kind": "greeting"}

    if _MILESTONE_ECHO_RE.match(text):
        return {"kind": "ignored", "reason": "milestone_echo"}

    if _ASI1_META_RE.search(text):
        return {"kind": "ignored", "reason": "asi1_meta"}

    # An ASI:One LLM recap (long / multi-line / emoji / supplier list) — never how
    # a user types a one-line intent. Drop it so it can neither re-trigger a deal
    # nor spawn a help-reply loop. (Checked before item-matching on purpose.)
    if _looks_like_echo_chatter(text):
        return {"kind": "ignored", "reason": "echo_chatter"}

    tl = text.lower()
    item = _match_item(tl)

    # A pure greeting / help probe with no item named -> capabilities.
    if item is None and _GREETING_RE.match(text):
        return {"kind": "greeting"}

    if item is None:
        return {"kind": "unknown"}

    # Explicit external-order intent ("order 500 saline") — proactive restock,
    # reachable even with no shortfall. Checked before the request-cue gate
    # because "order" is not a shortfall cue.
    if _ORDER_CUE_RE.search(text) and not _REQUEST_CUE_RE.search(text):
        requester = _match_facility(text) or REQUESTER
        item_aliases = tuple(syn for syn, canon in _ITEM_SYNONYMS.items() if canon == item)
        return {
            "kind": "order",
            "item": item,
            "requester": requester,
            "quantity": _match_quantity(text, item_aliases),
        }

    # Item mentioned but no request phrasing — likely ASI:One echoing our recap.
    if not _REQUEST_CUE_RE.search(text):
        return {"kind": "ignored", "reason": "no_request_cue"}

    requester = _match_facility(text) or REQUESTER  # default Hospital A

    # Aliases that resolved to this canonical item, used for "<n> <item>" matches.
    item_aliases = tuple(syn for syn, canon in _ITEM_SYNONYMS.items() if canon == item)
    quantity_needed = _match_quantity(text, item_aliases)

    return {
        "kind": "request",
        "item": item,
        "requester": requester,
        "quantity_needed": quantity_needed,
    }


# ---------------------------------------------------------------------------
# SEAM — OPTIONAL ASI:One LLM intent parser (drop-in, same signature).
#
# No API key is available in this environment, so this is intentionally NOT
# wired on by default. When BAYMAX_ASI1_API_KEY is set, _resolve_parser()
# would prefer this LLM parser; otherwise it returns the deterministic
# parse_intent() above. The LLM call below is illustrative (commented) — it uses
# the OpenAI-compatible ASI:One endpoint and must return the SAME dict shape as
# parse_intent(), so nothing downstream changes.
#
#   from openai import OpenAI
#
#   def parse_intent_llm(text: str) -> dict:
#       client = OpenAI(
#           base_url="https://api.asi1.ai/v1",
#           api_key=os.environ["BAYMAX_ASI1_API_KEY"],
#       )
#       resp = client.chat.completions.create(
#           model="asi1",
#           messages=[
#               {"role": "system", "content": (
#                   "Extract a hospital supply request. Respond ONLY as JSON: "
#                   '{"kind":"request","item":<one of: IV fluids|saline|sutures>,'
#                   '"requester":<Hospital A|B|C, default Hospital A>,'
#                   '"quantity_needed":<int or null>}. '
#                   'If it is a greeting use {"kind":"greeting"}; '
#                   'if unparseable use {"kind":"unknown"}.')},
#               {"role": "user", "content": text},
#           ],
#           temperature=0,
#       )
#       import json
#       data = json.loads(resp.choices[0].message.content)
#       # Defensive: re-canonicalise the item + default the requester so a loose
#       # LLM answer can never reach start_negotiation() with an unknown item.
#       if data.get("kind") == "request":
#           canon = _match_item(str(data.get("item", "")).lower())
#           if canon is None:
#               return {"kind": "unknown"}
#           data["item"] = canon
#           data["requester"] = data.get("requester") or REQUESTER
#       return data
# ---------------------------------------------------------------------------

def _resolve_parser():
    """Pick the active intent parser.

    Returns the ASI:One LLM parser when BAYMAX_ASI1_API_KEY is set AND the
    optional `openai` client is importable; otherwise the deterministic
    keyword/regex parse_intent(). Both share the exact same signature
    (str -> dict), so on_intent() never changes.
    """
    if os.getenv("BAYMAX_ASI1_API_KEY"):
        try:
            from openai import OpenAI  # noqa: F401  (presence check only)
            # return parse_intent_llm   # ← enable once the seam above is uncommented
        except Exception:
            pass
    return parse_intent


# The bound parser, resolved once at import. on_intent() calls this.
_PARSER = _resolve_parser()


# ---------------------------------------------------------------------------
# Chat intent handler — the bridge from ASI:One text to the negotiation core.
# ---------------------------------------------------------------------------

async def on_intent(ctx: Context, sender: str, text: str) -> None:
    """Drive the whole negotiation from one chat utterance.

    Called by the Chat Protocol shell (which has already acked the message). We
    parse the intent and, for a real supply request, call start_negotiation()
    with reply_to=sender so every NegotiationState milestone streams back to the
    ASI:One conversation automatically (and the final step ends the session).

    For greetings/help or unparseable input we reply directly with a ChatMessage
    and end the session — no negotiation is started. Echo/meta messages from
    ASI:One are silently ignored so narration cannot re-trigger a new deal.
    """
    # --- AWAITING_APPROVAL pre-filter (must run BEFORE intent parsing / cooldown /
    # duplicate-block, or the decision reply would be eaten as a non-intent). ---
    pending_req_id = find_awaiting_approval(sender)
    if pending_req_id is not None:
        # Ignore our own narration echoed back by ASI:One while we wait.
        # Use ONLY length/newline checks here — genuine echoes are long/multi-line;
        # a deliberate short admin reply (e.g. "order from supplier") must not be
        # dropped by _CHATTER_RE which matches "suppliers?".
        if (_MILESTONE_ECHO_RE.match(text) or _ASI1_META_RE.search(text)
                or len(text) > 240 or text.count("\n") >= 2):
            ctx.logger.debug(f"awaiting-approval: ignoring echo from {sender}")
            return
        decision = parse_decision(text)
        ctx.logger.info(f"admin decision from {sender}: {text!r} -> {decision}")
        if decision == "unclear":
            peek = _PARSER(text)
            if peek.get("kind") in ("request", "order"):
                pending_item = NEGOTIATIONS.get(pending_req_id, {}).get("item", "the pending request")
                msg = (f"You have a pending decision for **{pending_item}**. Please resolve it "
                       f"first (reply `approve`, `order`, or `reject`) before starting a new request.")
            else:
                msg = _DECISION_HELP
            await ctx.send(sender, create_text_chat(msg, end_session=False))
            return
        await resume_after_admin_decision(ctx, pending_req_id, decision)
        return

    parsed = _PARSER(text)
    kind = parsed.get("kind")
    ctx.logger.info(f"on_intent from {sender}: {text!r} -> {parsed}")

    if kind == "ignored":
        ctx.logger.debug(
            f"ignoring non-intent chat from {sender}: {parsed.get('reason')}"
        )
        return

    # Block duplicate starts while one is running (including awaiting wallet pay).
    for neg in NEGOTIATIONS.values():
        if neg.get("reply_to") == sender and not neg.get("done"):
            ctx.logger.info(
                f"negotiation already in progress for {sender} — ignoring duplicate intent"
            )
            return

    if kind == "greeting":
        await ctx.send(sender, create_text_chat(_CAPABILITIES, end_session=False))
        return

    if kind not in ("request", "order"):
        await ctx.send(sender, create_text_chat(_UNPARSEABLE_HELP, end_session=True))
        return

    # Cooldown: suppress the post-intent ASI:One echo storm from the same sender.
    last = _LAST_ACCEPTED_INTENT.get(sender)
    if last is not None and (time.monotonic() - last) < _INTENT_COOLDOWN_S:
        ctx.logger.info(
            f"intent cooldown ({_INTENT_COOLDOWN_S:.0f}s) active for {sender} — "
            f"ignoring likely echo")
        return
    _LAST_ACCEPTED_INTENT[sender] = time.monotonic()

    item = parsed["item"]
    requester = parsed.get("requester") or REQUESTER

    if kind == "order":
        quantity = parsed.get("quantity")
        qty_note = f" {quantity}" if quantity else ""
        await ctx.send(sender, create_text_chat(
            f"Understood — placing an external supplier order for{qty_note} {item} "
            f"({requester}). I'll narrate each step.", end_session=False))
        await start_order(ctx, item, quantity, requester=requester, reply_to=sender)
        return

    quantity_needed = parsed.get("quantity_needed")
    qty_note = f" ({quantity_needed} units)" if quantity_needed else ""
    await ctx.send(sender, create_text_chat(
        f"Understood — checking the network for {item} to cover {requester}{qty_note}. "
        f"I'll narrate each step.", end_session=False))
    await start_negotiation(
        ctx, item,
        requester=requester,
        quantity_needed=quantity_needed,
        reply_to=sender,
    )


# ---------------------------------------------------------------------------
# Agent construction (live ASI:One / Mailbox entrypoint).
# ---------------------------------------------------------------------------

def build_front_agent():
    """Build the FRONT agent: a fresh Hospital A in Mailbox mode, with the
    negotiation handlers and the ASI:One Chat Protocol attached.

    Mailbox=True makes it reachable through Agentverse/ASI:One without a public
    inbound endpoint. We do NOT reuse baymax_agents' demo agent and we do NOT
    kick off a negotiation on startup — the negotiation is driven purely by chat.
    """
    front = build_hospital_agent("Hospital A", mailbox=True)
    attach_front_handlers(front)

    chat_proto = build_chat_protocol(on_intent)
    front.include(chat_proto, publish_manifest=True)
    return front


# ---------------------------------------------------------------------------
# Local self-test — verify the whole chat->negotiation->narration loop WITHOUT
# ASI:One, Agentverse, or any network. Builds a 3-agent Bureau (FRONT + two
# surplus facilities), feeds a synthetic ChatMessage through the chat handler,
# and captures the narrated ChatMessages the FRONT would have streamed to a
# real chat sender. Run with:  BAYMAX_SELFTEST=1 python front_agent.py
# ---------------------------------------------------------------------------

def run_selftest():
    """Bureau-based self-test. Builds a 4-agent in-process Bureau:

        * FRONT (Hospital A)  — the real agent under test, with the real chat
          protocol + on_intent + negotiation handlers attached.
        * Hospital B, Hospital C — surplus facilities (attach_hospital_handlers),
          so the negotiation actually has counterparties to deal with.
        * a tiny "collector" agent — stands in for the ASI:One chat user. The
          FRONT streams its narration here (reply_to=collector), and the
          collector records + logs every milestone so we can SEE the narration.

    On startup, FRONT feeds a synthetic intent through the SAME on_intent() the
    live agent uses, so the whole chat -> parse -> negotiate -> narrate loop is
    exercised. The process self-exits at the terminal milestone
    (BAYMAX_EXIT_WHEN_DONE).
    """
    from uagents import Agent, Bureau
    from agent_base import FET_NETWORK
    from baymax_agents import attach_hospital_handlers

    # Self-terminate once the negotiation reaches a terminal (CONFIRMED/FAILED).
    os.environ.setdefault("BAYMAX_EXIT_WHEN_DONE", "1")
    intent_text = os.getenv("BAYMAX_SELFTEST_INTENT",
                            "Hospital A is short on IV fluids")

    # --- The three negotiation agents (Bureau / in-process mode, NOT mailbox) -
    front = build_hospital_agent("Hospital A")
    hospital_b = build_hospital_agent("Hospital B")
    hospital_c = build_hospital_agent("Hospital C")

    attach_front_handlers(front)
    attach_hospital_handlers(hospital_b, "Hospital B")
    attach_hospital_handlers(hospital_c, "Hospital C")

    # Attach the SAME chat protocol the live agent uses (exercise the real path).
    front.include(build_chat_protocol(on_intent), publish_manifest=True)

    # --- The collector: stands in for the ASI:One chat user -------------------
    collector = Agent(
        name="baymax_selftest_collector",
        seed="baymax-selftest-collector-seed",
        port=8009,
        network=FET_NETWORK,
    )
    narrated: list[str] = []

    async def _collect(ctx: Context, sender: str, text: str) -> None:
        narrated.append(text)
        ctx.logger.info(f"[NARRATION #{len(narrated)}] {text}")

    collector.include(build_chat_protocol(_collect), publish_manifest=True)
    collector_addr = collector.address

    @front.on_event("startup")
    async def _feed(ctx: Context):
        ctx.logger.info("=== SELF-TEST: driving on_intent with a synthetic intent ===")
        ctx.logger.info(f"=== SELF-TEST intent: {intent_text!r} "
                        f"(reply_to=collector {collector_addr}) ===")
        # Exercise the real on_intent path with the collector as the chat sender,
        # so every narrated milestone is streamed to + recorded by the collector.
        await on_intent(ctx, collector_addr, intent_text)

    bureau = Bureau()
    bureau.add(front)
    bureau.add(hospital_b)
    bureau.add(hospital_c)
    bureau.add(collector)
    bureau.run()


# ---------------------------------------------------------------------------
# Entrypoint.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if os.getenv("BAYMAX_SELFTEST", "").lower() in ("1", "true", "yes"):
        run_selftest()
    else:
        agent = build_front_agent()
        print("=" * 70)
        print("Baymax FRONT agent (Hospital A) — ASI:One entrypoint")
        print(f"  address : {agent.address}")
        print(f"  network : {os.getenv('FETCH_NETWORK', 'testnet')} (TESTNET ONLY)")
        print("-" * 70)
        print("Manual ASI:One steps:")
        print("  1. Run this file:            python front_agent.py")
        print("  2. Open the Inspector URL printed below by uAgents.")
        print("  3. In Agentverse: Connect -> Mailbox (one-time) for this agent.")
        print("  4. Find the agent on ASI:One (https://asi1.ai) and chat it, e.g.")
        print("       \"Hospital A is short on IV fluids\"")
        print("  5. Watch the negotiation milestones stream back into the chat.")
        print("=" * 70)
        agent.run()
