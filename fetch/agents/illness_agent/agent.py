"""
Illness Agent (MVP) — FR10, forecast inputs (workstream B).

Posts current illness activity to forecast:{region} as a simple {illness -> level}
map (e.g. {"influenza": "High", "covid": "Moderate"}), read from a mocked feed.

No weather/temperature here. A separate intelligence agent reads BOTH the weather
and the illness signals from Redis and reasons over trends (e.g. deriving demand
or heat risk). Adding an illness = adding an entry to mock_illness_feed.json.
Levels use the scale: Minimal | Low | Moderate | High | Very High.

NOTE: mock_illness_feed.json is a MOCK — no CDC API is currently integrated.
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


@agent.on_event("startup")
async def on_startup(ctx: Context):
    ctx.logger.info(json.dumps({
        "tag": "INGESTION", "file": "illness_agent/agent.py",
        "action": "startup", "region": REGION,
        "source": "mock_illness_feed.json (CDC API not integrated)",
        "feed_path": _FEED_PATH,
        "interval_s": 60,
    }))


def fetch_illnesses(region: str) -> dict:
    """Return {illness -> level} for the region from the mocked feed."""
    with open(_FEED_PATH) as f:
        feed = json.load(f)
    return feed.get("regions", {}).get(region, {})


@agent.on_interval(period=60.0)
async def poll_illness(ctx: Context):
    """Every 60s, read illness levels and write them to forecast:{region}."""
    ctx.logger.info(json.dumps({
        "tag": "INGESTION", "file": "illness_agent/agent.py",
        "action": "feed_read",
        "source": "mock_illness_feed.json",
        "note": "CDC API not integrated — static mock data",
        "region": REGION,
    }))

    try:
        illnesses = fetch_illnesses(REGION)
        ctx.logger.info(json.dumps({
            "tag": "INGESTION", "file": "illness_agent/agent.py",
            "action": "api_response",
            "source": "mock_illness_feed.json",
            "region": REGION,
            "payload": illnesses,
        }))
    except Exception as e:
        ctx.logger.error(json.dumps({
            "tag": "INGESTION", "file": "illness_agent/agent.py",
            "action": "feed_error", "error": str(e),
        }))
        return

    items = {"illness": illnesses}
    ctx.logger.info(json.dumps({
        "tag": "INGESTION", "file": "illness_agent/agent.py",
        "action": "transform",
        "transformed_items": items,
    }))

    # Merge the illness map into forecast:{region} alongside the weather signal.
    try:
        redis_io.upsert_forecast_items(REGION, items)
        ctx.logger.info(json.dumps({
            "tag": "INGESTION", "file": "illness_agent/agent.py",
            "action": "redis_write",
            "key": f"forecast:{REGION}",
            "payload": items,
        }))

        # Verification read
        written = redis_io.get_forecast(REGION)
        ctx.logger.info(json.dumps({
            "tag": "INGESTION", "file": "illness_agent/agent.py",
            "action": "redis_read_verify",
            "key": f"forecast:{REGION}",
            "payload": written,
        }))
    except Exception as e:
        ctx.logger.error(json.dumps({
            "tag": "INGESTION", "file": "illness_agent/agent.py",
            "action": "redis_error",
            "key": f"forecast:{REGION}",
            "error": str(e),
        }))

    # TODO: ctx.send(INTELLIGENCE_AGENT_ADDRESS, IllnessUpdate(region=REGION, illnesses=illnesses))


if __name__ == "__main__":
    agent.run()
