"""
sync_to_redis.py — push camera-detected supply counts into the Baymax Redis.

The vision pipeline (bottle_counter.py) counts saline on each side of the green
straw -> Hospital A (left) and Hospital B (right). This bridge writes those
counts into the SAME Redis the negotiation reads, so the physical shelf becomes
the live inventory signal:

    shelf  ->  MacBook camera  ->  Claude Vision  ->  Redis  ->  agent negotiation
                                                       ^^^^^ this file

It reuses bottle_counter for vision and the Redis track's own write helpers for
storage (no duplicated schema). The negotiation side (redis_inventory.py) reads
`spare_capacity` straight from the surplus we write here.

Usage (run from this directory):
    python sync_to_redis.py                          # capture + Claude Vision + write Redis
    python sync_to_redis.py --image capture_X.jpg    # vision-count a saved frame
    python sync_to_redis.py --from-json capture_X.json   # reuse a prior result (no API)
    python sync_to_redis.py --counts a=0,b=6         # manual counts (no camera/API)

Tuning:
    --item Saline        Redis item name to write (must match the seeded naming)
    --capacity 10        shelf-full reference for the pct/status field
    --reserve 2          units each side keeps before any is offered as surplus
    --region san_francisco   (reserved; hospitals already carry their region in meta)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# The Redis track owns the schema + write helpers; bridge to its src/ exactly
# like fetch/shared/redis_io.py does (single source of truth for keys).
# hardware/camera connection/sync_to_redis.py -> parents[2] == repo root.
_REDIS_SRC = Path(__file__).resolve().parents[2] / "redis" / "src"
if str(_REDIS_SRC) not in sys.path:
    sys.path.insert(0, str(_REDIS_SRC))

from inventory import status_from_pct, write_inventory, write_surplus  # noqa: E402
from vision_sync import write_latest_vision_result  # noqa: E402

# Vision side: counts saline left/right of the green straw -> A / B. Lives next
# to this file, so a plain import works when run from this directory.
HOSPITAL_IDS = {"a": "hospital_a", "b": "hospital_b"}


def _pct(qty: int, capacity: int) -> float:
    if capacity <= 0:
        return 0.0
    return round(min(100.0, max(0.0, qty / capacity * 100.0)), 1)


def counts_from_vision(image_path: str | None) -> dict:
    """Run the Claude Vision pipeline (camera or a saved image) and return
    {'a': int, 'b': int}. Imported lazily so the manual/json paths need no
    anthropic/opencv install or API key."""
    import cv2
    import bottle_counter as bc

    if image_path:
        jpeg_bytes, frame = bc.load_image(image_path)
    else:
        jpeg_bytes, frame = bc.capture_frame()
        # Save the captured frame so the UI can display it
        out_path = str(Path(__file__).parent / f"capture_{int(time.time())}.jpg")
        cv2.imwrite(out_path, frame)
        print(f"Frame saved: {out_path}")

    raw = bc.count_bottles(jpeg_bytes)
    parsed = bc.parse_result(raw)
    return {"a": parsed.get("hospital_a"), "b": parsed.get("hospital_b"),
            "notes": parsed.get("notes"), "raw": raw}


def counts_from_json(json_path: str) -> dict:
    """Reuse a prior `capture_*.json` vision result (no API call)."""
    with open(json_path) as f:
        data = json.load(f)
    # bottle_counter writes {hospital_a: {<item>: n}, ...} with a single item key.
    def _first_val(section):
        section = data.get(section) or {}
        return next((v for v in section.values() if isinstance(v, int)), None)
    return {"a": _first_val("hospital_a"), "b": _first_val("hospital_b"),
            "notes": data.get("notes")}


def counts_from_manual(spec: str) -> dict:
    """Parse '--counts a=0,b=6' (keys a/b) into {'a': int, 'b': int}."""
    out: dict = {"a": None, "b": None, "notes": "manual counts"}
    for pair in spec.split(","):
        if "=" not in pair:
            continue
        key, _, val = pair.partition("=")
        key = key.strip().lower().removeprefix("hospital_")
        if key in ("a", "b"):
            out[key] = int(val.strip())
    return out


def sync(counts: dict, *, item: str, capacity: int, reserve: int) -> None:
    """Write per-hospital qty/pct/status + surplus into Redis.

    Always writes a value for every hospital — uses 0 if count was not
    detected so stale values from a previous capture never persist.
    """
    for side, hid in HOSPITAL_IDS.items():
        qty = counts.get(side)
        if qty is None:
            qty = 0
            print(f"  {hid}: no count detected for side '{side}' — writing 0.")
        pct = _pct(qty, capacity)
        status = status_from_pct(pct)
        surplus = max(0, qty - reserve)
        write_inventory(hid, item, qty, pct, status)
        write_surplus(hid, item, surplus)
        print(f"  {hid}: {item} qty={qty} pct={pct} status={status} "
              f"surplus={surplus}  (reserve={reserve}, capacity={capacity})")


def main() -> None:
    p = argparse.ArgumentParser(description="Push camera supply counts into Baymax Redis.")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--image", help="Vision-count a saved image instead of the camera.")
    src.add_argument("--from-json", dest="from_json",
                     help="Reuse a prior capture_*.json result (no API call).")
    src.add_argument("--counts", help="Manual counts, e.g. 'a=0,b=6' (no camera/API).")
    p.add_argument("--item", default="Saline", help="Redis item name (default: Saline).")
    p.add_argument("--capacity", type=int, default=10, help="Shelf-full reference for pct (default 10).")
    p.add_argument("--reserve", type=int, default=2, help="Units kept before offering surplus (default 2).")
    p.add_argument("--region", default="san_francisco", help="(reserved) region label.")
    args = p.parse_args()

    if args.counts:
        counts = counts_from_manual(args.counts)
        source = f"manual ({args.counts})"
    elif args.from_json:
        counts = counts_from_json(args.from_json)
        source = f"json ({args.from_json})"
    elif args.image:
        counts = counts_from_vision(args.image)
        source = f"vision ({args.image})"
    else:
        counts = counts_from_vision(None)
        source = "vision (camera capture)"

    print(f"Source: {source}")
    if counts.get("notes"):
        print(f"Notes:  {counts['notes']}")
    if counts.get("raw"):
        print(f"Claude Vision raw response:\n{counts['raw']}")
    print(f"Writing {args.item} to Redis (REDIS_URL={os.getenv('REDIS_URL', 'redis://localhost:6379')}):")
    sync(counts, item=args.item, capacity=args.capacity, reserve=args.reserve)

    # Write vision:latest so the UI Vision card shows updated counts + raw response.
    # Always write counts (0 if undetected) so stale values never persist.
    item_key = args.item.lower().replace(" ", "_")
    vision_payload = {
        "hospital_a": {item_key: counts["a"] if counts.get("a") is not None else 0},
        "hospital_b": {item_key: counts["b"] if counts.get("b") is not None else 0},
        "notes": counts.get("notes"),
        "claude_raw": counts.get("raw"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
    try:
        write_latest_vision_result(vision_payload)
        print("vision:latest updated.")
    except Exception as e:
        print(f"Warning: could not write vision:latest — {e}")

    print("Done — the negotiation will read these via BAYMAX_REDIS=1.")


if __name__ == "__main__":
    main()
