# Vision Pipeline — Hospital Inventory Detection

## What this does

A MacBook camera captures a live frame of a physical shelf divided into two hospital supply sections. That frame is sent to Claude Vision, which counts the saline on each side and returns structured inventory data. The output feeds directly into the Baymax agent pipeline as the real-time inventory signal for Hospital A and Hospital B.

## Physical setup

Place supplies on a flat surface with a **green straw** acting as the physical divider:

```
[ Hospital A saline ]  |  [ Hospital B saline ]
                      (green straw)
```

Claude detects the green straw in the image and uses it as the boundary. Everything to the left is Hospital A, everything to the right is Hospital B.

## How it works

```
MacBook camera
      |
      v
 OpenCV captures frame
      |
      v
 Frame encoded as JPEG (base64)
      |
      v
 Claude Vision (claude-sonnet-4-6)
   - Locates the green straw divider
   - Counts saline left of straw  → Hospital A
   - Counts saline right of straw → Hospital B
      |
      v
 JSON output + saved to disk
```

## Output

```json
{
  "hospital_a": { "saline": 4 },
  "hospital_b": { "saline": 2 },
  "total": 6,
  "notes": "Green straw detected. Hospital A has 4 saline bags, Hospital B has 2.",
  "captured_image": "capture_1782006393.jpg",
  "timestamp": 1782006393
}
```

Each run saves two files side by side:
- `capture_<timestamp>.jpg` — the raw camera frame
- `capture_<timestamp>.json` — the structured inventory result

## Usage

```bash
# Set API key (once per session, or add to .env at project root)
export ANTHROPIC_API_KEY=sk-ant-...

# Run full pipeline (capture + Claude Vision + save)
python3 bottle_counter.py

# Test camera only — no API call
python3 bottle_counter.py --capture-only

# Run on an existing image
python3 bottle_counter.py --image path/to/photo.jpg
```

## Why Claude Vision over traditional CV

Traditional computer vision (object detection models, YOLO, etc.) requires a labeled training dataset and retraining every time the supply type changes. Claude Vision understands what a "saline bag" or "IV fluid bottle" is out of the box, handles variable lighting and shelf arrangements, and can be reprompted in plain English as the demo setup changes — no retraining loop.

## Dependencies

```bash
pip install anthropic opencv-python python-dotenv
```
