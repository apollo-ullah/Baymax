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

# Pub/sub channel the dashboard publishes one-shot capture requests on.
CAPTURE_CHANNEL = "vision:capture_request"

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


def grab_from_open_camera(cap):
    """Grab + encode one JPEG from an already-open VideoCapture. Returns bytes or None."""
    import cv2
    # Flush a few buffered frames so we read a fresh one each loop.
    for _ in range(3):
        cap.read()
    ret, frame = cap.read()
    if not ret or frame is None:
        return None
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return None
    return buf.tobytes()


def persist(r, hospital_id, item, capacity, reserve, jpeg_bytes, qty, notes):
    """Write image + inventory + surplus + merged vision:latest to Redis."""
    if jpeg_bytes:
        r.set(f"vision:image:{hospital_id}", base64.b64encode(jpeg_bytes).decode())

    pct = _pct(qty, capacity)
    status = status_from_pct(pct)
    surplus = max(0, qty - reserve)
    write_inventory(hospital_id, item, qty, pct, status)
    write_surplus(hospital_id, item, surplus)

    # Merge into vision:latest so the other hospital's data is preserved.
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
    return pct, status, surplus


def _redis():
    import redis as rlib
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    return rlib.Redis.from_url(url, decode_responses=True,
                               socket_connect_timeout=2, socket_timeout=2)


def capture_once(r, hospital_id, label, item, capacity, reserve):
    """Take ONE camera frame, count via Claude Vision, write everything to Redis."""
    import cv2
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Hospital {label}: capturing frame…")
    jpeg_bytes, frame = capture_and_encode()
    out = str(Path(__file__).parent / f"capture_{hospital_id}_{int(time.time())}.jpg")
    cv2.imwrite(out, frame)
    print("Sending to Claude Vision…")
    qty, notes = count_via_claude(jpeg_bytes)
    pct, status, surplus = persist(r, hospital_id, item, capacity, reserve,
                                   jpeg_bytes, qty, notes)
    print(f"Detected: {qty} {item} — {notes} (pct={pct} status={status} surplus={surplus})")
    return qty


def run_watch(hospital_id, label, item, capacity, reserve):
    """Wait for one-shot capture triggers on the shared Redis pub/sub channel.

    The dashboard publishes {"target": "<hospital_id>"|"all"} to CAPTURE_CHANNEL.
    On a matching trigger this takes a single picture + Claude count, then resumes
    waiting. Designed for the remote-trigger demo (click on Laptop A → B captures).
    """
    import redis as rlib
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    print(f"\n=== Hospital {label} WATCH mode — waiting for triggers on "
          f"'{CAPTURE_CHANNEL}' ({url}). Ctrl+C to stop ===")
    while True:
        try:
            # No socket_timeout here: pubsub.listen() blocks indefinitely.
            r = rlib.Redis.from_url(url, decode_responses=True,
                                    socket_connect_timeout=3)
            pubsub = r.pubsub()
            pubsub.subscribe(CAPTURE_CHANNEL)
            print("Subscribed — ready for capture requests.")
            for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    payload = json.loads(message["data"]) if message.get("data") else {}
                except (ValueError, TypeError):
                    payload = {}
                target = str(payload.get("target", "all")).lower()
                if target not in ("all", "both", hospital_id):
                    continue
                print(f"\n[trigger] capture request (target={target})")
                try:
                    capture_once(r, hospital_id, label, item, capacity, reserve)
                    print("=== Capture done — waiting for next trigger ===")
                except SystemExit as e:
                    print(f"capture error: {e}")
                except Exception as e:
                    print(f"capture error: {e}")
        except KeyboardInterrupt:
            print("\nStopped watch mode.")
            return
        except Exception as e:
            print(f"[watch] connection error: {e} — retrying in 2s")
            time.sleep(2)


def run_loop(r, hospital_id, label, item, capacity, reserve, interval, vision_every):
    """Continuous live-feed capture: push a fresh frame every `interval` seconds,
    re-counting via Claude Vision every `vision_every`-th frame."""
    import cv2
    print(f"\n=== Hospital {label} LIVE feed — every {interval}s, "
          f"Claude every {vision_every} frame(s). Ctrl+C to stop ===")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        sys.exit("ERROR: Could not open camera.")
    for _ in range(10):  # warm up the sensor
        cap.read()

    last_qty, frame_i = 0, 0
    try:
        while True:
            jpeg_bytes = grab_from_open_camera(cap)
            if jpeg_bytes is None:
                print("frame grab failed — retrying")
                time.sleep(interval)
                continue

            do_vision = (frame_i % max(1, vision_every) == 0)
            notes = ""
            if do_vision:
                try:
                    last_qty, notes = count_via_claude(jpeg_bytes)
                except SystemExit:
                    raise
                except Exception as e:
                    notes = f"vision error: {e}"
            qty = last_qty

            persist(r, hospital_id, item, capacity, reserve, jpeg_bytes, qty, notes)
            ts = datetime.now().strftime("%H:%M:%S")
            tag = f"counted {qty}" if do_vision else "image only"
            print(f"[{ts}] frame {frame_i}: pushed ({tag})")
            frame_i += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped live feed.")
    finally:
        cap.release()


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
    p.add_argument("--loop", action="store_true",
                   help="Continuously capture a live feed until Ctrl+C")
    p.add_argument("--interval", type=float, default=3.0,
                   help="Seconds between frames in --loop mode (default 3)")
    p.add_argument("--vision-every", type=int, default=1,
                   help="Run Claude Vision every Nth frame in --loop (default 1 = every frame)")
    p.add_argument("--watch", action="store_true",
                   help="Wait for one-shot capture triggers from the dashboard (remote trigger)")
    args = p.parse_args()

    label = "A" if hospital_id == "hospital_a" else "B"

    # ── Remote one-shot trigger (capture once per dashboard click) ───────────
    if args.watch:
        if args.count is not None or args.image:
            sys.exit("ERROR: --watch uses the live camera; don't combine with --count/--image")
        run_watch(hospital_id, label, item, capacity, reserve)
        return

    r = _redis()

    # ── Live feed ──────────────────────────────────────────────────────────
    if args.loop:
        if args.count is not None or args.image:
            sys.exit("ERROR: --loop uses the live camera; don't combine with --count/--image")
        run_loop(r, hospital_id, label, item, capacity, reserve,
                 args.interval, args.vision_every)
        return

    # ── One-shot ───────────────────────────────────────────────────────────
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
            import cv2
            out = str(Path(__file__).parent / f"capture_{hospital_id}_{int(time.time())}.jpg")
            cv2.imwrite(out, frame)
            print(f"Frame saved: {out}")

        print("Sending to Claude Vision...")
        qty, notes = count_via_claude(jpeg_bytes)
        print(f"Detected: {qty} {item} — {notes}")

    pct, status, surplus = persist(r, hospital_id, item, capacity, reserve,
                                   jpeg_bytes, qty, notes)
    if jpeg_bytes:
        print(f"Image written to Redis: vision:image:{hospital_id}")
    print(f"Inventory: {item} qty={qty} pct={pct} status={status} surplus={surplus}")
    print(f"=== Done — Hospital {label} ready ===\n")


if __name__ == "__main__":
    main()
