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

# ── disease.sh endpoints ────────────────────────────────────────────────────
DISEASE_SH_URL = "https://disease.sh/v3/covid-19/historical/all?lastdays=30"
# CDC US Clinical Labs influenza surveillance (% specimens positive for flu).
INFLUENZA_SH_URL = "https://disease.sh/v3/influenza/CDC/USCL"
ATTRIBUTION = "disease.sh (WHO/JHU CSSE)"
ATTRIBUTION_FLU = "disease.sh · CDC US Clinical Labs (influenza surveillance)"

# ── CDC feed: severity ordering + illness → critical supplies ────────────────
# The CDC illness feed (mock_illness_feed.json, hardcoded demo) decides which
# illness the WHO query and Claude reasoning focus on — the worst-rated one.
_ILLNESS_SEVERITY = {"Minimal": 0, "Low": 1, "Moderate": 2, "High": 3, "Very High": 4}

# Hardcoded illness → critical-supplies guideline (the "what we need" summary).
# Stands in for a CDC supply guideline until that feed is integrated.
_ILLNESS_SUPPLIES = {
    "influenza": ["IV Fluids", "Saline", "Antivirals", "N95 Masks"],
    "covid":     ["N95 Masks", "Oxygen", "Ventilators", "IV Fluids"],
    "rsv":       ["Oxygen", "Nebulizers", "IV Fluids"],
}
_DEFAULT_SUPPLIES = ["IV Fluids", "Saline"]

CDC_FEED_SOURCE = "CDC illness surveillance feed (hardcoded demo)"

# ── Redis key for Claude reasoning output ───────────────────────────────────
REASONING_KEY = "reasoning:latest"


# ── CDC focus-illness selection ──────────────────────────────────────────────

def determine_focus_illness(region: str) -> dict:
    """
    Pick the illness the CDC feed rates worst for ``region`` — this is what the
    WHO query and Claude reasoning center on. Falls back to influenza so the
    pipeline always has a focus even when the feed is missing.

    Returns {"illness": str, "level": str}.
    """
    levels = (_read_illness_items(region) or {}).get("illness", {})
    if not levels:
        return {"illness": "influenza", "level": "Unknown"}
    illness, level = max(
        levels.items(), key=lambda kv: _ILLNESS_SEVERITY.get(kv[1], -1)
    )
    log.info(json.dumps({
        "tag": "WHO", "file": "who_agent/fetcher.py",
        "action": "focus_illness_selected",
        "source": CDC_FEED_SOURCE,
        "region": region, "illness": illness, "level": level,
        "all_levels": levels,
    }))
    return {"illness": illness, "level": level}


def supplies_for_illness(illness: str) -> list:
    """Critical supplies the focus illness implies (the 'what we need' list)."""
    return _ILLNESS_SUPPLIES.get((illness or "").lower(), _DEFAULT_SUPPLIES)


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


# ── Influenza fetch (disease.sh / CDC US Clinical Labs) ──────────────────────

def _num(d: dict, *keys):
    """First present numeric value among ``keys`` (tolerates key-spelling drift)."""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            try:
                return float(d[k])
            except (TypeError, ValueError):
                continue
    return None


def fetch_influenza_cdc() -> dict:
    """
    Pull current-season CDC influenza surveillance from disease.sh and normalise
    it to the same shape as the COVID stats so the dashboard renders generically.

    Headline metric = % of clinical-lab specimens testing positive for flu;
    trend compares the latest reported week to the prior one. Raises on failure.
    """
    log.info(json.dumps({
        "tag": "WHO", "file": "who_agent/fetcher.py",
        "action": "api_request", "endpoint": INFLUENZA_SH_URL,
        "disease": "influenza", "source": ATTRIBUTION_FLU,
    }))
    with httpx.Client(timeout=15) as client:
        resp = client.get(INFLUENZA_SH_URL)
        resp.raise_for_status()
        data = resp.json()

    weeks = data if isinstance(data, list) else data.get("data") or []
    if not weeks:
        raise ValueError("influenza endpoint returned no weekly rows")

    latest = weeks[-1]
    prior = weeks[-2] if len(weeks) >= 2 else {}

    pct = _num(latest, "percentpositive", "percentPositive", "percent_positive")
    a = _num(latest, "totala", "totalA", "a") or 0
    b = _num(latest, "totalb", "totalB", "b") or 0
    specimens = _num(latest, "totalspecimens", "totalSpecimens", "total_specimens")
    positives = int(a + b)
    if pct is None and specimens:
        pct = round((a + b) / specimens * 100, 1)

    prior_pct = _num(prior, "percentpositive", "percentPositive", "percent_positive")
    if pct is not None and prior_pct not in (None, 0):
        trend_pct = round((pct - prior_pct) / prior_pct * 100, 1)
    else:
        trend_pct = 0.0
    trend = "rising" if trend_pct > 5 else "falling" if trend_pct < -5 else "stable"

    week = (latest.get("week") or latest.get("weekending")
            or latest.get("weekEnding") or "current week")

    stats = {
        "illness": "influenza",
        "headline_label": "specimens positive for flu",
        "headline_value": f"{pct}%" if pct is not None else "–",
        "secondary_label": "positive specimens (A+B)",
        "secondary_value": f"{positives:,}",
        "trend": trend,
        "trend_pct": trend_pct,
        "last_date": str(week),
        "source": ATTRIBUTION_FLU,
    }
    log.info(json.dumps({
        "tag": "WHO", "file": "who_agent/fetcher.py",
        "action": "parsed_disease_counts", "disease": "influenza",
        "percent_positive": pct, "positive_specimens": positives,
        "trend": trend, "trend_pct_7d": trend_pct, "last_week": str(week),
    }))
    return stats


