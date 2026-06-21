"""claude_intent_check.py — bureau-free verification for the intent parsers.

Two phases, both self-contained (NO uagents Agent/Bureau — port 8000 may be held
by a live demo):

  A) Deterministic baseline: run front_agent.parse_intent over a battery of
     canonical phrasings and assert the expected kind/item. Locks in current
     behaviour so the Claude parser can be compared against it.

  B) Claude parser (only if ANTHROPIC_API_KEY is set): run
     claude_intent.parse_intent_llm over the SAME battery PLUS hard/ambiguous/typo
     cases, and assert the parsed kind/item are sane and match the contract.

Prints per-case results and a final PASS/FAIL, then sys.exit(0/1).

Run:  ./.venv/bin/python claude_intent_check.py
"""

from __future__ import annotations

import os
import sys

# Load .env so ANTHROPIC_API_KEY is available (mirrors how the agents load it).
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

import front_agent
from front_agent import KNOWN_ITEMS, REQUESTER, parse_intent

_PASS = "PASS"
_FAIL = "FAIL"


def _check(label, got_kind, want_kind, got_item=None, want_item=None, extra=""):
    """Print one case line; return True iff it passed."""
    ok = got_kind == want_kind
    if want_item is not None:
        ok = ok and (got_item == want_item)
    status = _PASS if ok else _FAIL
    item_str = f"  item={got_item!r}" if got_item is not None or want_item is not None else ""
    print(f"  [{status}] {label!r:<48} -> kind={got_kind!r}{item_str}{(' ' + extra) if extra else ''}")
    if not ok:
        exp = f"kind={want_kind!r}" + (f" item={want_item!r}" if want_item is not None else "")
        print(f"           expected {exp}")
    return ok


# (text, expected_kind, expected_item-or-None) — canonical phrasings the
# deterministic parser already handles. expected_item=None means "don't assert item".
_BASELINE = [
    ("Hospital A is short on IV fluids", "request", "IV fluids"),
    ("we're short 200 saline", "request", "saline"),
    ("need sutures at Hospital A", "request", "sutures"),
    ("Hospital A needs 150 IV fluids", "request", "IV fluids"),
    ("running low on saline at hospital a", "request", "saline"),
    ("order 500 saline", "order", "saline"),
    ("buy 300 sutures", "order", "sutures"),
    ("hi", "greeting", None),
    ("what can you do", "greeting", None),
    ("the weather is nice today", "unknown", None),
    ("", "greeting", None),
]

# Hard / ambiguous / typo cases — exercised ONLY against the Claude parser.
# (text, expected_kind, expected_item-or-None)
_LLM_HARD = [
    ("we ran outta IV bags at site A", "request", "IV fluids"),
    ("urgently need ~200 units of saline asap", "request", "saline"),
    ("hey what can you do", "greeting", None),
    ("Hospital B is completely out of stitches", "request", "sutures"),
    ("can you procure another 400 normal saline for us", "order", "saline"),
    ("pizza toppings inventory is low", "unknown", None),
]


def run_deterministic() -> bool:
    print("Phase A — deterministic parse_intent (baseline):")
    all_ok = True
    for text, want_kind, want_item in _BASELINE:
        got = parse_intent(text)
        ok = _check(text, got.get("kind"), want_kind, got.get("item"), want_item)
        # Contract assertions on the structured shape:
        if got.get("kind") == "request":
            ok = ok and got.get("item") in KNOWN_ITEMS and isinstance(got.get("requester"), str)
            qn = got.get("quantity_needed")
            ok = ok and (qn is None or isinstance(qn, int))
        elif got.get("kind") == "order":
            ok = ok and got.get("item") in KNOWN_ITEMS and isinstance(got.get("requester"), str)
            q = got.get("quantity")
            ok = ok and (q is None or isinstance(q, int))
        all_ok = all_ok and ok
    return all_ok


def _validate_llm_shape(got: dict) -> bool:
    """Assert the LLM result matches the parse_intent contract exactly."""
    kind = got.get("kind")
    if kind not in {"greeting", "request", "order", "unknown", "ignored"}:
        return False
    if kind == "request":
        return (
            got.get("item") in KNOWN_ITEMS
            and isinstance(got.get("requester"), str)
            and (got.get("quantity_needed") is None or isinstance(got.get("quantity_needed"), int))
        )
    if kind == "order":
        return (
            got.get("item") in KNOWN_ITEMS
            and isinstance(got.get("requester"), str)
            and (got.get("quantity") is None or isinstance(got.get("quantity"), int))
        )
    if kind == "ignored":
        return isinstance(got.get("reason"), str)
    return True  # greeting / unknown carry no extra required keys


def run_llm() -> bool:
    from claude_intent import parse_intent_llm

    all_ok = True

    print("\nPhase B.1 — Claude parse_intent_llm on the baseline battery:")
    for text, want_kind, want_item in _BASELINE:
        got = parse_intent_llm(text)
        ok = _check(text, got.get("kind"), want_kind, got.get("item"), want_item)
        ok = ok and _validate_llm_shape(got)
        all_ok = all_ok and ok

    print("\nPhase B.2 — Claude parse_intent_llm on hard / ambiguous / typo cases:")
    for text, want_kind, want_item in _LLM_HARD:
        got = parse_intent_llm(text)
        ok = _check(text, got.get("kind"), want_kind, got.get("item"), want_item)
        ok = ok and _validate_llm_shape(got)
        all_ok = all_ok and ok

    print("\nPhase B.3 — resolver wiring (BAYMAX_CLAUDE_INTENT on -> Claude parser):")
    os.environ["BAYMAX_CLAUDE_INTENT"] = "1"
    parser = front_agent._resolve_parser()
    is_wrapper = getattr(parser, "__name__", "") == "_claude_then_fallback"
    print(f"  [{_PASS if is_wrapper else _FAIL}] resolver returned the Claude wrapper "
          f"(name={getattr(parser, '__name__', '?')!r})")
    all_ok = all_ok and is_wrapper
    if is_wrapper:
        got = parser("Hospital A is short on IV fluids")
        ok = _check("resolver('Hospital A is short on IV fluids')",
                    got.get("kind"), "request", got.get("item"), "IV fluids")
        all_ok = all_ok and ok

    return all_ok


def main() -> int:
    print("=" * 72)
    print("Baymax intent-parser verification (bureau-free)")
    print("=" * 72)

    ok = run_deterministic()

    # Default-resolver sanity: with no env, the resolver MUST be the exact
    # deterministic parse_intent (byte-for-byte fallback guarantee).
    os.environ.pop("BAYMAX_CLAUDE_INTENT", None)
    default_parser = front_agent._resolve_parser()
    default_ok = default_parser is parse_intent
    print("\nDefault resolver (no BAYMAX_CLAUDE_INTENT) is the deterministic parser:")
    print(f"  [{_PASS if default_ok else _FAIL}] _resolve_parser() is parse_intent")
    ok = ok and default_ok

    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            ok = run_llm() and ok
        except Exception as exc:  # surface, don't mask — a broken LLM path is a FAIL
            print(f"\n  [{_FAIL}] Claude parser raised: {type(exc).__name__}: {exc}")
            ok = False
    else:
        print("\nPhase B — SKIPPED (ANTHROPIC_API_KEY not set).")

    print("\n" + "=" * 72)
    print(f"RESULT: {_PASS if ok else _FAIL}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
