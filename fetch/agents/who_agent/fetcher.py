"""
WHO historical disease data fetcher + Claude reasoning layer.

Data source: disease.sh  (https://disease.sh) — aggregates WHO / JHU CSSE.
No API key required.

Callable standalone or from the uAgent:
    from fetch.agents.who_agent.fetcher import run_who_update
    result = run_who_update(region="san_francisco")

Writes two Redis keys:
    forecast:{region}  — adds who_* fields alongside weather / illness
    reasoning:latest   — Claude's supply-risk interpretation

Claude model: claude-haiku-4-5-20251001 (fast, structured JSON output)
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("who_fetcher")

# ── Paths ────────────────────────────────────────────────────────────────────
_AGENT_ROOT = Path(__file__).resolve().parents[1]   # fetch/agents/
_MOCK_ILLNESS_PATH = _AGENT_ROOT / "illness_agent" / "mock_illness_feed.json"

# Open-Meteo defaults (San Francisco)
_WEATHER_LAT = float(os.getenv("WEATHER_LATITUDE", "37.7749"))
_WEATHER_LNG = float(os.getenv("WEATHER_LONGITUDE", "-122.4194"))

# ── Bridge to redis/src ─────────────────────────────────────────────────────
_REDIS_SRC = Path(__file__).resolve().parents[3] / "redis" / "src"
if str(_REDIS_SRC) not in sys.path:
    sys.path.insert(0, str(_REDIS_SRC))

from redis_client import get_redis  # noqa: E402
from forecast import get_forecast, write_forecast  # noqa: E402

# ── disease.sh endpoint ─────────────────────────────────────────────────────
DISEASE_SH_URL = "https://disease.sh/v3/covid-19/historical/all?lastdays=30"
ATTRIBUTION = "disease.sh (WHO/JHU CSSE)"

# ── Redis key for Claude reasoning output ───────────────────────────────────
REASONING_KEY = "reasoning:latest"


# ── WHO data fetch ──────────────────────────────────────────────────────────

def fetch_who_covid(lastdays: int = 30) -> dict:
    """
    Call disease.sh for global COVID-19 historical data.
    Returns {cases: {date: n}, deaths: {date: n}, recovered: {date: n}}.
    Raises httpx.HTTPError on network failure.
    """
    url = f"https://disease.sh/v3/covid-19/historical/all?lastdays={lastdays}"
    log.info(json.dumps({
        "tag": "WHO", "file": "who_agent/fetcher.py",
        "action": "api_request",
        "endpoint": url,
        "params": {"lastdays": lastdays},
        "source": ATTRIBUTION,
    }))

    with httpx.Client(timeout=15) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    log.info(json.dumps({
        "tag": "WHO", "file": "who_agent/fetcher.py",
        "action": "api_response",
        "source": ATTRIBUTION,
        "date_count": len(data.get("cases", {})),
        "sample_keys": list(data.get("cases", {}).keys())[:3],
    }))
    return data


def parse_covid_stats(raw: dict) -> dict:
    """
    Derive 30-day totals and 7-day trend from cumulative daily snapshots.

    disease.sh returns CUMULATIVE counts, so we diff adjacent days to get
    daily new cases/deaths, then sum windows.

    Returns:
        {
          "cases_30d": int,
          "deaths_30d": int,
          "trend": "rising" | "stable" | "falling",
          "trend_pct": float,   # % change: last-7d vs prior-7d new cases
          "last_date": str,
        }
    """
    cases_raw = raw.get("cases", {})
    deaths_raw = raw.get("deaths", {})

    # Sort chronologically
    case_dates = sorted(cases_raw.keys())
    death_dates = sorted(deaths_raw.keys())

    def daily_deltas(cum: dict, dates: list) -> list:
        vals = [cum[d] for d in dates]
        return [max(0, vals[i] - vals[i - 1]) for i in range(1, len(vals))]

    case_deltas = daily_deltas(cases_raw, case_dates)
    death_deltas = daily_deltas(deaths_raw, death_dates)

    cases_30d = sum(case_deltas)
    deaths_30d = sum(death_deltas)

    # 7-day trend: last 7 daily deltas vs prior 7
    if len(case_deltas) >= 14:
        last7 = sum(case_deltas[-7:])
        prior7 = sum(case_deltas[-14:-7])
        if prior7 > 0:
            pct = round((last7 - prior7) / prior7 * 100, 1)
        else:
            pct = 0.0
        if pct > 5:
            trend = "rising"
        elif pct < -5:
            trend = "falling"
        else:
            trend = "stable"
    else:
        pct = 0.0
        trend = "stable"

    result = {
        "cases_30d": cases_30d,
        "deaths_30d": deaths_30d,
        "trend": trend,
        "trend_pct": pct,
        "last_date": case_dates[-1] if case_dates else "unknown",
    }
    log.info(json.dumps({
        "tag": "WHO", "file": "who_agent/fetcher.py",
        "action": "parsed_disease_counts",
        "disease": "covid-19",
        "timespan": "30_days",
        "historical_cases": cases_30d,
        "historical_deaths": deaths_30d,
        "trend": trend,
        "trend_pct_7d": pct,
        "last_date": result["last_date"],
    }))
    return result


# ── Claude reasoning ─────────────────────────────────────────────────────────

def reason_with_claude(
    region: str,
    covid_stats: dict,
    forecast_items: dict,
    inventory_snapshot: Optional[dict] = None,
    vision_snapshot: Optional[dict] = None,
    scenario_snapshot: Optional[dict] = None,
) -> dict:
    """
    Ask Claude to interpret the combined disease + weather + inventory context
    and produce a structured supply-risk recommendation.

    Returns a dict with keys: risk_level, priority_items, reasoning,
    recommended_action, updated_at.
    Falls back to a rule-based default if the API call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning(json.dumps({
            "tag": "CLAUDE_REASONING", "file": "who_agent/fetcher.py",
            "action": "skip", "reason": "ANTHROPIC_API_KEY not set",
        }))
        return _fallback_reasoning(covid_stats, forecast_items)

    try:
        import anthropic
    except ImportError:
        log.warning(json.dumps({
            "tag": "CLAUDE_REASONING", "file": "who_agent/fetcher.py",
            "action": "skip", "reason": "anthropic package not installed",
        }))
        return _fallback_reasoning(covid_stats, forecast_items)

    # Strip raw camera text from vision before sending to Claude (already parsed)
    vision_counts = {}
    if vision_snapshot:
        vision_counts = {
            k: v for k, v in vision_snapshot.items()
            if k in ("hospital_a", "hospital_b") and isinstance(v, dict)
        }

    context = {
        "region": region,
        "weather_live": {
            "temperature_c": forecast_items.get("weather_temperature_c"),
            "windspeed_kmh": forecast_items.get("weather_windspeed"),
            "weather_code": forecast_items.get("weather_code"),
            "source": "Open-Meteo (live)",
        },
        "illness_levels": {
            **(forecast_items.get("illness", {})),
            "_source": "mock feed (CDC not integrated)",
        },
        "who_covid_30d": {
            "new_cases": covid_stats["cases_30d"],
            "new_deaths": covid_stats["deaths_30d"],
            "7day_trend": covid_stats["trend"],
            "trend_change_pct": covid_stats["trend_pct"],
            "last_date": covid_stats["last_date"],
            "source": ATTRIBUTION,
        },
        "hospital_inventory": inventory_snapshot or {},
        "camera_vision_counts": vision_counts or "no capture yet",
        "active_scenario": scenario_snapshot or "none",
    }

    prompt = (
        "You are a hospital supply chain risk analyst for a regional hospital network. "
        "Review ALL of the following real-time data sources:\n\n"
        f"{json.dumps(context, indent=2)}\n\n"
        "Weather code key: 0=clear, 45=fog, 61-67=rain, 71-77=snow, 95+=thunderstorm. "
        "Illness scale: Minimal < Low < Moderate < High < Very High. "
        "Inventory qty is units on hand; surplus is available to transfer.\n\n"
        "Combine ALL signals (disease burden, weather, illness levels, camera counts, "
        "current stock, active scenario) to assess supply risk for the next 7 days.\n\n"
        "Respond ONLY with valid JSON (no markdown fences, no extra text):\n"
        '{"risk_level": "low|medium|high|critical", '
        '"priority_items": ["item1", "item2"], '
        '"reasoning": "2-3 sentences citing specific numbers from the data", '
        '"recommended_action": "one concrete action the supply manager should take right now"}'
    )

    log.info(json.dumps({
        "tag": "CLAUDE_REASONING", "file": "who_agent/fetcher.py",
        "action": "claude_request",
        "model": "claude-haiku-4-5-20251001",
        "context_keys": list(context.keys()),
        "weather": context["weather_live"],
        "illness": context["illness_levels"],
        "disease_data": context["who_covid_30d"],
        "hospitals": list((inventory_snapshot or {}).keys()),
        "vision_counts": vision_counts,
        "has_scenario": bool(scenario_snapshot),
    }))

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = msg.content[0].text.strip()

    log.info(json.dumps({
        "tag": "CLAUDE_REASONING", "file": "who_agent/fetcher.py",
        "action": "claude_response",
        "model": "claude-haiku-4-5-20251001",
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "raw": raw_text,
    }))

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        # Claude sometimes wraps in ```json — strip fences
        cleaned = raw_text.strip("`").removeprefix("json").strip()
        result = json.loads(cleaned)

    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    log.info(json.dumps({
        "tag": "CLAUDE_REASONING", "file": "who_agent/fetcher.py",
        "action": "recommendation",
        "risk_level": result.get("risk_level"),
        "priority_items": result.get("priority_items"),
        "reasoning": result.get("reasoning"),
        "recommended_action": result.get("recommended_action"),
    }))
    return result


