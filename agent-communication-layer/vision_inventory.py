"""vision_inventory.py — trigger a shelf scan and re-read inventory for one item.

Used by start_crisis() so the negotiation quantity reflects what Claude Vision
actually sees on the webcam (water-bottle demo props), not stale seed data.

Fail-soft: on any error, callers fall back to get_inventory() as-is.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from interfaces import InventoryState, get_inventory, vision_item_key

_HOSPITAL_IDS = {
    "Hospital A": "hospital_a",
    "Hospital B": "hospital_b",
    "Hospital C": "hospital_c",
}
_CAPTURE_CHANNEL = "vision:capture_request"
_HARDWARE_DIR = Path(__file__).resolve().parents[1] / "hardware" / "camera connection"
_CAPTURE_SCRIPT = _HARDWARE_DIR / "capture_single.py"


def vision_on_crisis_enabled() -> bool:
    """When true, start_crisis() requests a fresh vision count before negotiating."""
    if os.getenv("BAYMAX_VISION_ON_CRISIS", "").strip().lower() in ("1", "true", "yes"):
        return True
    # Bottle-desk demo implies live vision should drive quantities.
    from interfaces import demo_mode
    return demo_mode()


def _redis_client():
    import redis
    url = os.getenv("REDIS_URL") or "redis://localhost:6379"
    return redis.Redis.from_url(
        url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2,
    )


def _publish_capture(hospital_id: str, item: str) -> int:
    """Notify remote camera workers; return subscriber count."""
    r = _redis_client()
    payload = json.dumps({"target": hospital_id, "item": item})
    return int(r.publish(_CAPTURE_CHANNEL, payload))


def _local_capture(hospital_id: str, item: str, manual_count: int | None = None) -> bool:
    """Run capture_single.py on this machine if present. Returns True if invoked."""
    if not _CAPTURE_SCRIPT.is_file():
        return False
    env = {
        **os.environ,
        "HOSPITAL_ID": hospital_id,
        "ITEM": item,
        "CAPACITY": os.getenv("BAYMAX_DEMO_CAPACITY", "4"),
        "RESERVE": os.getenv(
            "BAYMAX_DEMO_TARGET" if hospital_id == "hospital_a" else "BAYMAX_DEMO_RESERVE",
            "3" if hospital_id == "hospital_a" else "1",
        ),
        "BAYMAX_VISION_DEMO": os.getenv("BAYMAX_VISION_DEMO", "1"),
    }
    cmd = [sys.executable, str(_CAPTURE_SCRIPT)]
    if manual_count is not None:
        cmd += ["--count", str(manual_count)]
    try:
        result = subprocess.run(
            cmd, cwd=str(_HARDWARE_DIR), env=env,
            capture_output=True, text=True, timeout=90,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def refresh_vision_inventory(hospital: str, item: str) -> InventoryState:
    """Request a vision scan for `item` at `hospital`, wait briefly, re-read Redis/mock.

    Order: publish remote trigger → optional local capture_single → sleep → get_inventory.
    Never raises — returns current inventory on failure."""
    hid = _HOSPITAL_IDS.get(hospital, hospital)
    try:
        subs = _publish_capture(hid, item)
        # Local one-shot only when no remote worker subscribed (typical single-laptop demo).
        if subs == 0:
            _local_capture(hid, item)
        else:
            time.sleep(float(os.getenv("BAYMAX_VISION_WAIT_S", "2.5")))
        if subs > 0:
            time.sleep(float(os.getenv("BAYMAX_VISION_WAIT_S", "2.5")))
    except Exception:  # noqa: BLE001
        pass
    return get_inventory(hospital, item)


def read_vision_count(hospital_id: str, item: str) -> int | None:
    """Read the latest vision count for one item from vision:latest, if present."""
    key = vision_item_key(item)
    try:
        raw = _redis_client().get("vision:latest")
        if not raw:
            return None
        data = json.loads(raw)
        section = data.get(hospital_id) or {}
        if isinstance(section, dict):
            return section.get(key)
    except Exception:  # noqa: BLE001
        return None
    return None
