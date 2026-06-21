"""
WHO Agent — polls disease.sh (WHO/JHU CSSE) for COVID-19 historical data
every hour, passes results + current illness/weather/inventory to Claude
for supply-risk reasoning, and writes both to Redis.

Port 8004 (distinct from weather=8001, illness=8002 in this family).

Run standalone:
    python -m fetch.agents.who_agent.agent

Or import and call directly (no uAgent needed):
    from fetch.agents.who_agent.fetcher import run_who_update
    run_who_update()
"""

import json
import os

from dotenv import load_dotenv
from uagents import Agent, Context

from .fetcher import run_who_update

load_dotenv()

REGION = os.getenv("FORECAST_REGION", "san_francisco")

agent = Agent(
    name="who_agent",
    seed=os.getenv("WHO_AGENT_SEED", "who_agent_dev_seed"),
    port=8004,
    endpoint=["http://127.0.0.1:8004/submit"],
)


@agent.on_event("startup")
async def on_startup(ctx: Context):
    ctx.logger.info(json.dumps({
        "tag": "WHO", "file": "who_agent/agent.py",
        "action": "startup",
        "region": REGION,
        "source": "disease.sh (WHO/JHU CSSE)",
        "interval_s": 3600,
        "reasoning_model": "claude-haiku-4-5-20251001",
    }))
    # Run immediately on startup so data is available right away
    try:
        result = run_who_update(region=REGION)
        ctx.logger.info(json.dumps({
            "tag": "WHO", "file": "who_agent/agent.py",
            "action": "startup_complete",
            "risk_level": result.get("risk_level"),
            "priority_items": result.get("priority_items"),
        }))
    except Exception as e:
        ctx.logger.error(json.dumps({
            "tag": "WHO", "file": "who_agent/agent.py",
            "action": "startup_error", "error": str(e),
        }))


@agent.on_interval(period=3600.0)
async def poll_who(ctx: Context):
    """Every hour, refresh WHO data and Claude reasoning."""
    ctx.logger.info(json.dumps({
        "tag": "WHO", "file": "who_agent/agent.py",
        "action": "interval_poll", "region": REGION,
    }))
    try:
        result = run_who_update(region=REGION)
        ctx.logger.info(json.dumps({
            "tag": "WHO", "file": "who_agent/agent.py",
            "action": "poll_complete",
            "risk_level": result.get("risk_level"),
            "priority_items": result.get("priority_items"),
        }))
    except Exception as e:
        ctx.logger.error(json.dumps({
            "tag": "WHO", "file": "who_agent/agent.py",
            "action": "poll_error", "error": str(e),
        }))


if __name__ == "__main__":
    agent.run()