def _fallback_reasoning(covid_stats: dict, forecast_items: dict) -> dict:
    """Rule-based fallback when Claude is unavailable."""
    illness = forecast_items.get("illness", {})
    high_illness = any(
        v in ("High", "Very High") for v in illness.values()
    )
    rising = covid_stats.get("trend") == "rising"
    risk = "high" if (high_illness and rising) else "medium" if high_illness or rising else "low"
    return {
        "risk_level": risk,
        "priority_items": ["IV Fluids", "Saline"] if risk in ("high", "critical") else ["N95 Masks"],
        "reasoning": (
            f"Rule-based fallback (Claude unavailable). "
            f"COVID trend={covid_stats.get('trend')}, "
            f"illness={illness}."
        ),
        "recommended_action": "Check API key and retry for Claude reasoning.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Weather helper (Open-Meteo, no key) ──────────────────────────────────────

def _fetch_weather_items() -> dict:
    """
    Pull current weather from Open-Meteo (free, no key required).
    Returns keys matching what weather_agent writes to forecast:{region}.
    Falls back to empty dict on any network error.
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={_WEATHER_LAT}&longitude={_WEATHER_LNG}&current_weather=true"
    )
    log.info(json.dumps({
        "tag": "INGESTION", "file": "who_agent/fetcher.py",
        "action": "weather_api_request",
        "endpoint": url, "source": "open-meteo (no key)",
    }))
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            resp.raise_for_status()
            cw = resp.json().get("current_weather", {})
        items = {
            "weather_temperature_c": cw.get("temperature"),
            "weather_windspeed":     cw.get("windspeed"),
            "weather_code":          cw.get("weathercode"),
        }
        log.info(json.dumps({
            "tag": "INGESTION", "file": "who_agent/fetcher.py",
            "action": "weather_api_response", "payload": items,
        }))
        return items
    except Exception as e:
        log.warning(json.dumps({
            "tag": "INGESTION", "file": "who_agent/fetcher.py",
            "action": "weather_api_error", "error": str(e),
        }))
        return {}


# ── Illness helper (mock feed, same source as illness_agent) ──────────────────

def _read_illness_items(region: str) -> dict:
    """
    Read illness levels from the mock_illness_feed.json (same file the
    illness_agent polls). Falls back to empty dict if file is missing.
    Returns {"illness": {"influenza": "Very High", ...}}.
    """
    try:
        with open(_MOCK_ILLNESS_PATH) as f:
            feed = json.load(f)
        levels = feed.get("regions", {}).get(region, {})
        log.info(json.dumps({
            "tag": "INGESTION", "file": "who_agent/fetcher.py",
            "action": "illness_feed_read",
            "source": "mock_illness_feed.json",
            "region": region, "levels": levels,
        }))
        return {"illness": levels} if levels else {}
    except Exception as e:
        log.warning(json.dumps({
            "tag": "INGESTION", "file": "who_agent/fetcher.py",
            "action": "illness_feed_error", "error": str(e),
        }))
        return {}


# ── Inventory snapshot helper ─────────────────────────────────────────────────

def _get_inventory_snapshot() -> dict:
    """Read inventory for all three hospitals from Redis."""
    try:
        client = get_redis()
        out = {}
        for hid in ("hospital_a", "hospital_b", "hospital_c"):
            raw = client.hgetall(f"hospital:{hid}:inventory")
            sur = client.hgetall(f"hospital:{hid}:surplus")
            out[hid] = {
                item: {**json.loads(val), "surplus": int(sur.get(item, 0))}
                for item, val in raw.items()
            }
        return out
    except Exception:
        return {}


def _get_vision_snapshot() -> dict:
    """Read latest camera vision counts from Redis (vision:latest)."""
    try:
        client = get_redis()
        raw = client.get("vision:latest")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _get_scenario_snapshot() -> dict:
    """Read the active heatstroke scenario from Redis."""
    try:
        client = get_redis()
        raw = client.get("scenario:heatstroke")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


# ── Main orchestrator ─────────────────────────────────────────────────────────

def run_who_update(region: str = "san_francisco") -> dict:
    """
    Full pipeline:  disease.sh → parse → Claude reasoning → Redis write.

    Returns the reasoning dict. On WHO API failure, skips to Claude with
    whatever data is already in Redis and logs the error.
    """
    # Step 1: fetch WHO / disease.sh data
    covid_stats = None
    try:
        raw = fetch_who_covid(lastdays=30)
        covid_stats = parse_covid_stats(raw)
    except Exception as e:
        log.error(json.dumps({
            "tag": "WHO", "file": "who_agent/fetcher.py",
            "action": "api_error", "error": str(e),
            "note": "falling back to zero counts",
        }))
        covid_stats = {
            "cases_30d": 0, "deaths_30d": 0,
            "trend": "unknown", "trend_pct": 0.0, "last_date": "unknown",
        }

    # Step 2: fetch weather (Open-Meteo, live, no key) + illness (mock feed)
    weather_items = _fetch_weather_items()
    illness_items = _read_illness_items(region)

    # Step 3: merge WHO + weather + illness into forecast:{region}
    try:
        r = get_redis()
        existing_raw = r.get(f"forecast:{region}")
        existing = json.loads(existing_raw) if existing_raw else {}
        items = dict(existing.get("items", {}))
        items.update({
            "who_covid_30d_cases": covid_stats["cases_30d"],
            "who_covid_30d_deaths": covid_stats["deaths_30d"],
            "who_covid_trend": covid_stats["trend"],
            "who_covid_trend_pct": covid_stats["trend_pct"],
            "who_last_date": covid_stats["last_date"],
            "who_source": ATTRIBUTION,
        })
        items.update(weather_items)
        items.update(illness_items)
        record = {
            "region": region,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }
        r.set(f"forecast:{region}", json.dumps(record))
        log.info(json.dumps({
            "tag": "WHO", "file": "who_agent/fetcher.py",
            "action": "redis_write",
            "key": f"forecast:{region}",
            "who_fields":     {k: v for k, v in items.items() if k.startswith("who_")},
            "weather_fields": {k: v for k, v in items.items() if k.startswith("weather_")},
            "illness_fields": items.get("illness", {}),
        }))
    except Exception as e:
        log.error(json.dumps({
            "tag": "WHO", "file": "who_agent/fetcher.py",
            "action": "redis_write_error", "error": str(e),
        }))
        items = {}

    # Step 4: gather remaining context for Claude
    inventory_snapshot = _get_inventory_snapshot()
    vision_snapshot    = _get_vision_snapshot()
    scenario_snapshot  = _get_scenario_snapshot()

    # Step 5: Claude reasoning — full context: WHO + weather + illness + inventory + vision + scenario
    reasoning = reason_with_claude(
        region=region,
        covid_stats=covid_stats,
        forecast_items=items,
        inventory_snapshot=inventory_snapshot,
        vision_snapshot=vision_snapshot,
        scenario_snapshot=scenario_snapshot,
    )

    # Step 6: write reasoning to Redis
    try:
        r = get_redis()
        r.set(REASONING_KEY, json.dumps(reasoning))
        log.info(json.dumps({
            "tag": "CLAUDE_REASONING", "file": "who_agent/fetcher.py",
            "action": "redis_write",
            "key": REASONING_KEY,
            "payload": reasoning,
        }))
    except Exception as e:
        log.error(json.dumps({
            "tag": "CLAUDE_REASONING", "file": "who_agent/fetcher.py",
            "action": "redis_write_error", "error": str(e),
        }))

    return reasoning
