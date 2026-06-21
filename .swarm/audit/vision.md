# vision audit

Track: **vision** — camera → Claude Vision → Redis inventory. The physical shelf becomes the live `qty`/`surplus`/`pct`/`status` that the negotiation reads. Files span `hardware/camera connection/`, the agent-side mirrors (`agent-communication-layer/vision_count.py`, `camera_worker.py`, `scan_dashboard.py`), and the Redis writer (`redis/src/vision_sync.py`).

## Aligned with demo story

This track is the **most demo-ready** of the lower layers and directly serves north-star step 3 (vision/Redis reveals a shortfall) and the two-surface story (dashboard + chat over one Redis).

- **The camera→Redis chain is real and end-to-end wired.** `camera_worker.py` (the canonical, live path) holds the webcam open in a background thread, exposes `GET /stream` (MJPEG live feed), `POST /scan` (frame → `vision_count.count_shelf` → Claude Vision → `_write_redis`), and `GET /health`. `scan_dashboard.py` proxies both workers' streams, fans `/scan` across A+B, and `/scan-and-negotiate` derives a shortfall (`reserve − qty`) and pushes a `baymax:trigger` onto the dashboard bus → FRONT runs the negotiation and narrates back over SSE. This is the closest thing in the repo to the "two surfaces, one engine" vision and is exercised by `wave4_dashboard_e2e_check.py` (offline, with a stubbed `dashboard_bus`).
- **Keyless-testable as documented.** `sync_to_redis.py --counts a=0,b=6` works with only `redis` installed (no anthropic/opencv/API key) — `counts_from_vision` is imported lazily so the manual/JSON paths never touch the camera. `camera_worker.py --mock-count N` and `capture_single.py --count N` give the same demo-safety escape hatch. README documents the clinical path; `--counts` is documented in `sync_to_redis.py`'s own docstring.
- **Schema written matches what the negotiation reads.** `camera_worker._write_redis` and `sync_to_redis.sync` both write `hospital:{id}:inventory` (JSON `{qty,pct,status,reserve,updated_at}`) and `hospital:{id}:surplus` (`str(max(0, qty−reserve))`), keyed via `redis/src/schema.py`. `redis_inventory.redis_get_inventory` reads exactly these keys; with `reserve` present it sets `safety_threshold = reserve` so a low shelf count reads as a shortfall, and `spare_capacity` equals the written surplus. The `surplus = max(0, count − reserve)` contract holds across writer and reader. This is a genuine single-source-of-truth bridge (no duplicated key schema).
- **On-demand triggering exists.** A crisis flow *can* force a fresh vision read: `camera_worker` exposes `count_shelf` behind `POST /scan` (HTTP, on demand); `capture_single.py --watch` subscribes to the `vision:capture_request` pub/sub channel and `ui/app.py`'s `/api/capture_request` publishes to it. So vision is not purely a `__main__` script — there is a callable seam (`vision_count.count_shelf(jpeg_bytes, item)` is a pure function) and two trigger mechanisms (HTTP + pub/sub).
- **Demo-mode prompt hardening.** `vision_count.py` has a `BAYMAX_VISION_DEMO` path that counts water bottles / Red Bull cans as saline proxies — pragmatic and realistic for a hackathon table demo.

## Redundant / dead / off-track

- **~10 MB of committed capture artifacts (24 jpg + 2 json = 26 files).** `hardware/camera connection/capture_*.jpg` are runtime camera dumps committed to git (`capture_hospital_a_*.jpg`, `capture_hospital_b_*.jpg`, `capture_<ts>.jpg`). These are pure bloat — generated every run, never read back except the 2 paired `.json` files (only consumable via the rarely-needed `--from-json` path). **CUT all of them** and add `hardware/camera connection/capture_*.jpg` + `capture_*.json` to `.gitignore`. Keeping 1 sample frame for `--image` testing is defensible; 24 is not.
- **A tracked `.pyc`: `hardware/camera connection/__pycache__/bottle_counter.cpython-311.pyc`** is committed despite `__pycache__/` being in `.gitignore` (it was force-added or added before the ignore rule). **CUT.**
- **Four overlapping camera→Redis writers — no single canonical one.** This is the track's biggest code-health problem:
  1. `hardware/camera connection/bottle_counter.py` — ONE frame, green-straw split → A (left) / B (right), `HOSPITAL A:/B:/TOTAL:/NOTES:` prompt format.
  2. `hardware/camera connection/sync_to_redis.py` — wraps `bottle_counter` + Redis writers; the documented bridge.
  3. `hardware/camera connection/capture_single.py` — ONE shelf per machine (no straw split), `COUNT:/NOTES:` format, plus `--loop`/`--watch`/pub-sub; writes image to `vision:image:{hid}` and merges `vision:latest`.
  4. `agent-communication-layer/vision_count.py` + `camera_worker.py` — ONE shelf per MacBook, HTTP server, `COUNT:/NOTES:` format, demo-mode prompts, Tailscale.
  Three of these re-implement the same prompt + parse + Redis-write logic with subtly different prompts and status thresholds. The green-straw single-frame split (`bottle_counter`/`sync_to_redis`) appears **superseded** by the two-camera, one-shelf-per-machine model (`camera_worker`/`capture_single`) that the live dashboard actually uses. See "headline" below.
