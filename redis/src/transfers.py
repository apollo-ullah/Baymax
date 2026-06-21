"""Transfer logging and event publishing backed by a Redis Stream."""

import json
import uuid
from datetime import datetime, timezone

from redis_client import get_redis
from schema import EVENTS_CHANNEL, TRANSFERS_STREAM


def _ensure_fields(transfer):
    """Return a copy of the transfer with transfer_id and created_at filled in."""
    enriched = dict(transfer)
    if not enriched.get("transfer_id"):
        enriched["transfer_id"] = f"transfer_{uuid.uuid4().hex[:8]}"
    if not enriched.get("created_at"):
        enriched["created_at"] = datetime.now(timezone.utc).isoformat()
    return enriched


def publish_transfer_event(transfer):
    """Publish a transfer event to the events channel."""
    client = get_redis()
    event = {"type": "transfer", **transfer}
    client.publish(EVENTS_CHANNEL, json.dumps(event))
    return event


def log_transfer(transfer):
    """Append a transfer to the stream, publish an event, and print a summary."""
    client = get_redis()
    enriched = _ensure_fields(transfer)

    client.xadd(TRANSFERS_STREAM, {"data": json.dumps(enriched)})
    publish_transfer_event(enriched)

    print(
        f"[TRANSFER] {enriched['transfer_id']}: "
        f"{enriched.get('quantity')} x {enriched.get('item')} "
        f"{enriched.get('from_hospital')} -> {enriched.get('to_hospital')} "
        f"(eta {enriched.get('eta_minutes')}m, status {enriched.get('status')}, "
        f"settlement {enriched.get('settlement_status')})"
    )

    return enriched


def get_recent_transfers(limit=10):
    """Return the most recent transfers (newest first) as parsed dicts."""
    client = get_redis()
    entries = client.xrevrange(TRANSFERS_STREAM, count=limit)
    transfers = []
    for _entry_id, fields in entries:
        data = fields.get("data")
        if data is not None:
            transfers.append(json.loads(data))
    return transfers
