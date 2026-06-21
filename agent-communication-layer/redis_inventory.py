"""redis_inventory.py — the REAL implementation of the inventory seam (Workstream C).

`interfaces.get_inventory()` shipped a deterministic mock so the agent network was
never blocked on Redis. This module is the live backend that the mock was a
placeholder for: it reads the teammates' Redis (owned by `tracks/redis`) and
returns the exact same `InventoryState` shape, so the negotiation core is
unchanged. `interfaces.get_inventory()` delegates here when BAYMAX_REDIS=1 and
falls back to the mock on ANY failure (contract FR1: the seam must never hang).

Bridge pattern mirrors `tracks/fetch/shared/redis_io.py`: the Redis track owns the
key schema, so we add its `src/` to sys.path and reuse `schema.*` for key names
rather than redefining them. We build our OWN timeout-bounded client (their
get_redis() sets no timeouts) so a slow/unreachable RPC can't freeze a handler.

Redis schema (see tracks/redis/redis_contract.md):
    hospital:{id}:inventory  hash  item -> JSON {qty, pct, status, updated_at}
    hospital:{id}:surplus    hash  item -> spare qty (string)
    hospital:{id}:meta       hash  {name, region, lat, lng, capacity}

Mapping onto InventoryState (which conflates give/short into one safety_threshold):
    qty              <- inventory record qty
    capacity         <- round(qty / (pct/100))  (per-item), else meta capacity
    lat/lng          <- meta
    safety_threshold <- qty - surplus[item]   so spare_capacity == Redis surplus
                        (offerers B/C give exactly their surplus); when no surplus
                        field exists, round(capacity * 0.5) as a sane default.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# The Redis track owns the schema + key helpers. Add its src/ to the path and
# reuse schema.* (single source of truth for key names — never redefine them).
# agent-communication-layer/redis_inventory.py -> parents[1] == repo root.
_REDIS_SRC = Path(__file__).resolve().parents[1] / "redis" / "src"
if str(_REDIS_SRC) not in sys.path:
    sys.path.insert(0, str(_REDIS_SRC))

import schema  # noqa: E402  (redis/src/schema.py — key-name helpers only)

# InventoryState lives in interfaces; importing it at module top is safe because
# interfaces only imports THIS module lazily (inside get_inventory), so interfaces
# is already fully initialised by the time we are first imported.
from interfaces import InventoryState  # noqa: E402

DEFAULT_REDIS_URL = "redis://localhost:6379"
_SOCKET_TIMEOUT_S = float(os.getenv("BAYMAX_REDIS_TIMEOUT", "2.0"))

# Baymax uses display names ("Hospital A"); the Redis track keys by id
# ("hospital_a"). Map by the trailing letter so "Hospital B" -> "hospital_b".
_HOSPITAL_IDS = {
    "Hospital A": "hospital_a",
    "Hospital B": "hospital_b",
    "Hospital C": "hospital_c",
}

_client_cache = None


def redis_enabled() -> bool:
    """True when the live Redis backend should be used (set by run_front.py /
    the live runners). Default off so the offline harnesses need no Redis."""
    return os.getenv("BAYMAX_REDIS", "").lower() in ("1", "true", "yes")


def _client():
    """Cached, timeout-bounded Redis client. Raises on construction failure;
    callers treat any exception as 'Redis unavailable -> fall back to mock'."""
    global _client_cache
    if _client_cache is None:
        import redis  # local import: missing lib => fall back to mock

        # `or` (not getenv default) so an empty REDIS_URL="" — as shipped in
        # .env — falls back to the local default instead of failing from_url()
        # with "must specify one of the following schemes" -> silent mock.
        url = os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
        _client_cache = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=_SOCKET_TIMEOUT_S,
            socket_timeout=_SOCKET_TIMEOUT_S,
        )
    return _client_cache


def _hid(hospital: str) -> str:
    return _HOSPITAL_IDS.get(hospital, hospital)


def _canon_field(item: str, fields) -> Optional[str]:
    """Match `item` against the actual hash field names case-insensitively, so
    Baymax's "IV fluids" / "saline" resolve to Redis "IV Fluids" / "Saline"."""
    target = item.strip().lower()
    for f in fields:
        if f.strip().lower() == target:
            return f
    return None


def _meta(client, hid: str) -> dict:
    return client.hgetall(schema.meta_key(hid))


