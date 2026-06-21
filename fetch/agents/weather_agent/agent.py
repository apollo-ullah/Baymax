"""
Weather Agent (MVP) — FR10, forecast inputs (workstream B).

Pulls current weather from Open-Meteo (no API key needed) on an interval.
For now it just fetches and logs. Next it feeds the forecast: it writes demand
signals to forecast:{region} via the Redis bridge (fetch/shared/redis_io.py)
and/or sends WeatherUpdate to the intelligence agent that ranks offers.
"""

import os

import httpx
from dotenv import load_dotenv
from uagents import Agent, Context

from .models import WeatherUpdate
from ...shared import redis_io

load_dotenv()  # read the project-root .env

# Location — defaults to Berkeley, CA. Override WEATHER_LATITUDE/LONGITUDE in .env.
LATITUDE = float(os.getenv("WEATHER_LATITUDE", "37.8716"))
LONGITUDE = float(os.getenv("WEATHER_LONGITUDE", "-122.2727"))
# Region this signal belongs to — must match the Redis track's seeded region.
REGION = os.getenv("FORECAST_REGION", "san_francisco")

# Seed derives the agent's identity/address — keep it secret, set it in .env.
agent = Agent(
    name="weather_agent",
    seed=os.getenv("WEATHER_AGENT_SEED", "weather_agent_dev_seed"),
    port=8001,
    endpoint=["http://127.0.0.1:8001/submit"],
)


async def fetch_weather() -> dict:
    """Fetch current weather from Open-Meteo."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}&current_weather=true"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json().get("current_weather", {})


@agent.on_interval(period=30.0)
async def poll_weather(ctx: Context):
    """Every 30s, pull weather and write the demand signal to forecast:{region}."""
    try:
        weather = await fetch_weather()
        ctx.logger.info(f"Current weather: {weather}")
    except Exception as e:
        ctx.logger.error(f"Failed to fetch weather: {e}")
        return

    # Write our slice of the forecast to Redis (resilient: a Redis outage must
    # not kill the agent — PRD §12, every dependency has a fallback).
    try:
        redis_io.upsert_forecast_items(REGION, {
            "weather_temperature_c": weather.get("temperature"),
            "weather_windspeed": weather.get("windspeed"),
            "weather_code": weather.get("weathercode"),
        })
        ctx.logger.info(f"Wrote weather signal to forecast:{REGION}")
    except Exception as e:
        ctx.logger.error(f"Redis write failed (forecast:{REGION}): {e}")

    # TODO: ctx.send(INTELLIGENCE_AGENT_ADDRESS, WeatherUpdate(**weather))


if __name__ == "__main__":
    agent.run()
