"""
capture_single.py — single-hospital camera capture for two-laptop demo setup.

Each MacBook runs this script independently. No green straw divider needed —
the entire camera frame is one hospital's shelf.

Usage (run from this directory):
    HOSPITAL_ID=hospital_a REDIS_URL=redis://10.59.174.131:6379 python capture_single.py
    HOSPITAL_ID=hospital_b REDIS_URL=redis://10.59.174.131:6379 python capture_single.py

    # Manual count (no camera/API):
    HOSPITAL_ID=hospital_a python capture_single.py --count 7

    # Use a saved image:
    HOSPITAL_ID=hospital_a python capture_single.py --image capture_X.jpg

Env vars:
    HOSPITAL_ID      hospital_a or hospital_b (required)
    REDIS_URL        Redis endpoint (default redis://localhost:6379)
    ANTHROPIC_API_KEY  for Claude Vision (required unless --count used)
    ITEM             inventory item name (default Saline)
    CAPACITY         shelf-full reference for pct (default 10)
    RESERVE          units kept before offering as surplus (default 2)
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Bridge to Redis track helpers
_REDIS_SRC = Path(__file__).resolve().parents[2] / "redis" / "src"
if str(_REDIS_SRC) not in sys.path:
    sys.path.insert(0, str(_REDIS_SRC))

from inventory import status_from_pct, write_inventory, write_surplus  # noqa: E402
from vision_sync import write_latest_vision_result  # noqa: E402

VALID_HOSPITALS = {"hospital_a", "hospital_b"}

SINGLE_HOSPITAL_PROMPT = """You are a hospital supply inventory scanner.

Count the saline units visible in this image. Count ALL of the following as one saline unit:
- Saline bags or IV fluid bags
- Water bottles or liquid containers
- Yellow or red Red Bull cans (used as saline proxies in this demo)
- Any similar cylindrical or pouch-shaped liquid supply item

Respond in exactly this format — no extra text:

COUNT: <number>
NOTES: <one sentence describing what you see, or "None">"""


def capture_and_encode() -> tuple[bytes, object]:
    """Capture from built-in camera, return (jpeg_bytes, frame)."""
    import cv2
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        sys.exit("ERROR: Could not open camera.")
    for _ in range(10):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        sys.exit("ERROR: Failed to capture frame.")
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        sys.exit("ERROR: Failed to encode frame as JPEG.")
    return buf.tobytes(), frame


def load_and_encode(path: str) -> tuple[bytes, object]:
    import cv2
    img = cv2.imread(path)
    if img is None:
        sys.exit(f"ERROR: Could not read image at '{path}'.")
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        sys.exit("ERROR: Failed to encode image.")
    return buf.tobytes(), img


def count_via_claude(jpeg_bytes: bytes) -> tuple[int, str]:
    """Send frame to Claude Vision, return (count, notes)."""
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY not set.")
    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(jpeg_bytes).decode()
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=128,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": SINGLE_HOSPITAL_PROMPT},
        ]}],
    )
    raw = msg.content[0].text.strip()
    print(f"Claude Vision raw:\n{raw}")
    count, notes = 0, ""
    for line in raw.splitlines():
        if line.startswith("COUNT:"):
            try:
                count = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("NOTES:"):
            notes = line.split(":", 1)[1].strip()
    return count, notes


def write_image_to_redis(hospital_id: str, jpeg_bytes: bytes) -> None:
    """Store JPEG as base64 in Redis so Flask can serve it."""
    import redis as rlib
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = rlib.Redis.from_url(url, decode_responses=True,
                            socket_connect_timeout=2, socket_timeout=2)
    r.set(f"vision:image:{hospital_id}", base64.b64encode(jpeg_bytes).decode())


def _pct(qty: int, capacity: int) -> float:
    if capacity <= 0:
        return 0.0
    return round(min(100.0, max(0.0, qty / capacity * 100.0)), 1)


def main():
    hospital_id = os.environ.get("HOSPITAL_ID", "").lower().strip()
    if not hospital_id:
        sys.exit("ERROR: Set HOSPITAL_ID=hospital_a or hospital_b")
    if hospital_id not in VALID_HOSPITALS:
        sys.exit(f"ERROR: HOSPITAL_ID must be one of {VALID_HOSPITALS}")

    item = os.environ.get("ITEM", "Saline")
    capacity = int(os.environ.get("CAPACITY", "10"))
    reserve = int(os.environ.get("RESERVE", "2"))

    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group()
    src.add_argument("--count", type=int, help="Manual count (skip camera + Claude)")
    src.add_argument("--image", help="Use a saved image instead of camera")
    args = p.parse_args()

    label = "A" if hospital_id == "hospital_a" else "B"
    print(f"\n=== Hospital {label} ({hospital_id}) capture ===")

    jpeg_bytes = None
    notes = ""

    if args.count is not None:
        qty = args.count
        notes = f"manual count: {qty}"
        print(f"Manual count: {qty}")
    else:
        if args.image:
            jpeg_bytes, _ = load_and_encode(args.image)
            print(f"Loaded image: {args.image}")
        else:
            print("Capturing from camera...")
            jpeg_bytes, frame = capture_and_encode()
            # Also save locally for reference
            import cv2
            out = str(Path(__file__).parent / f"capture_{hospital_id}_{int(time.time())}.jpg")
            cv2.imwrite(out, frame)
            print(f"Frame saved: {out}")

        print("Sending to Claude Vision...")
        qty, notes = count_via_claude(jpeg_bytes)
        print(f"Detected: {qty} {item} — {notes}")

    # Write image to Redis for dashboard display
    if jpeg_bytes:
        write_image_to_redis(hospital_id, jpeg_bytes)
        print(f"Image written to Redis: vision:image:{hospital_id}")

    # Write inventory + surplus
    pct = _pct(qty, capacity)
    status = status_from_pct(pct)
    surplus = max(0, qty - reserve)
    write_inventory(hospital_id, item, qty, pct, status)
    write_surplus(hospital_id, item, surplus)
    print(f"Inventory: {item} qty={qty} pct={pct} status={status} surplus={surplus}")

    # Update vision:latest (merge with other hospital's existing data)
    import redis as rlib
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = rlib.Redis.from_url(url, decode_responses=True,
                            socket_connect_timeout=2, socket_timeout=2)
    try:
        existing_raw = r.get("vision:latest")
        existing = json.loads(existing_raw) if existing_raw else {}
    except Exception:
        existing = {}

    item_key = item.lower().replace(" ", "_")
    existing[hospital_id] = {item_key: qty}
    existing["captured_at"] = datetime.now(timezone.utc).isoformat()
    existing["source"] = f"capture_single ({hospital_id})"
    if notes:
        existing["notes"] = notes

    write_latest_vision_result(existing)
    print("vision:latest updated.")
    print(f"=== Done — Hospital {label} ready ===\n")


if __name__ == "__main__":
    main()
