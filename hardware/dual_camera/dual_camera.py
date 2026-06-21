#!/usr/bin/env python3
"""Dual CSI camera validation for Raspberry Pi 5.

Pure hardware bring-up: detect Camera 0 and Camera 1, capture one still from
each, and save them locally. No Claude API, no Redis, no application logic.

Run on the Pi:
    python3 dual_camera.py
"""

import sys

try:
    from picamera2 import Picamera2
except ImportError:
    sys.exit(
        "Picamera2 is not installed. Install it with:\n"
        "    sudo apt install -y python3-picamera2\n"
        "(see README.md for full setup)"
    )

# Camera index -> (human label, output filename)
CAMERAS = {
    0: ("A", "facility_a.jpg"),
    1: ("B", "facility_b.jpg"),
}


def detect_cameras():
    """Return the list of cameras Picamera2 can see."""
    cameras = Picamera2.global_camera_info()
    print(f"Detected {len(cameras)} camera(s):")
    for idx, info in enumerate(cameras):
        model = info.get("Model", "unknown")
        location = info.get("Location", "?")
        print(f"  [{idx}] model={model} location={location} id={info.get('Id', '?')}")
    return cameras


def capture(index, label, filename):
    """Open a single camera, capture one still, save it, then release it."""
    cam = Picamera2(index)
    try:
        cam.configure(cam.create_still_configuration())
        cam.start()
        cam.capture_file(filename)
    finally:
        cam.close()
    print(f"Camera {label} captured -> {filename}")


def main():
    cameras = detect_cameras()

    if len(cameras) < 2:
        sys.exit(
            f"Expected 2 CSI cameras, found {len(cameras)}.\n"
            "Check the ribbon cables and run: rpicam-hello --list-cameras"
        )

    for index, (label, filename) in CAMERAS.items():
        capture(index, label, filename)

    print("\nBoth cameras validated successfully.")


if __name__ == "__main__":
    main()
