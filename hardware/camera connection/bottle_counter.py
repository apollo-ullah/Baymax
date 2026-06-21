"""
MacBook camera -> Claude Vision -> bottle count for Section A and Section B.

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python bottle_counter.py

    # Use an existing image instead of the camera:
    python bottle_counter.py --image path/to/photo.jpg

    # Save the captured frame:
    python bottle_counter.py --save
"""

import argparse
import base64
import json
import logging
import os
import sys
import time

import anthropic
import cv2
from dotenv import load_dotenv

log = logging.getLogger("bottle_counter")
logging.basicConfig(level=logging.INFO, format="%(message)s")

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

MODEL = "claude-sonnet-4-6"

PROMPT = """You are a hospital supply inventory scanner.

Examine this image carefully. There is a GREEN STRAW (or green stick/divider) physically placed in the scene that separates two hospital supply sections:
- **Hospital A**: everything to the LEFT of the green straw
- **Hospital B**: everything to the RIGHT of the green straw

Count the number of saline units on each side. For this inventory system, count ALL of the following as one saline unit:
- Saline bags or IV fluid bags
- Water bottles or liquid containers
- **Yellow or red Red Bull cans** (used as saline proxies in this demo environment)
- Any similar cylindrical or pouch-shaped item that could represent a hospital fluid supply

Respond in exactly this format — no extra text before or after:

HOSPITAL A: <number>
HOSPITAL B: <number>
TOTAL: <number>
NOTES: <one sentence describing what you see including can/bottle types detected, or "None" if nothing notable>

If you cannot see the green straw, split the frame visually down the middle and note that no divider was detected. If no items are visible, return 0 for both."""


def capture_frame() -> bytes:
    """Capture a single frame from the default MacBook camera and return JPEG bytes."""
    log.info(json.dumps({
        "tag": "VISION", "file": "bottle_counter.py",
        "action": "camera_capture_start", "device": "MacBook camera (cv2 device 0)",
    }))
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        sys.exit("ERROR: Could not open camera. Check that no other app is using it.")

    # Let the camera warm up for a moment so exposure settles
    for _ in range(10):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        sys.exit("ERROR: Failed to capture frame from camera.")

    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        sys.exit("ERROR: Failed to encode captured frame as JPEG.")

    jpeg_bytes = buf.tobytes()
    log.info(json.dumps({
        "tag": "VISION", "file": "bottle_counter.py",
        "action": "camera_capture_complete",
        "size_bytes": len(jpeg_bytes),
        "timestamp": int(time.time()),
    }))
    return jpeg_bytes, frame


def load_image(path: str) -> bytes:
    """Load an existing image file and return JPEG bytes."""
    img = cv2.imread(path)
    if img is None:
        sys.exit(f"ERROR: Could not read image at '{path}'.")
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        sys.exit("ERROR: Failed to encode image as JPEG.")
    return buf.tobytes(), img


def count_bottles(jpeg_bytes: bytes) -> str:
    """Send the image to Claude Vision and return the raw response text."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY environment variable is not set.")

    log.info(json.dumps({
        "tag": "VISION", "file": "bottle_counter.py",
        "action": "claude_request",
        "model": MODEL,
        "image_size_bytes": len(jpeg_bytes),
        "prompt_chars": len(PROMPT),
    }))

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(jpeg_bytes).decode("utf-8")

    message = client.messages.create(
        model=MODEL,
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
    raw = message.content[0].text
    log.info(json.dumps({
        "tag": "VISION", "file": "bottle_counter.py",
        "action": "claude_response",
        "model": MODEL,
        "raw_response": raw,
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }))
    return raw


def parse_result(raw: str) -> dict:
    result = {"hospital_a": None, "hospital_b": None, "total": None, "notes": ""}
    for line in raw.strip().splitlines():
        line = line.strip()
        if line.startswith("HOSPITAL A:"):
            try:
                result["hospital_a"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("HOSPITAL B:"):
            try:
                result["hospital_b"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("TOTAL:"):
            try:
                result["total"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("NOTES:"):
            result["notes"] = line.split(":", 1)[1].strip()

    log.info(json.dumps({
        "tag": "VISION", "file": "bottle_counter.py",
        "action": "parsed_inventory",
        "hospital_a": {"saline": result["hospital_a"]},
        "hospital_b": {"saline": result["hospital_b"]},
        "total": result["total"],
        "notes": result["notes"],
    }))
    return result


def print_result(result: dict) -> None:
    output = {
        "hospital_a": {"saline": result["hospital_a"]},
        "hospital_b": {"saline": result["hospital_b"]},
        "total": result["total"],
        "notes": result["notes"] if result["notes"].lower() != "none" else None,
    }
    print(json.dumps(output, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Count bottles via Claude Vision.")
    parser.add_argument("--image", help="Path to an existing image (skips camera capture).")
    parser.add_argument("--save", action="store_true", help="Save the captured frame to disk.")
    parser.add_argument("--capture-only", action="store_true", help="Capture and save only — skip the Claude API call.")
    args = parser.parse_args()

    if args.image:
        print(f"Loading image: {args.image}")
        jpeg_bytes, frame = load_image(args.image)
    else:
        print("Capturing frame from MacBook camera...")
        jpeg_bytes, frame = capture_frame()
        print("Frame captured.")

    ts = int(time.time())
    out_path = os.path.join(os.path.dirname(__file__), f"capture_{ts}.jpg")
    if args.capture_only or args.save or not args.image:
        cv2.imwrite(out_path, frame)
        print(f"Frame saved: {out_path}")

    if args.capture_only:
        print("Capture-only mode — skipping Claude API call.")
        return

    print("Sending to Claude Vision...")
    raw = count_bottles(jpeg_bytes)

    result = parse_result(raw)
    print_result(result)

    json_path = out_path.replace(".jpg", ".json")
    with open(json_path, "w") as f:
        output = {
            "hospital_a": {"saline": result["hospital_a"]},
            "hospital_b": {"saline": result["hospital_b"]},
            "total": result["total"],
            "notes": result["notes"] if result["notes"] and result["notes"].lower() != "none" else None,
            "captured_image": out_path,
            "timestamp": ts,
        }
        json.dump(output, f, indent=2)
    print(f"Result saved: {json_path}")


if __name__ == "__main__":
    main()
