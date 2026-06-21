"""vision_count.py — count one hospital shelf via Claude Vision (single shelf).

Unlike `hardware/camera connection/bottle_counter.py` (which splits ONE frame into
Hospital A / B across a green-straw divider), this counts ONE shelf — used by the
two-camera setup where each MacBook's camera_worker owns a single hospital.

Pure function of JPEG bytes:
    count, notes = count_shelf(jpeg_bytes, item="Saline")

CLI (parser/Vision smoke test on a saved frame):
    ./.venv/bin/python vision_count.py --image capture.jpg --item Saline
"""
from __future__ import annotations

import base64
import os
import re

MODEL = "claude-sonnet-4-6"


def _prompt(item: str) -> str:
    return (
        "You are a hospital supply inventory scanner. This image shows ONE "
        "hospital's supply shelf.\n\n"
        f"Count the {item} units visible — saline bags, IV fluid bags, bottles, "
        "or similar liquid hospital-supply containers.\n\n"
        "Respond in EXACTLY this format, nothing else:\n"
        "COUNT: <integer>\n"
        'NOTES: <one short sentence, or "None">\n\n'
        "If nothing is visible, return 0."
    )


_COUNT_RE = re.compile(r"COUNT:\s*(\d+)", re.IGNORECASE)
_NOTES_RE = re.compile(r"NOTES:\s*(.+)", re.IGNORECASE)


def parse_count(raw: str) -> tuple[int | None, str | None]:
    """Pull (count, notes) out of the model's text. count is None if absent."""
    m = _COUNT_RE.search(raw or "")
    count = int(m.group(1)) if m else None
    n = _NOTES_RE.search(raw or "")
    notes = n.group(1).strip() if n else None
    if notes and notes.lower() == "none":
        notes = None
    return count, notes


def count_shelf(jpeg_bytes: bytes, item: str = "Saline") -> tuple[int | None, str | None]:
    """Call Claude Vision on one shelf image; return (count, notes).

    Raises RuntimeError if ANTHROPIC_API_KEY is unset (the worker catches this and
    falls back to its --mock-count / 0)."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(jpeg_bytes).decode("utf-8")
    message = client.messages.create(
        model=MODEL,
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": _prompt(item)},
            ],
        }],
    )
    return parse_count(message.content[0].text)


def main() -> None:
    import argparse
    import sys

    import cv2

    p = argparse.ArgumentParser(description="Single-shelf Claude Vision count.")
    p.add_argument("--image", required=True, help="Path to a shelf image (JPEG/PNG).")
    p.add_argument("--item", default="Saline", help="Item name (default: Saline).")
    a = p.parse_args()
    img = cv2.imread(a.image)
    if img is None:
        sys.exit(f"ERROR: could not read image at {a.image!r}")
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        sys.exit("ERROR: failed to encode image as JPEG")
    count, notes = count_shelf(buf.tobytes(), a.item)
    print(f"COUNT={count}  NOTES={notes}")


if __name__ == "__main__":
    main()
