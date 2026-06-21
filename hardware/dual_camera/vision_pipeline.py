#!/usr/bin/env python3
"""Dual CSI camera -> (future) Claude Vision pipeline for Raspberry Pi 5.

This stage bridges raw dual-camera capture to a future Claude Vision
integration. It captures one still per facility and exposes a placeholder
analysis function whose shape matches what the real Claude call will return.

  Camera 0 -> Facility A -> captures/facility_a.jpg
  Camera 1 -> Facility B -> captures/facility_b.jpg

No Anthropic call, no Redis writes yet. Pure capture + contract stub.

Run on the Pi:
    python3 vision_pipeline.py

TODO:
  - Anthropic API integration
  - Claude Vision prompt
  - Redis vision:latest integration
  - sync_vision_counts_to_redis()
"""

import json
import os
import sys

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None  # Allows the placeholder/contract to be imported off-Pi.

CAPTURES_DIR = "captures"

# Camera index -> (facility key, output filename)
CAMERAS = {
    0: ("facility_a", "facility_a.jpg"),
    1: ("facility_b", "facility_b.jpg"),
}


def ensure_captures_dir():
    """Create the captures/ directory if it does not already exist."""
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    return CAPTURES_DIR


def _capture_one(index, filename):
    """Open a single camera, capture one still, save it, then release it."""
    if Picamera2 is None:
        sys.exit(
            "Picamera2 is not installed. Install it on the Pi with:\n"
            "    sudo apt install -y python3-picamera2\n"
            "(see README.md for full setup)"
        )
    path = os.path.join(CAPTURES_DIR, filename)
    cam = Picamera2(index)
    try:
        cam.configure(cam.create_still_configuration())
        cam.start()
        cam.capture_file(path)
    finally:
        cam.close()
    return path


def capture_facility_images():
    """Capture one still per facility and return their paths.

    Camera 0 -> Facility A, Camera 1 -> Facility B.

    Returns:
        dict: {
            "facility_a_image": "captures/facility_a.jpg",
            "facility_b_image": "captures/facility_b.jpg",
        }
    """
    ensure_captures_dir()
    paths = {}
    for index, (facility, filename) in CAMERAS.items():
        paths[f"{facility}_image"] = _capture_one(index, filename)
    return paths


def analyze_with_claude(image_paths):
    """Placeholder for Claude Vision inventory analysis.

    Does NOT call Anthropic yet. Returns the contract shape the real
    integration will fill in, with null counts as placeholders.

    Args:
        image_paths (dict): output of capture_facility_images().

    Returns:
        dict: per-facility item counts (currently null placeholders).

    TODO:
      - Anthropic API integration
      - Claude Vision prompt
      - Redis vision:latest integration
      - sync_vision_counts_to_redis()
    """
    return {
        "facility_a": {
            "saline": None,
        },
        "facility_b": {
            "saline": None,
        },
    }


def main():
    image_paths = capture_facility_images()

    print("Saved images:")
    for key, path in image_paths.items():
        print(f"  {key}: {path}")

    analysis = analyze_with_claude(image_paths)

    print("\nPlaceholder analysis (no Claude call yet):")
    print(json.dumps(analysis, indent=2))


if __name__ == "__main__":
    main()