def _covid_to_stats(covid_stats: dict) -> dict:
    """Normalise the COVID 30-day stats into the generic disease-stats shape."""
    return {
        "illness": "covid",
        "headline_label": "covid 30d cases",
        "headline_value": f"{covid_stats.get('cases_30d', 0):,}",
        "secondary_label": "covid 30d deaths",
        "secondary_value": f"{covid_stats.get('deaths_30d', 0):,}",
        "trend": covid_stats.get("trend", "unknown"),
        "trend_pct": covid_stats.get("trend_pct", 0.0),
        "last_date": covid_stats.get("last_date", "unknown"),
        "source": ATTRIBUTION,
    }


def _synthetic_stats(illness: str, level: str) -> dict:
    """
    Hardcoded fallback when the focus illness has no live disease.sh endpoint
    (e.g. RSV) or the live call fails — keeps the visualization aligned to the
    CDC-flagged illness using its severity level.
    """
    rising = level in ("High", "Very High")
    return {
        "illness": illness,
        "headline_label": "CDC activity level",
        "headline_value": level or "Unknown",
        "secondary_label": "surveillance",
        "secondary_value": "no live disease.sh feed",
        "trend": "rising" if rising else "stable",
        "trend_pct": 0.0,
        "last_date": "n/a",
        "source": f"{CDC_FEED_SOURCE} (level only)",
    }


def fetch_disease_stats(focus: dict) -> dict:
    """
    Fetch normalised disease stats for the CDC-flagged focus illness. Influenza
    and COVID hit live disease.sh endpoints; anything else (or a live failure)
    falls back to a severity-derived synthetic stat. Never raises.
    """
    illness = (focus.get("illness") or "influenza").lower()
    level = focus.get("level", "Unknown")
    try:
        if illness == "influenza":
            return fetch_influenza_cdc()
        if illness == "covid":
            return _covid_to_stats(parse_covid_stats(fetch_who_covid(lastdays=30)))
    except Exception as e:
        log.error(json.dumps({
            "tag": "WHO", "file": "who_agent/fetcher.py",
            "action": "api_error", "disease": illness, "error": str(e),
            "note": "falling back to severity-derived synthetic stats",
        }))
    return _synthetic_stats(illness, level)


# ── Claude reasoning ─────────────────────────────────────────────────────────