def redis_facility_meta(hospital: str) -> Optional[dict]:
    """(lat, lng, capacity) for a facility from Redis meta, or None if unknown.
    Used so distance_between() can use the same coordinates as inventory."""
    client = _client()
    meta = _meta(client, _hid(hospital))
    if not meta:
        return None
    return {
        "lat": float(meta.get("lat", 0.0) or 0.0),
        "lng": float(meta.get("lng", 0.0) or 0.0),
        "capacity": int(float(meta.get("capacity", 0) or 0)),
    }


def redis_get_inventory(hospital: str, item: str) -> InventoryState:
    """Live inventory of `item` at `hospital` from Redis.

    Raises on a connection/decode failure OR when the hospital is unknown to
    Redis (no meta and no inventory) — the caller (interfaces.get_inventory)
    catches and falls back to the mock. A reachable Redis with the item simply
    absent returns a not-present InventoryState (a real "no stock" answer).
    """
    client = _client()
    hid = _hid(hospital)

    inv_raw = client.hgetall(schema.inventory_key(hid))     # may raise (conn)
    meta = _meta(client, hid)
    if not inv_raw and not meta:
        raise LookupError(f"{hospital!r} ({hid}) not present in Redis")

    lat = float(meta.get("lat", 0.0) or 0.0)
    lng = float(meta.get("lng", 0.0) or 0.0)
    meta_capacity = int(float(meta.get("capacity", 0) or 0))

    field = _canon_field(item, inv_raw.keys())
    if field is None:
        # Reachable, but this facility does not stock the item: real "no stock".
        return InventoryState(
            hospital=hospital, item=item, qty=0,
            capacity=meta_capacity, safety_threshold=0,
            lat=lat, lng=lng, present=False,
        )

    record = json.loads(inv_raw[field])
    qty = int(record.get("qty", 0))
    pct = float(record.get("pct", 0) or 0)
    capacity = round(qty / (pct / 100.0)) if pct > 0 else meta_capacity

    surplus_raw = client.hgetall(schema.surplus_key(hid))
    surplus_field = _canon_field(item, surplus_raw.keys())
    surplus = int(float(surplus_raw[surplus_field])) if surplus_field is not None else 0
    # Camera workers store a fixed `reserve` (minimum safe stock). When present,
    # use it as safety_threshold so a low shelf count (qty < reserve) still reads
    # as a shortfall. Without it, fall back to qty-surplus (legacy) or 50% capacity.
    reserve = int(record.get("reserve", 0) or 0)
    if reserve > 0:
        safety_threshold = reserve
    elif surplus_field is not None:
        safety_threshold = max(0, qty - surplus)
    else:
        # No declared surplus: keep a sane reserve so spare_capacity is 0-ish and
        # a low requester still reads as short.
        safety_threshold = round(capacity * 0.5)

    return InventoryState(
        hospital=hospital, item=item, qty=qty, capacity=capacity,
        safety_threshold=safety_threshold, lat=lat, lng=lng,
        updated_at=record.get("updated_at", InventoryState.updated_at),
        present=True,
    )


# ---------------------------------------------------------------------------
# Crisis brief (crisis:active) — fail-soft writer for the agent layer.
# start_crisis calls this to publish the inferred crisis brief for the dashboard.
# It NO-OPS (returns False) when Redis is disabled or unreachable, so the crisis
# flow runs identically offline (the negotiation never depends on this write).
# ---------------------------------------------------------------------------

def write_crisis_active(payload: dict) -> bool:
    """Best-effort write of the active crisis brief to `crisis:active` (JSON
    string). Returns True on success, False when Redis is disabled/unavailable."""
    if not redis_enabled():
        return False
    try:
        client = _client()
        client.set(schema.CRISIS_ACTIVE_KEY, json.dumps(payload))
        return True
    except Exception:  # noqa: BLE001 — fail-soft; crisis flow must not depend on this
        return False


def get_crisis_active() -> Optional[dict]:
    """Read the active crisis brief, or None when disabled/unavailable/unset."""
    if not redis_enabled():
        return None
    try:
        value = _client().get(schema.CRISIS_ACTIVE_KEY)
        return json.loads(value) if value else None
    except Exception:  # noqa: BLE001
        return None


def write_transfer_record(record: dict) -> bool:
    """Fail-soft XADD of a confirmed transfer onto the `transfers` stream so the
    dashboard's transfer panel reflects REAL settlements (previously demo-only).
    Matches redis/src/transfers.py's `{"data": json}` entry shape. No-op
    (returns False) when Redis is disabled/unavailable."""
    if not redis_enabled():
        return False
    try:
        _client().xadd(schema.TRANSFERS_STREAM, {"data": json.dumps(record)})
        return True
    except Exception:  # noqa: BLE001 — never let an audit write break settlement
        return False
