# Raspberry Pi 5 — Dual CSI Camera Starter

Pure camera-validation project for a **Raspberry Pi 5** with **two CSI cameras**.
It detects both cameras, captures one still from each, and saves them locally.

**No Claude API. No Redis. No application logic.** This only proves the dual-camera
hardware works before any integration code is written.

## What it does

1. Detects Camera 0 and Camera 1 via Picamera2.
2. Captures:
   - `facility_a.jpg` (Camera 0)
   - `facility_b.jpg` (Camera 1)
3. Saves both images to the current directory.
4. Prints `Camera A captured` and `Camera B captured`.

## Hardware

- Raspberry Pi 5
- Two CSI cameras connected to the **CAM0** and **CAM1** ports
  (the Pi 5 has two 4-lane MIPI CSI/DSI connectors; use the Pi 5 / camera FPC
  cables, the connector contacts face the correct side on each end).

> The Pi 5 needs the newer **22-pin → 15-pin** camera cables. The cables that
> shipped with older Pis (Pi 4 and earlier) will not fit the Pi 5 ports.

## Raspberry Pi setup (exact steps)

Run these **on the Pi**, in a terminal.

### 1. Update the OS

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

### 2. Install Picamera2 and the camera tools

Picamera2 must come from APT (it is tied to the system libcamera stack):

```bash
sudo apt install -y python3-picamera2 rpicam-apps
```

### 3. Confirm both cameras are detected

```bash
rpicam-hello --list-cameras
```

You should see **two** entries (indexes `0` and `1`). If only one (or none)
appears, reseat the ribbon cables and check the firmware:

```bash
# Older OS images use libcamera-hello instead of rpicam-hello:
libcamera-hello --list-cameras
```

If auto-detection fails, ensure `camera_auto_detect=1` is set in
`/boot/firmware/config.txt`, then reboot. For non-standard sensors you may need
explicit overlays, e.g.:

```ini
# /boot/firmware/config.txt  (only if auto-detect does not work)
camera_auto_detect=0
dtoverlay=imx708,cam0
dtoverlay=imx708,cam1
```

### 4. Check Python

```bash
python3 --version   # Python 3.x (Raspberry Pi OS Bookworm ships 3.11+)
```

### 5. Verify Picamera2 imports

```bash
python3 -c "from picamera2 import Picamera2; print('Picamera2 OK')"
```

## Run

From this folder, on the Pi:

```bash
python3 dual_camera.py
```

Expected output (model names will vary by sensor):

```
Detected 2 camera(s):
  [0] model=imx708 location=2 id=/base/axi/pcie@120000/rp1/i2c@88000/imx708@1a
  [1] model=imx708 location=2 id=/base/axi/pcie@120000/rp1/i2c@80000/imx708@1a
Camera A captured -> facility_a.jpg
Camera B captured -> facility_b.jpg

Both cameras validated successfully.
```

After a successful run you will have `facility_a.jpg` and `facility_b.jpg` in
this directory.

## requirements.txt note

`requirements.txt` lists `picamera2`/`pillow` for reference only. **On the Pi,
install Picamera2 via APT** (`sudo apt install -y python3-picamera2`) — do not
`pip install` it. If you use a virtualenv on the Pi, create it with
`--system-site-packages` so it can see the APT-installed Picamera2:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
```

## Next Step: Claude Vision Integration

`vision_pipeline.py` is the bridge from raw dual-camera capture to the upcoming
Claude Vision inventory analysis.

Camera-to-facility mapping (fixed):

- **Camera 0 = Facility A**
- **Camera 1 = Facility B**

Because each facility has its **own dedicated camera**, **no green divider is
required** — the old single-camera setup split one frame with a green straw
divider (Facility A left / Facility B right). With two CSI cameras, each frame
is a whole facility, so there is nothing to split.

Running it now:

```bash
python3 vision_pipeline.py
```

This captures `captures/facility_a.jpg` and `captures/facility_b.jpg`, then
prints a **placeholder** analysis JSON (no Anthropic call yet):

```json
{
  "facility_a": { "saline": null },
  "facility_b": { "saline": null }
}
```

### Future flow

```
Dual Cameras
   ↓
Claude Vision
   ↓
Inventory Counts
   ↓
Redis vision:latest
   ↓
Inventory State
```

Still TODO (stubs already marked in `vision_pipeline.py`):

- Anthropic API integration
- Claude Vision prompt
- Redis `vision:latest` integration
- `sync_vision_counts_to_redis()`

## Troubleshooting

| Symptom | Fix |
| :-- | :-- |
| `ModuleNotFoundError: No module named 'picamera2'` | `sudo apt install -y python3-picamera2` (and use `--system-site-packages` venv) |
| `rpicam-hello --list-cameras` shows < 2 cameras | Reseat both ribbon cables; confirm CAM0/CAM1 seating; reboot |
| Only one camera works | Swap cables/ports to isolate a bad cable vs. bad port/sensor |
| `command not found: rpicam-hello` | `sudo apt install -y rpicam-apps`, or use `libcamera-hello` on older images |
| Capture hangs / black image | Ensure nothing else holds the camera; only one process at a time |
