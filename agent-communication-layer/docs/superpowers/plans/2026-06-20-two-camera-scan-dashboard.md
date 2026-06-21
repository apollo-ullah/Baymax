# Plan: Two-Camera Scan Dashboard (Wave 4)

**Date:** 2026-06-20  
**Status:** Implemented

## Goal

Replace the single-MacBook camera setup with a two-camera architecture: one MacBook per hospital, each running a `camera_worker.py` FastAPI server, connected via Tailscale for public WiFi scenarios. Add a web scan dashboard that proxies both MJPEG streams, triggers scans, and shows negotiation narration via SSE.

## Architecture

```
MacBook A (server)            MacBook B (peer)
 camera_worker.py :8765        camera_worker.py :8766
     Hospital A                    Hospital B
         |                              |
         v                              |
     Redis (shared)  <-----------------+
         |
         v
     baymax_agents (negotiation)
         |
         v
     dashboard_bus (pub/sub)
         |
         v
     scan_dashboard.py (FastAPI)
         |
         v
     Browser (demo operator)
```

## New files

- `camera_worker.py` — FastAPI server: `/health`, `/stream` (MJPEG), `/scan` (POST).
  Captures frames, runs `vision_count.py`, writes to Redis.
- `vision_count.py` — `count_shelf(jpeg_bytes, item)` via Claude Vision.
- `scan_dashboard.py` — FastAPI dashboard: MJPEG proxies, `/scan`, `/scan-and-negotiate`, `/decision`, `/narration` (SSE).
- `dashboard_bus.py` — Redis pub/sub bridge: trigger queue, decision queue, narration channel.
- `run_dashboard_demo.py` — Bureau demo + dashboard HTTP server in one process.
- `tailscale_hosts.py` — Resolves peer addresses; applies env for Tailscale mode.
- `tailscale_smoke_test.py` — Connectivity check for the two-MacBook setup.
- `scripts/` — Shell helpers: `free_demo_ports.sh`, `single_mac_demo.sh`, `tailscale_server.sh`, `tailscale_peer_b.sh`, `tailscale_env.sh`.

## Test harnesses
- `wave4_dashboard_e2e_check.py` — offline Bureau test with stubbed dashboard_bus.
