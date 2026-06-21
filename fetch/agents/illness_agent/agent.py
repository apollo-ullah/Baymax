"""
Illness Agent (MVP) — FR10, forecast inputs (workstream B).

Posts current illness activity to forecast:{region} as a simple {illness -> level}
map (e.g. {"influenza": "High", "covid": "Moderate"}), read from a mocked feed.

No weather/temperature here. A separate intelligence agent reads BOTH the weather
and the illness signals from Redis and reasons over trends (e.g. deriving demand
or heat risk). Adding an illness = adding an entry to mock_illness_feed.json.
Levels use the scale: Minimal | Low | Moderate | High | Very High.
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
_FEED_PATH = os.path.join(os.path.dirname(__file__), "mock_illness_feed.json")

agent = Agent(
    name="illness_agent",
    seed=os.getenv("ILLNESS_AGENT_SEED", "illness_agent_dev_seed"),
    port=8002,
    endpoint=["http://127.0.0.1:8002/submit"],
)


def fetch_illnesses(region: str) -> dict:
    """Return {illness -> level} for the region from the mocked feed."""
    with open(_FEED_PATH) as f:
        feed = json.load(f)
    return feed.get("regions", {}).get(region, {})


@agent.on_interval(period=60.0)
async def poll_illness(ctx: Context):
    """Every 60s, read illness levels and write them to forecast:{region}."""
    try:
        illnesses = fetch_illnesses(REGION)
        ctx.logger.info(f"Illness activity ({REGION}): {illnesses}")
    except Exception as e:
        ctx.logger.error(f"Failed to read illness feed: {e}")
        return

    # Merge the illness map into forecast:{region} alongside the weather signal.
    try:
        redis_io.upsert_forecast_items(REGION, {"illness": illnesses})
        ctx.logger.info(f"Wrote illness signal to forecast:{REGION}")
    except Exception as e:
        ctx.logger.error(f"Redis write failed (forecast:{REGION}): {e}")

    # TODO: ctx.send(INTELLIGENCE_AGENT_ADDRESS, IllnessUpdate(region=REGION, illnesses=illnesses))


if __name__ == "__main__":
    agent.run()
