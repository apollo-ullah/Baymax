"""Crisis-brief read/write helpers backed by a Redis string (JSON).

`crisis:active` is the front-of-funnel for both demo surfaces: the user's crisis
prompt plus the inferred crisis type, ranked at-risk supplies, rationale, and a
status the dashboard renders. The dashboard seeds it (status="researching") when
the admin submits a crisis; the agent layer's start_crisis fills in the research
result (status="researched"). Mirrors forecast.py (JSON string, get_redis()).
"""

import json
from datetime import datetime, timezone

from redis_client import get_redis
from schema import CRISIS_ACTIVE_KEY


def write_crisis(payload):
    """Store the active crisis brief as a JSON string. `payload` is a dict;
    `updated_at` is stamped if absent. Returns the stored record."""
    client = get_redis()
    record = dict(payload)
    record.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    client.set(CRISIS_ACTIVE_KEY, json.dumps(record))
    return record


def update_crisis(**fields):
    """Merge `fields` into the existing crisis brief (or create one). Returns the
    merged record."""
    existing = get_crisis() or {}
    existing.update(fields)
    return write_crisis(existing)


def get_crisis():
    """Return the parsed active crisis brief, or None if unset."""
    client = get_redis()
    value = client.get(CRISIS_ACTIVE_KEY)
    return json.loads(value) if value else None
