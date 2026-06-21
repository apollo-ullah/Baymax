"""camera_worker.py — per-MacBook camera HTTP server (one hospital shelf).

Run ONE per MacBook (each owns a single shelf — no green-straw split):

    # MacBook A (also the server)
    ./.venv/bin/python camera_worker.py --hospital a

    # MacBook B (points Redis at A over the hotspot; counts locally via Vision)
    REDIS_URL=redis://<A-ip>:6379 ANTHROPIC_API_KEY=sk-... \
        ./.venv/bin/python camera_worker.py --hospital b

Endpoints:
    GET  /stream   continuous MJPEG of the webcam (the dashboard's live feed)
    POST /scan     grab a frame -> Claude Vision count -> write Redis -> JSON
    GET  /health   {ok, hospital, camera_ok, vision_ok}

Writes the SAME Redis keys the negotiation reads (redis_inventory.py):
    hospital:{id}:inventory  hash  {item} -> JSON {qty,pct,status,updated_at}
    hospital:{id}:surplus    hash  {item} -> str(max(0, qty-reserve))

Demo safety: --mock-count N forces the scan count (skips Vision) so a flaky
webcam or a missing API key can never block the pitch; the live feed still works
whenever a camera is present.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import threading
import time
from datetime import datetime, timezone

import cv2
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

import vision_count

CFG = {"hospital": "a", "item": "Saline", "capacity": 10, "reserve": 2,
       "device": 0, "mock_count": None}
HOSPITAL_IDS = {"a": "hospital_a", "b": "hospital_b"}

_frame_lock = threading.Lock()
_latest_jpeg: bytes | None = None
_camera_ok = False


def _capture_loop() -> None:
    """Hold the webcam open and keep the latest JPEG frame in a shared buffer so
    both /stream and /scan read a frame without contending for the device."""
    global _latest_jpeg, _camera_ok
    cap = cv2.VideoCapture(CFG["device"])
    _camera_ok = bool(cap.isOpened())
    if not _camera_ok:
        print(f"[camera_worker] WARNING: camera {CFG['device']} did not open; "
              f"/stream will be blank. /scan uses --mock-count or 0.")
        return
    for _ in range(5):           # warm up so exposure settles
        cap.read()
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            time.sleep(0.1)
            continue
        enc, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if enc:
            with _frame_lock:
                _latest_jpeg = buf.tobytes()
        time.sleep(0.05)


def _get_latest() -> bytes | None:
    with _frame_lock:
        return _latest_jpeg


def _redis():
    import redis
    url = os.getenv("REDIS_URL") or "redis://localhost:6379"
    return redis.Redis.from_url(
        url, decode_responses=True, socket_connect_timeout=2.0, socket_timeout=2.0)


def _status(pct: float) -> str:
    return "low" if pct < 25 else "warning" if pct < 50 else "ok"


def _write_redis(hid: str, item: str, qty: int) -> dict:
    cap = max(1, CFG["capacity"])
    pct = round(min(100.0, max(0.0, qty / cap * 100.0)), 1)
    status = _status(pct)
    surplus = max(0, qty - CFG["reserve"])
    record = {"qty": qty, "pct": pct, "status": status,
              "updated_at": datetime.now(timezone.utc).isoformat()}
    r = _redis()
    r.hset(f"hospital:{hid}:inventory", item, json.dumps(record))
    r.hset(f"hospital:{hid}:surplus", item, str(surplus))
    return {"qty": qty, "pct": pct, "status": status, "surplus": surplus}


app = FastAPI()


@app.get("/health")
def health():
    return {"ok": True, "hospital": CFG["hospital"], "item": CFG["item"],
            "camera_ok": _camera_ok,
            "vision_ok": bool(os.getenv("ANTHROPIC_API_KEY")),
            "mock_count": CFG["mock_count"]}


def _mjpeg():
    while True:
        jpeg = _get_latest()
        if jpeg is None:
            time.sleep(0.1)
            continue
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
        time.sleep(0.066)


@app.get("/stream")
def stream():
    return StreamingResponse(
        _mjpeg(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.post("/scan")
def scan():
    hid = HOSPITAL_IDS[CFG["hospital"]]
    item = CFG["item"]
    jpeg = _get_latest()
    notes = None
    count = CFG["mock_count"]
    source = "mock"
    if count is None:
        if jpeg is not None and os.getenv("ANTHROPIC_API_KEY"):
            try:
                count, notes = vision_count.count_shelf(jpeg, item)
                source = "vision"
            except Exception as e:           # vision down / quota / parse miss
                notes = f"vision failed: {e}"
        if count is None:
            count, source = 0, "fallback-0"
    try:
        written = _write_redis(hid, item, int(count))
    except Exception as e:
        return JSONResponse(status_code=503,
                            content={"hospital": CFG["hospital"], "item": item,
                                     "error": f"redis write failed: {e}"})
    frame_b64 = base64.standard_b64encode(jpeg).decode() if jpeg else None
    return JSONResponse({"hospital": CFG["hospital"], "item": item,
                         "source": source, "notes": notes,
                         "frame_b64": frame_b64, **written})


def main() -> None:
    p = argparse.ArgumentParser(description="Per-MacBook camera worker (one shelf).")
    p.add_argument("--hospital", required=True, choices=["a", "b"])
    p.add_argument("--item", default="Saline")
    p.add_argument("--capacity", type=int, default=10)
    p.add_argument("--reserve", type=int, default=2)
    p.add_argument("--device", type=int, default=0)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--mock-count", type=int, default=None,
                   help="Force the scan count (skip camera+Vision) — demo safety.")
    a = p.parse_args()
    CFG.update(hospital=a.hospital, item=a.item, capacity=a.capacity,
               reserve=a.reserve, device=a.device, mock_count=a.mock_count)
    threading.Thread(target=_capture_loop, daemon=True).start()
    print(f"[camera_worker] hospital={a.hospital} item={a.item} port={a.port} "
          f"mock_count={a.mock_count} "
          f"REDIS_URL={os.getenv('REDIS_URL', 'redis://localhost:6379')}")
    uvicorn.run(app, host="0.0.0.0", port=a.port)


if __name__ == "__main__":
    main()
