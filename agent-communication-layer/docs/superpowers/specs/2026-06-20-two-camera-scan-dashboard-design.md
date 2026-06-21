# Design: Two-Camera Scan Dashboard (Wave 4)

## Component layout

```
camera_worker.py           (FastAPI, one per hospital MacBook)
  /health    GET  -> {"status":"ok","hospital":"a","item":"saline","count":N}
  /stream    GET  -> multipart/x-mixed-replace MJPEG
  /scan      POST -> {"count":N,"reserve":R,"surplus":S,"notes":"..."}

scan_dashboard.py          (FastAPI, runs on server MacBook)
  /          GET  -> HTML with two <img> MJPEG streams + control buttons
  /stream-a  GET  -> proxy of worker A /stream
  /stream-b  GET  -> proxy of worker B /stream
  /scan      POST -> {"a":{...},"b":{...}}
  /scan-and-negotiate POST -> push trigger + return shortfall info
  /decision  POST -> {"decision":"approve|order|reject","req_id":"..."}
  /narration GET  -> SSE stream of narration payloads

dashboard_bus.py           (Redis pub/sub bridge)
  baymax:trigger  Redis LIST  — scan-and-negotiate triggers
  baymax:decision Redis LIST  — admin approve/order/reject
  baymax:narration Redis PUBSUB — negotiation milestone events

run_dashboard_demo.py      (Bureau demo + dashboard HTTP server)
  - Polls baymax:trigger every 2s -> start_negotiation(source="dashboard")
  - Polls baymax:decision every 1s -> resume_after_admin_decision()
  - Runs FastAPI dashboard in a daemon thread alongside the Bureau
```

## Tailscale two-MacBook setup

```
MacBook A (server)
  - redis-stack on :6379 (Docker)
  - camera_worker.py --hospital a on :8765
  - run_front.py or run_dashboard_demo.py
  - scan_dashboard.py on :8080

MacBook B (peer)
  - camera_worker.py --hospital b on :8766
  - REDIS_URL=redis://<tailscale-server-ip>:6379
  - BAYMAX_WORKER_B_URL=http://localhost:8766 (local)
  - BAYMAX_WORKER_A_URL=http://<tailscale-server-ip>:8765
```

## Vision pipeline

```
camera_worker._capture_loop()
  -> OpenCV VideoCapture -> JPEG encode
  -> vision_count.count_shelf(jpeg_bytes, item)
      -> Anthropic claude-sonnet-4-6 vision API (or demo mode: water bottle count)
      -> (count, notes)
  -> _write_redis(count, notes)
      -> hospital:{id}:inventory  HSET qty/capacity/reserve/surplus
      -> hospital:{id}:surplus    HSET spare_capacity
```

## Demo mode (BAYMAX_VISION_DEMO=1)

Counts water bottles / props instead of clinical supplies. No item-specific
training or special props needed for the hackathon demo. The full clinical path
is available via the default (demo mode off).
