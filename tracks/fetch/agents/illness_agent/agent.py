"""
Illness Agent (MVP) — FR10, forecast inputs (workstream B).

Serves a MOCKED CDC/WHO illness-activity feed (NOT live; the live scrape is
FR16, P2). On an interval it reads the regional illness activity and logs it.
Next it feeds the forecast: writes to forecast:{region} via the Redis track
helper (tracks/redis/src/forecast.write_forecast) and/or sends IllnessUpdate to
the intelligence agent that reasons over demand.

Together with the weather agent, this populates the demand side of shortfall
detection (FR3: falling stock AND rising forecast demand).
"""

import json
import os

from dotenv import load_dotenv
from uagents import Agent, Context

from .models import IllnessUpdate
from ...shared import redis_io

load_dotenv()  # read the project-root .env

# Which region's illness activity to serve. Override FORECAST_REGION in .env.
REGION = os.getenv("FORECAST_REGION", "san_francisco")

# Mocked feed lives next to this file.
_FEED_PATH = os.path.join(os.path.dirname(__file__), "mock_cdc_feed.json")

agent = Agent(
    name="illness_agent",
    seed=os.getenv("ILLNESS_AGENT_SEED", "illness_agent_dev_seed"),
    port=8002,
    endpoint=["http://127.0.0.1:8002/submit"],
)


def fetch_illness(region: str) -> dict:
    """Read the mocked CDC/WHO feed and return activity for one region."""
    with open(_FEED_PATH) as f:
        feed = json.load(f)
    activity = feed["regions"].get(region, {})
    return {
        "region": region,
        "influenza": activity.get("influenza", "Minimal"),
        "covid": activity.get("covid", "Minimal"),
        "rsv": activity.get("rsv", "Minimal"),
        "source": feed["source"],
    }


@agent.on_interval(period=60.0)
async def poll_illness(ctx: Context):
    """Every 60s, read the mocked illness feed and write it to forecast:{region}."""
    try:
        illness = fetch_illness(REGION)
        ctx.logger.info(f"Illness activity ({REGION}): {illness}")
    except Exception as e:
        ctx.logger.error(f"Failed to read illness feed: {e}")
        return

    # Merge our slice into forecast:{region} alongside the weather signal.
    try:
        redis_io.upsert_forecast_items(REGION, {
            "illness_influenza": illness["influenza"],
            "illness_covid": illness["covid"],
            "illness_rsv": illness["rsv"],
        })
        ctx.logger.info(f"Wrote illness signal to forecast:{REGION}")
    except Exception as e:
        ctx.logger.error(f"Redis write failed (forecast:{REGION}): {e}")

    # TODO: ctx.send(INTELLIGENCE_AGENT_ADDRESS, IllnessUpdate(**illness))


if __name__ == "__main__":
    agent.run()