- **`camera.py` and `take_photo.py` are Raspberry-Pi `picamera2` scripts** (`/home/bmonster/Desktop`, Tkinter GUI). They import `picamera2`, which is not in `requirements.txt` and won't run on the MacBook demo path. Off-track relative to the MacBook-webcam story — almost certainly leftover hardware experiments. **CUT** (or move to an `experiments/` dir) unless a Pi is in the live rig.
- **`hardware/test folder/` (Pico BOOTSEL button test + serial monitor)** is unrelated to the vision pipeline — a hardware button spike. Off-track for this track; harmless but should be flagged as not part of the demo.

## Buggy / untested / risky

- **Status-threshold divergence across writers (real inconsistency).** `redis/src/inventory.status_from_pct` (used by `sync_to_redis`/`capture_single`): `<25 low`, `<50 warning`, else `ok`. `camera_worker._status`: identical thresholds but **inverted label/comment intent** — fine numerically. BUT `redis/src/vision_sync.status_from_count` uses a *completely different* scheme on raw count: `0→warning, 1→low, ≥2→ok`. Three status vocabularies exist for the same field; whichever writer ran last wins. Pick one.
- **`vision_sync.sync_vision_counts_to_redis` writes inventory but NOT surplus, and writes no `reserve`.** If anything calls that path (it's the Redis-track's own writer), `redis_inventory` falls through to `safety_threshold = round(capacity*0.5)` and `spare_capacity` will not match the camera's intended surplus. In practice the live path uses `sync_to_redis`/`camera_worker` (which DO write surplus), so this is latent, not active — but it's a trap if someone wires the dashboard to the Redis-track writer. Note: `sync_to_redis.sync` also does **not** write a `reserve` field into the inventory record (only `camera_worker` does), so `redis_inventory` reading sync_to_redis data falls back to the legacy `qty − surplus` threshold rather than the reserve floor — a behavioral difference between the two "live" writers.
- **No automated test touches Claude Vision or the camera.** `wave4_dashboard_e2e_check.py` stubs `dashboard_bus` and never calls `count_shelf` or opens a camera; the vision call itself is verified only by manual `--image`/live runs. The parsing functions (`bottle_counter.parse_result`, `vision_count.parse_count`, `sync_to_redis.counts_from_*`) are pure and trivially unit-testable but have **zero tests**. The model output format ("`COUNT: <n>`") is load-bearing and unguarded against drift.
- **`camera_worker.scan()` swallows all Vision errors to `count=0, source="fallback-0"`** (lines 170–173). Good for demo resilience, but a silent 0 reads as a max shortfall in `_shortfall_from_scan` — a flaky API key could spuriously trigger a "crisis" negotiation. The `notes`/`source` fields surface it, but only if someone reads them.
- **`bottle_counter.capture_frame()` / `load_image()` docstrings say "return JPEG bytes" but actually return a `(jpeg_bytes, frame)` tuple.** Callers handle it correctly; the docstring/`-> bytes` annotation is wrong. Cosmetic but misleading.
- **Model id `claude-sonnet-4-6` is hardcoded in 3 places** (`bottle_counter.py`, `vision_count.py`, `capture_single.py`) with no env override and no validation — if that alias is wrong/retired the whole vision path fails at runtime with an API error. Worth centralizing.

## File inventory (path -> 1-line purpose -> keep/cut/refactor)

