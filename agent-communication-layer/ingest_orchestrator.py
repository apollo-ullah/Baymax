"""ingest_orchestrator.py — the REAL backend of the forecast-ingest seam,
behind interfaces.ingest_forecast().

The "Ingest Data" loop (WHO + weather + illness + CDC stub -> Redis -> Claude
reasoning -> proactive recommendation) already exists, unlabeled, in
fetch/agents/who_agent/fetcher.py::run_who_update. This module is a thin,
fail-closed BRIDGE from the agent layer to that orchestrator — it does NOT
re-implement the loop (the realignment's "one engine, no duplicate
implementations" rule). It mirrors redis_inventory.py / claude_ranking.py:

  * interfaces.ingest_forecast() delegates here only when BAYMAX_INGEST=1, and
    falls back to a deterministic mock on ANY failure (missing fetch/Redis
    stack, network error, malformed output) — so the offline harnesses never
    pull httpx/redis/anthropic.
  * NO uagents dependency. The heavy cross-track import (the WHO fetcher, which
    pulls httpx + redis) is lazy, inside run_ingest().
  * Returns interfaces.ForecastRecommendation (never redefined here).
  * SYNC. The agent layer calls it off the event loop via asyncio.to_thread().

Env:
    BAYMAX_INGEST   1/true -> interfaces.ingest_forecast() uses this live backend
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from interfaces import ForecastRecommendation  # never redefine

# Repo root (…/CalHax) so `import fetch.agents.who_agent.fetcher` resolves the
# same way ui/app.py imports it. agent-communication-layer/ingest_orchestrator.py
# -> parents[1] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]


def ingest_enabled() -> bool:
    """True when the live ingest backend should be used (opt-in)."""
    return os.getenv("BAYMAX_INGEST", "").strip().lower() in ("1", "true", "yes", "on")


def run_ingest(region: str = "san_francisco") -> ForecastRecommendation:
    """Run the live WHO + weather + illness (+ CDC stub) ingest and return the
    Claude proactive recommendation as a ForecastRecommendation.

    Raises on ANY failure so interfaces.ingest_forecast() falls back to the mock.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    # Lazy: pulls httpx + redis + (optionally) anthropic. B2 owns the internals
    # (CDC wiring, the upsert fix); this bridge only adapts the return shape.
    from fetch.agents.who_agent.fetcher import run_who_update

    raw = run_who_update(region=region)
    if not isinstance(raw, dict):
        raise ValueError(f"run_who_update returned {type(raw)!r}, expected dict")

    priority = raw.get("priority_items") or []
    if not isinstance(priority, list):
        priority = [str(priority)]

    return ForecastRecommendation(
        risk_level=str(raw.get("risk_level", "low")),
        priority_items=[str(p) for p in priority],
        reasoning=str(raw.get("reasoning", "")),
        recommended_action=str(raw.get("recommended_action", "")),
        region=region,
        updated_at=str(raw.get("updated_at", "")),
    )
