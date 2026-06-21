"""dashboard_bus.py — tiny Redis bus between the scan dashboard and FRONT agent.

  * TRIGGER  (list  baymax:trigger)    dashboard RPUSHes a negotiation request;
                                        the FRONT agent LPOPs it in an on_interval.
  * NARRATION(pubsub baymax:narration)  the FRONT agent publishes each milestone;
                                        the dashboard SSE subscribes.

Keeps the negotiation core free of any Redis/web import: run_front wires
publish_narration() as a narration sink; the dashboard reads the same keys. Uses
the same REDIS_URL (default redis://localhost:6379) as redis_inventory.py.
"""
from __future__ import annotations
import json
import os

TRIGGER_LIST = "baymax:trigger"
DECISION_LIST = "baymax:decision"
NARRATION_CHANNEL = "baymax:narration"
DEFAULT_REDIS_URL = "redis://localhost:6379"
_SOCKET_TIMEOUT_S = float(os.getenv("BAYMAX_REDIS_TIMEOUT", "2.0"))

_client = None


def _redis():
    global _client
    if _client is None:
        import redis
        url = os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
        _client = redis.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=_SOCKET_TIMEOUT_S, socket_timeout=_SOCKET_TIMEOUT_S,
        )
    return _client


def push_trigger(item, requester=None, quantity=None, prompt=None, stock=None):
    """RPUSH a negotiation request onto the trigger list. Returns the payload.

    ``prompt`` is the natural-language intent and ``stock`` carries the current
    item stock for Hospital A and B — both surfaced to the FRONT agent.
    """
    payload = {"item": item, "requester": requester, "quantity": quantity,
               "prompt": prompt, "stock": stock}
    _redis().rpush(TRIGGER_LIST, json.dumps(payload))
    return payload


def pop_trigger():
    """LPOP one queued trigger, or None. Never raises on a malformed entry."""
    raw = _redis().lpop(TRIGGER_LIST)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def push_decision(req_id, decision):
    """RPUSH an admin decision ({approve|order|reject}) for a halted negotiation."""
    payload = {"req_id": req_id, "decision": decision}
    _redis().rpush(DECISION_LIST, json.dumps(payload))
    return payload


def pop_decision():
    """LPOP one queued decision, or None. Never raises on a malformed entry."""
    raw = _redis().lpop(DECISION_LIST)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


NARRATION_LIST = "baymax:narration:log"   # persistent list — survives pubsub gaps

def publish_narration(payload: dict) -> None:
    """Publish one milestone. Writes to BOTH pubsub (real-time) and a Redis list
    (reliable catch-up). Swallows all errors — narration must never break the core."""
    try:
        r = _redis()
        data = json.dumps(payload)
        r.publish(NARRATION_CHANNEL, data)   # real-time path
        r.rpush(NARRATION_LIST, data)        # persistent path
        r.ltrim(NARRATION_LIST, -200, -1)    # keep last 200 events
    except Exception:
        pass


async def narration_events():
    """Async generator of narration dicts for the dashboard SSE (one subscription
    per connection — fine for a demo's one or two browser tabs)."""
    import redis.asyncio as aredis
    url = os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
    client = aredis.from_url(url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(NARRATION_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                yield json.loads(message["data"])
            except (ValueError, TypeError):
                continue
    finally:
        await pubsub.unsubscribe(NARRATION_CHANNEL)
        await pubsub.aclose()
        await client.aclose()
