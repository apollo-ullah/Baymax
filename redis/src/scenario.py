"""Scenario profile read/write helpers backed by Redis strings (JSON)."""

import json

from redis_client import get_redis
from schema import scenario_key


def write_scenario(scenario_id, scenario_data):
    """Store a scenario profile as a JSON string via Redis SET."""
    client = get_redis()
    key = scenario_key(scenario_id)
    client.set(key, json.dumps(scenario_data))
    return scenario_data


def get_scenario(scenario_id):
    """Return the parsed scenario profile, or None if missing."""
    client = get_redis()
    value = client.get(scenario_key(scenario_id))
    if value is None:
        return None
    return json.loads(value)