def reason_with_claude(
    region: str,
    focus: dict,
    disease_stats: dict,
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
        return _fallback_reasoning(focus, disease_stats, forecast_items)

    try:
        import anthropic
    except ImportError:
        log.warning(json.dumps({
            "tag": "CLAUDE_REASONING", "file": "who_agent/fetcher.py",
            "action": "skip", "reason": "anthropic package not installed",
        }))
        return _fallback_reasoning(focus, disease_stats, forecast_items)

    # Strip raw camera text from vision before sending to Claude (already parsed)
    vision_counts = {}
    if vision_snapshot:
        vision_counts = {
            k: v for k, v in vision_snapshot.items()
            if k in ("hospital_a", "hospital_b") and isinstance(v, dict)
        }

    focus_illness = focus.get("illness", "influenza")
    focus_level = focus.get("level", "Unknown")
    required = supplies_for_illness(focus_illness)

    context = {
        "region": region,
        "cdc_focus_illness": {
            "illness": focus_illness,
            "cdc_activity_level": focus_level,
            "required_supplies": required,
            "_source": CDC_FEED_SOURCE,
        },
        "weather_live": {
            "temperature_c": forecast_items.get("weather_temperature_c"),
            "windspeed_kmh": forecast_items.get("weather_windspeed"),
            "weather_code": forecast_items.get("weather_code"),
            "source": "Open-Meteo (live)",
        },
        "cdc_illness_levels": {
            **(forecast_items.get("illness", {})),
            "_source": CDC_FEED_SOURCE,
        },
        "who_focus_surveillance": {
            "illness": disease_stats.get("illness"),
            disease_stats.get("headline_label", "metric"): disease_stats.get("headline_value"),
            disease_stats.get("secondary_label", "metric2"): disease_stats.get("secondary_value"),
            "trend": disease_stats.get("trend"),
            "trend_change_pct": disease_stats.get("trend_pct"),
            "last_date": disease_stats.get("last_date"),
            "source": disease_stats.get("source"),
        },
        "hospital_inventory": inventory_snapshot or {},
        "camera_vision_counts": vision_counts or "no capture yet",
        "active_scenario": scenario_snapshot or "none",
    }

    prompt = (
        "You are a hospital supply chain risk analyst for a regional hospital network. "
        f"The CDC surveillance feed flags '{focus_illness}' as the dominant illness for "
        f"{region} at activity level '{focus_level}', so center your assessment on it and "
        f"on the supplies it drives ({', '.join(required)}). "
        "Review ALL of the following real-time data sources:\n\n"
        f"{json.dumps(context, indent=2)}\n\n"
        "Weather code key: 0=clear, 45=fog, 61-67=rain, 71-77=snow, 95+=thunderstorm. "
        "Illness scale: Minimal < Low < Moderate < High < Very High. "
        "Inventory qty is units on hand; surplus is available to transfer.\n\n"
        "Combine ALL signals (the CDC-flagged focus illness and its WHO/CDC surveillance "
        "numbers, weather, other illness levels, camera counts, current stock, active "
        "scenario) to assess supply risk for the next 7 days. Prioritise the focus "
        "illness's required supplies.\n\n"
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
        "focus_illness": focus_illness, "focus_level": focus_level,
        "required_supplies": required,
        "weather": context["weather_live"],
        "illness": context["cdc_illness_levels"],
        "disease_data": context["who_focus_surveillance"],
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


def _fallback_reasoning(focus: dict, disease_stats: dict, forecast_items: dict) -> dict:
    """Rule-based fallback when Claude is unavailable, centred on the focus illness."""
    illness = forecast_items.get("illness", {})
    focus_illness = focus.get("illness", "influenza")
    focus_level = focus.get("level", "Unknown")
    high_illness = focus_level in ("High", "Very High")
    rising = disease_stats.get("trend") == "rising"
    risk = "high" if (high_illness and rising) else "medium" if high_illness or rising else "low"
    required = supplies_for_illness(focus_illness)
    return {
        "risk_level": risk,
        "priority_items": required[:2] if risk in ("high", "critical") else required[:1],
        "reasoning": (
            f"Rule-based fallback (Claude unavailable). CDC focus illness="
            f"{focus_illness} ({focus_level}); surveillance trend={disease_stats.get('trend')} "
            f"({disease_stats.get('headline_value')} {disease_stats.get('headline_label')})."
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
            "source": CDC_FEED_SOURCE,
            "file_path": "mock_illness_feed.json",
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
    """Read the active demo scenario from Redis (flu surge)."""
    try:
        client = get_redis()
        raw = client.get("scenario:flu_surge")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


# ── Main orchestrator ─────────────────────────────────────────────────────────

def run_who_update(region: str = "san_francisco") -> dict:
    """
    Full pipeline:  CDC focus illness → disease.sh surveillance → Claude → Redis.

    The CDC illness feed decides which illness to query disease.sh for (the worst-
    rated one), so the WHO numbers and reasoning track the actual problem. Returns
    the reasoning dict; any WHO API failure falls back gracefully and logs.
    """
    # Step 1: let the CDC feed pick the focus illness, then fetch its surveillance.
    focus = determine_focus_illness(region)
    disease_stats = fetch_disease_stats(focus)
    required = supplies_for_illness(focus["illness"])

    # Step 2: fetch weather (Open-Meteo, live, no key) + illness (CDC feed)
    weather_items = _fetch_weather_items()
    illness_items = _read_illness_items(region)

    # Step 3: merge focus-illness WHO surveillance + weather + illness into forecast:{region}
    try:
        r = get_redis()
        existing_raw = r.get(f"forecast:{region}")
        existing = json.loads(existing_raw) if existing_raw else {}
        # Drop any prior who_* fields so a focus-illness switch never leaves stale
        # surveillance numbers (e.g. old covid counts) behind in the record.
        items = {k: v for k, v in existing.get("items", {}).items()
                 if not k.startswith("who_")}
        items.update({
            "who_focus_illness": focus["illness"],
            "who_focus_level": focus["level"],
            "who_metric_label": disease_stats["headline_label"],
            "who_metric_value": disease_stats["headline_value"],
            "who_metric2_label": disease_stats["secondary_label"],
            "who_metric2_value": disease_stats["secondary_value"],
            "who_trend": disease_stats["trend"],
            "who_trend_pct": disease_stats["trend_pct"],
            "who_last_date": disease_stats["last_date"],
            "who_source": disease_stats["source"],
            "who_required_supplies": required,
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
            "focus_illness": focus["illness"], "focus_level": focus["level"],
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

    # Step 5: Claude reasoning — focus illness + WHO surveillance + weather + inventory + vision + scenario
    reasoning = reason_with_claude(
        region=region,
        focus=focus,
        disease_stats=disease_stats,
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