| Path | Purpose | Verdict |
| :-- | :-- | :-- |
| `agent-communication-layer/camera_worker.py` | Per-MacBook HTTP camera server (`/stream`,`/scan`,`/health`) → Vision → Redis | **KEEP** (canonical live path) |
| `agent-communication-layer/vision_count.py` | Pure `count_shelf(jpeg,item)` Claude-Vision fn + demo prompts; the reusable seam | **KEEP** (canonical vision call) |
| `agent-communication-layer/scan_dashboard.py` | FastAPI dashboard: proxies streams, `/scan`, `/scan-and-negotiate` → bus | **KEEP** (the demo surface) |
| `redis/src/vision_sync.py` | Redis writer for vision counts + `vision:latest`; count-based status | **REFACTOR** (no surplus/reserve; 3rd status scheme — reconcile or retire) |
| `redis/src/inventory.py` | `write_inventory`/`write_surplus`/`status_from_pct` Redis helpers | **KEEP** (shared schema; not vision-only) |
| `hardware/camera connection/sync_to_redis.py` | Green-straw split bridge: `bottle_counter` → Redis; documented `--counts` path | **REFACTOR/CONSOLIDATE** (overlaps camera_worker; keyless test path is the one thing worth preserving) |
| `hardware/camera connection/bottle_counter.py` | ONE-frame green-straw A/B split via Claude Vision | **CONSOLIDATE** (superseded by one-shelf-per-cam model) |
| `hardware/camera connection/capture_single.py` | One-shelf-per-machine capture + `--loop`/`--watch` pub-sub + `vision:image` | **REFACTOR** (overlaps camera_worker; pub-sub `--watch` trigger is unique & useful) |
| `hardware/camera connection/camera.py` | Raspberry-Pi `picamera2` Tkinter capture/record GUI | **CUT** (Pi, not MacBook demo; dep not in requirements) |
| `hardware/camera connection/take_photo.py` | Raspberry-Pi `picamera2` one-shot still | **CUT** (Pi-only) |
| `hardware/camera connection/README.md` | Vision pipeline docs (green-straw model) | **KEEP** (update to reflect canonical two-cam path) |
| `hardware/requirements.txt` | `anthropic`/`opencv-python`/`dotenv`/`redis` | **KEEP** (missing `fastapi`/`uvicorn`/`httpx`/`picamera2` if those paths are kept) |
| `hardware/camera connection/capture_*.jpg` (24) + `*.json` (2) | Runtime camera dumps committed to git (~10 MB) | **CUT** + gitignore |
| `hardware/camera connection/__pycache__/*.pyc` | Stray committed bytecode | **CUT** + already gitignored |
| `hardware/test folder/` (`monitor.py`,`pico_code/main.py`,README) | Pico BOOTSEL button serial test | **CUT** (off-track hardware spike) |

## Dependencies on other tracks

- **redis track (`redis/src/`):** hard dependency. `sync_to_redis.py` and `capture_single.py` add `redis/src` to `sys.path` and import `inventory.{status_from_pct,write_inventory,write_surplus}` + `vision_sync.write_latest_vision_result`. Key names come from `redis/src/schema.py`. `camera_worker.py` instead hand-rolls the same key writes inline (does NOT import the redis helpers) — a divergence to flag.
- **agent-communication-layer / negotiation:** vision is the upstream producer of the `get_inventory` seam. `redis_inventory.redis_get_inventory` consumes the `qty`/`surplus`/`reserve` this track writes; `scan_dashboard` pushes triggers onto `dashboard_bus` (`baymax:trigger`) that FRONT consumes. Contract surface = the Redis key schema + the `reserve`/`surplus` fields, not a Python import.
- **ui track (`ui/app.py`, Flask):** subprocess-shells `capture_single.py` (`/api/capture`), publishes `vision:capture_request` (`/api/capture_request`), and reads `vision:latest` + `vision:image:{hid}`. This is a SECOND dashboard parallel to `scan_dashboard.py` (FastAPI) — two UIs consume this track's output differently.
- **fetch / who_agent (`fetch/agents/who_agent/fetcher.py`):** reads `vision:latest` as `vision_snapshot` into the Claude forecasting prompt (north-star step 7 ingest loop). Soft dependency on the `vision:latest` JSON shape. (Note: it does NOT import `vision_count` — that grep hit was a local variable named `vision_counts`.)

## Open questions for the lead

1. **Which writer is canonical?** The repo has FOUR camera→Redis writers (green-straw `bottle_counter`/`sync_to_redis` vs. one-shelf `camera_worker`/`capture_single`). The live dashboard uses `camera_worker`. Should the green-straw single-frame path be retired, or is it still the fallback for a one-laptop demo? This decision unblocks ~3 files of consolidation.
2. **Which dashboard is canonical** — FastAPI `scan_dashboard.py` (agent-layer) or Flask `ui/app.py`? They consume vision output via different mechanisms (HTTP `/scan` vs. subprocess + pub-sub). The north-star says ONE dashboard.
3. **Status vocabulary:** reconcile the three `status` schemes (`status_from_pct` ×2 paths vs. `status_from_count`). Should `vision_sync.sync_vision_counts_to_redis` be deleted in favor of `sync_to_redis`/`camera_worker`, or fixed to also write `surplus`+`reserve`?
4. **Crisis-driven fresh read:** a future `research_crisis` seam will want to force a vision scan for a *selected* at-risk item. Today `count_shelf` is item-parameterized but the prompts are saline-specific. Does the realignment need `count_shelf` generalized per arbitrary item, or is saline the only camera-backed item for the demo?
5. **Can the ~10 MB of `capture_*.jpg` be purged from history** (BFG/filter-repo) or just removed going forward + gitignored?
