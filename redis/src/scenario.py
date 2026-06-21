"""Scenario profile read/write helpers backed by Redis strings (JSON).

NOTE: scenario matching (forecast signals → scenario → required supplies) is NOT
implemented as a pipeline stage. This module only provides R/W helpers. The
HEATSTROKE_SCENARIO seeded by seed_demo_data.py has placeholder None values for
inventory, recommendation, and forecast fields that no agent currently fills in.
"""

import json
import logging

from redis_client import get_redis
from schema import scenario_key

log = logging.getLogger("scenario")


def write_scenario(scenario_id, scenario_data):
    """Store a scenario profile as a JSON string via Redis SET."""
    client = get_redis()
    key = scenario_key(scenario_id)
    client.set(key, json.dumps(scenario_data))
    log.info(json.dumps({
        "tag": "SCENARIO_MATCH", "file": "redis/src/scenario.py",
        "action": "redis_write",
        "key": key,
        "scenario_id": scenario_id,
        "required_supplies": (scenario_data.get("disease") or {}).get("required_supplies"),
    }))
    return scenario_data


def get_scenario(scenario_id):
    """Return the parsed scenario profile, or None if missing."""
    client = get_redis()
    key = scenario_key(scenario_id)
    value = client.get(key)
    if value is None:
        log.warning(json.dumps({
            "tag": "SCENARIO_MATCH", "file": "redis/src/scenario.py",
            "action": "redis_read_miss",
            "key": key,
            "scenario_id": scenario_id,
        }))
        return None
    result = json.loads(value)
    log.info(json.dumps({
        "tag": "SCENARIO_MATCH", "file": "redis/src/scenario.py",
        "action": "redis_read",
        "key": key,
        "scenario_id": scenario_id,
        "detected": (result.get("disease") or {}).get("name"),
        "required_supplies": (result.get("disease") or {}).get("required_supplies"),
    }))
    return result
