"""Inventory and surplus read/write helpers backed by Redis hashes."""

import json
from datetime import datetime, timezone

from redis.src.redis_client import get_redis
from redis.src.schema import EVENTS_CHANNEL, inventory_key, surplus_key


def status_from_pct(pct: float) -> str:
    """Derive a stock status label from a percentage value.

    pct < 25        -> "low"
    25 <= pct < 50  -> "warning"
    pct >= 50       -> "ok"
    """
    if pct < 25:
        return "low"
    if pct < 50:
        return "warning"
    return "ok"


def write_inventory(hospital_id, item, qty, pct, status=None):
    """Write a single inventory item as a JSON string and publish an event."""
    client = get_redis()
    if status is None:
        status = status_from_pct(pct)

    record = {
        "qty": qty,
        "pct": pct,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    key = inventory_key(hospital_id)
    client.hset(key, item, json.dumps(record))

    event = {
        "type": "inventory_update",
        "hospital_id": hospital_id,
        "item": item,
        **record,
    }
    client.publish(EVENTS_CHANNEL, json.dumps(event))

    return record


def get_inventory(hospital_id):
    """Return all inventory items for a hospital as a dict of parsed records."""
    client = get_redis()
    raw = client.hgetall(inventory_key(hospital_id))
    return {item: json.loads(value) for item, value in raw.items()}


def get_item_inventory(hospital_id, item):
    """Return a single inventory item's parsed record, or None if missing."""
    client = get_redis()
    value = client.hget(inventory_key(hospital_id), item)
    if value is None:
        return None
    return json.loads(value)


def write_surplus(hospital_id, item, spare_qty):
    """Write the spare/surplus quantity for an item to a Redis hash."""
    client = get_redis()
    client.hset(surplus_key(hospital_id), item, str(spare_qty))
    return spare_qty


def get_surplus(hospital_id):
    """Return all surplus counts for a hospital as a dict of ints."""
    client = get_redis()
    raw = client.hgetall(surplus_key(hospital_id))
    return {item: int(value) for item, value in raw.items()}
