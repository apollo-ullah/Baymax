"""
Shortage detection for the approval flow.

Finds a hospital short on an item and another that can spare it, reading the
Redis track's inventory + surplus. Falls back to the canonical seeded scenario
(hospital_a short on "IV Fluids", hospital_b has surplus) when Redis has no data,
so the flow always has something to demo.
"""

import logging
from dataclasses import dataclass

from ..shared import redis_io

log = logging.getLogger("stockpile.shortage")

# Display names — matches redis/src/seed_demo_data.py meta.
_HOSPITAL_NAMES = {
    "hospital_a": "SF General",
    "hospital_b": "UCSF Mission Bay",
    "hospital_c": "Kaiser SF",
}
_HOSPITALS = ["hospital_a", "hospital_b"]  # A & B are the two demo facilities
_SHORT_STATUSES = {"low", "critical", "warning"}
_MAX_REQUEST = 110  # cap a single request to a sensible chunk

# Canonical fallback (no/empty Redis) — matches seed_demo_data.
_MOCK = {"requester": "hospital_a", "provider": "hospital_b",
         "item": "IV Fluids", "quantity": 110}


@dataclass
class Shortage:
    requester_id: str
    requester_name: str
    provider_id: str
    provider_name: str
    item: str
    quantity: int


def _name(hid: str) -> str:
    return _HOSPITAL_NAMES.get(hid, hid)


def make_shortage(requester_id: str, provider_id: str, item: str,
                  quantity: int = 110) -> Shortage:
    """Build a Shortage explicitly — lets the caller force the direction
    (e.g. A requests from B, or B requests from A) instead of auto-detecting."""
    return Shortage(requester_id, _name(requester_id),
                    provider_id, _name(provider_id), item, quantity)


def _detect_from_redis(item):
    """Find (requester short on item) + (provider with surplus) in Redis, or None."""
    short = None  # (hospital_id, item)
    for hid in _HOSPITALS:
        inv = redis_io.get_inventory(hid)  # {item: {qty, pct, status}}
        for it, rec in (inv or {}).items():
            if item and it != item:
                continue
            if rec.get("status") in _SHORT_STATUSES:
                short = (hid, it)
                break
        if short:
            break
    if not short:
        return None

    req_id, it = short
    for hid in _HOSPITALS:
        if hid == req_id:
            continue
        spare = (redis_io.get_surplus(hid) or {}).get(it, 0)
        if spare > 0:
            return Shortage(req_id, _name(req_id), hid, _name(hid),
                            it, min(spare, _MAX_REQUEST))
    return None


def detect_shortage(item=None) -> Shortage:
    """Return a Shortage (a short requester + a provider with surplus). Tries
    Redis first, falls back to the canonical mock scenario."""
    try:
        found = _detect_from_redis(item)
        if found:
            log.info("[shortage] redis: %s short on %s; %s can spare %s",
                     found.requester_id, found.item,
                     found.provider_id, found.quantity)
            return found
    except Exception as exc:  # noqa: BLE001
        log.warning("[shortage] redis detection failed (%s) — using mock", exc)

    m = _MOCK
    return Shortage(m["requester"], _name(m["requester"]),
                    m["provider"], _name(m["provider"]),
                    item or m["item"], m["quantity"])
