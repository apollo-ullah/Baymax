"""check_research_crisis.py — offline unit check for the Wave-A crisis-research
and forecast-ingest seams (interfaces.research_crisis / ingest_forecast).

Mock-only: asserts crisis-type classification, that every at-risk item is a
canonical KNOWN_ITEMS string, that the top item is usable by the negotiation,
and that the ingest mock returns a well-formed recommendation. No network, no
key, no Redis — mirrors check_interfaces_order.py / check_parse_decision.py.

Run:  python check_research_crisis.py   (must print ALL CHECKS PASSED, exit 0)
"""

from __future__ import annotations

import sys

import interfaces
from interfaces import (
    CrisisBrief,
    ForecastRecommendation,
    KNOWN_ITEMS,
    ingest_forecast,
    research_crisis,
)

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok  - {msg}")
    else:
        print(f"  FAIL- {msg}")
        _failures.append(msg)


# --- research_crisis: classification + at-risk mapping ---------------------
CASES = [
    ("wildfires near Hospital A", "wildfire", "saline"),
    ("there's a wildfire and the smoke is bad", "wildfire", "saline"),
    ("extreme heatwave hitting the region", "heatwave", "IV fluids"),
    ("flu surge / respiratory outbreak downtown", "flu_surge", "IV fluids"),
    ("major earthquake, mass casualty event", "earthquake", "sutures"),
    ("severe flooding from the storm", "storm", "IV fluids"),
    ("something weird is happening", "unknown", "IV fluids"),  # default path
]

print("== research_crisis (mock) ==")
for text, want_type, want_top in CASES:
    brief = research_crisis(text)
    check(isinstance(brief, CrisisBrief), f"{text!r} -> CrisisBrief")
    check(brief.crisis_type == want_type,
          f"{text!r} -> type {brief.crisis_type!r} (want {want_type!r})")
    check(len(brief.at_risk) >= 1, f"{text!r} -> >=1 at-risk item")
    check(all(a.item in KNOWN_ITEMS for a in brief.at_risk),
          f"{text!r} -> all at-risk items are canonical KNOWN_ITEMS")
    check(brief.top_item == want_top,
          f"{text!r} -> top item {brief.top_item!r} (want {want_top!r})")
    check(bool(brief.rationale), f"{text!r} -> non-empty rationale")

# Empty/None input must not crash and must still yield a usable item.
empty = research_crisis("")
check(empty.top_item in KNOWN_ITEMS, "empty crisis text -> usable default item")

# --- the seam must remain fail-closed: mock used when Claude not enabled ----
import claude_research  # noqa: E402
check(claude_research.claude_research_enabled() is False,
      "claude_research disabled by default (no BAYMAX_CLAUDE_RESEARCH/key)")

# --- ingest_forecast: mock recommendation ----------------------------------
print("== ingest_forecast (mock) ==")
rec = ingest_forecast()
check(isinstance(rec, ForecastRecommendation), "ingest_forecast -> ForecastRecommendation")
check(rec.risk_level in ("low", "medium", "high", "critical"), "valid risk_level")
check(len(rec.priority_items) >= 1, ">=1 priority item")
check(bool(rec.recommended_action), "non-empty recommended_action")

import ingest_orchestrator  # noqa: E402
check(ingest_orchestrator.ingest_enabled() is False,
      "ingest disabled by default (no BAYMAX_INGEST) -> mock path")

print()
if _failures:
    print(f"RESEARCH/INGEST CHECK FAILED: {len(_failures)} assertion(s) failed")
    sys.exit(1)
print("ALL CHECKS PASSED — research_crisis + ingest_forecast seams OK (mock)")
