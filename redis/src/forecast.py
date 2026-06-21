"""Forecast read/write helpers backed by Redis strings (JSON)."""

import json
from datetime import datetime, timezone

from redis_client import get_redis
from schema import EVENTS_CHANNEL, forecast_key


def write_forecast(region, forecast_data):
    """Store a region's forecast as a JSON string and publish an event."""
    client = get_redis()
    record = {
        "region": region,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": forecast_data.get("items", forecast_data),
    }

    key = forecast_key(region)
    client.set(key, json.dumps(record))

    event = {
        "type": "forecast_update",
        "region": region,
        "updated_at": record["updated_at"],
    }
    client.publish(EVENTS_CHANNEL, json.dumps(event))

    return record


def get_forecast(region):
    """Return the parsed forecast record for a region, or None if missing."""
    client = get_redis()
    value = client.get(forecast_key(region))
    if value is None:
        return None
    return json.loads(value)


def get_predicted_demand(region, item):
    """Return the forecast entry for a single item in a region, or None."""
    forecast = get_forecast(region)
    if forecast is None:
        return None
    return forecast.get("items", {}).get(item)
