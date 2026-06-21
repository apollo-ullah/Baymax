"""Sync hardware/vision pipeline output into Redis.

The vision system only reports raw item COUNTS (no percentages). This module
writes those counts into the inventory hashes and stores the latest raw vision
payload at `vision:latest`. It deliberately does NOT compute or invent `pct`
values, since the camera has no notion of capacity.
"""

import json
from datetime import datetime, timezone

from redis_client import get_redis
from schema import VISION_LATEST_KEY, inventory_key

# Map vision payload item keys -> canonical inventory item names.
ITEM_NAME_MAP = {
    "saline": "Saline",
}

# Map vision payload facility keys -> Redis hospital ids.
HOSPITAL_MAP = {
    "hospital_a": "hospital_a",
    "hospital_b": "hospital_b",
}


def status_from_count(qty: int) -> str:
    """Derive a stock status label from a raw count (no percentages).

    qty == 0  -> "warning"
    qty == 1  -> "low"
    qty >= 2  -> "ok"
    """
    if qty == 0:
        return "warning"
    if qty == 1:
        return "low"
    return "ok"


def write_latest_vision_result(vision_result):
    """Store the raw vision output as a JSON string at vision:latest."""
    client = get_redis()
    client.set(VISION_LATEST_KEY, json.dumps(vision_result))
    return vision_result


def get_latest_vision_result():
    """Return the latest parsed vision result, or None if missing."""
    client = get_redis()
    value = client.get(VISION_LATEST_KEY)
    if value is None:
        return None
    return json.loads(value)


def sync_vision_counts_to_redis(vision_result):
    """Write camera-derived saline counts into Redis inventory.

    Reads `hospital_a.saline` and `hospital_b.saline` from the vision result,
    writes them into each hospital's inventory hash (item "Saline") with a
    count-derived status, and stores the raw payload at vision:latest.

    No `pct` value is calculated; the vision system only knows counts.
    """
    client = get_redis()
    updated_at = datetime.now(timezone.utc).isoformat()

    written = {}
    for vision_hospital, hospital_id in HOSPITAL_MAP.items():
        facility = vision_result.get(vision_hospital)
        if not isinstance(facility, dict):
            continue
        for vision_item, item_name in ITEM_NAME_MAP.items():
            if vision_item not in facility:
                continue
            qty = facility[vision_item]
            record = {
                "qty": qty,
                "status": status_from_count(qty),
                "updated_at": updated_at,
                "source": "vision",
            }
            client.hset(inventory_key(hospital_id), item_name, json.dumps(record))
            written.setdefault(hospital_id, {})[item_name] = record

    write_latest_vision_result(vision_result)
    return written
