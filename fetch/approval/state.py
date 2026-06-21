"""
Approval request state, stored in Redis at approval:{id} as JSON.

Each transition publishes an `approval_update` event on the events channel so the
dashboard can show the human-approval flow alongside the agent negotiation.
"""

import json
import uuid
from datetime import datetime, timezone

from ..shared import redis_io


def _key(request_id: str) -> str:
    return f"approval:{request_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(shortage) -> dict:
    """Create a new approval request from a Shortage and persist it."""
    rid = f"appr_{uuid.uuid4().hex[:8]}"
    rec = {
        "request_id": rid,
        "status": "CREATED",
        "requester_id": shortage.requester_id,
        "requester_name": shortage.requester_name,
        "provider_id": shortage.provider_id,
        "provider_name": shortage.provider_name,
        "item": shortage.item,
        "quantity": shortage.quantity,
        "created_at": _now(),
        "updated_at": _now(),
        "history": [{"status": "CREATED", "at": _now()}],
    }
    _save(rec, publish=False)
    return rec


def get(request_id: str):
    """Return the request record, or None."""
    raw = redis_io.get_redis().get(_key(request_id))
    return json.loads(raw) if raw else None


def update(request_id: str, status: str, note: str = "") -> dict:
    """Advance a request to `status`, persist, and publish a dashboard event."""
    rec = get(request_id)
    if rec is None:
        raise KeyError(request_id)
    rec["status"] = status
    rec["updated_at"] = _now()
    rec.setdefault("history", []).append({"status": status, "note": note, "at": _now()})
    _save(rec, publish=True)
    return rec


def _save(rec: dict, publish: bool):
    client = redis_io.get_redis()
    client.set(_key(rec["request_id"]), json.dumps(rec))
    if publish:
        client.publish(redis_io.EVENTS_CHANNEL, json.dumps({
            "type": "approval_update",
            **{k: rec[k] for k in ("request_id", "status", "item", "quantity",
                                   "requester_name", "provider_name")},
        }))
