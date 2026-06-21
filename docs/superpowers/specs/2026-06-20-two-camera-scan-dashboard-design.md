# Two-Camera Live Scan + Dashboard + One-Click Negotiate

**Date:** 2026-06-20
**Status:** Approved (design) — pending implementation plan
**Scope:** Minimum pitchable product (MPP) for the hackathon demo. Not final.

## 1. Summary

Upgrade Baymax's inventory source from a single camera (green-straw divider
splitting Hospital A / B in one frame) to **two independent live camera feeds**,
one per MacBook, each owning one hospital's shelf. Add a **web dashboard** that:

1. Shows both shelves as **continuous live video**.
2. On **"Scan Hospitals"**, captures a frame from each camera, runs it through
   Claude Vision, and writes fresh counts into the same Redis the negotiation
   already reads.
3. On **"Scan & Negotiate"**, does the scan and then kicks the FRONT agent to
   run a full supply negotiation, streaming each milestone back onto the
   dashboard live.

The negotiation core, ranking, Redis inventory schema, and the existing ASI:One
chat + real-FET-payment path are **unchanged**.

## 2. Goals / Non-goals

**Goals**
- Two MacBooks, each running a tiny HTTP camera worker for one hospital.
- One-click scan refreshes Redis inventory from real shelves via Claude Vision.
- A single web page shows live feeds, fresh counts, and the live negotiation.
- One-click "Scan & Negotiate" triggers the FRONT agent directly.
- Works over a hotspot / shared LAN (hub-and-spoke; MacBooks talk only to the
  server, never to each other).
- Demo-safe: a flaky webcam or missing API key cannot block the pitch.

**Non-goals (out of scope for the MPP)**
- Background polling / continuous auto-scan (explicitly dropped — on-demand only).
- Real signed on-chain FET settlement *from the dashboard* (physically requires
  the ASI:One wallet; see §7). The real-FET path remains the ASI:One flow.
- Authentication, multi-tenant, persistence beyond Redis, production hardening.
- More than two hospitals on the camera path (Hospital C stays mock/seed-only).
- Final visual design — this is a pitchable MPP, not the finished UI.

## 3. Topology (MacBook A doubles as server)

```
┌─ MacBook A (server) ──────────────────────────────┐        ┌─ MacBook B ──────────┐
│  Redis  ·  FRONT agent (run_front.py, trigger poll)│        │  camera_worker.py    │
│  camera_worker.py  --hospital a   :8765           │        │  --hospital b  :8765 │
│  scan_dashboard.py (FastAPI)       :8000  ◄─────── browser │  webcam → Vision     │
└───────────────────────────────────────────────────┘        └──────────────────────┘
        ▲ /stream ▲ /scan        RPUSH baymax:trigger ▲│▼ on_interval pop  │ writes Redis
        │ (local) │                 (Redis, on A) ─────┘│  → start_negotiation  ▼ over hotspot
   browser <img>  └── dashboard fans out /scan to A(local)+B(hotspot) → Redis(A)
```

Redis on MacBook A is the **bidirectional bus** between the dashboard and the
FRONT agent: the dashboard pushes a negotiation trigger onto a Redis list, and
the FRONT agent publishes narration back onto a Redis channel. This avoids any
uAgents-REST-alongside-mailbox uncertainty and the agent's default-port (8000)
collision with the dashboard.

- **MacBook A** runs: Redis, the FRONT agent (`run_front.py`), camera worker A,
  and the dashboard.
- **MacBook B** runs: camera worker B only, with `REDIS_URL` and its `/scan`
  reachable from A over the hotspot.
- Hub-and-spoke: every MacBook reaches only the server (HTTP for scan triggers,
  Redis for inventory writes). MacBooks never talk to each other.

## 4. Components

Each component is an independent unit with a narrow interface, testable alone.

### 4.1 `camera_worker.py` — per-MacBook camera HTTP server

Runs on each MacBook; owns exactly one hospital's shelf. FastAPI + uvicorn.

**Endpoints**
| Method/Path | Behavior |
| :-- | :-- |
| `GET /stream` | Continuous **MJPEG** (`multipart/x-mixed-replace`) of the webcam — the live feed the dashboard embeds in an `<img>`. |
| `POST /scan` | Grab the current frame → Claude Vision single-shelf count → write Redis → return `{hospital, item, qty, pct, status, surplus, frame_b64}`. |
| `GET /health` | `{ok: true, hospital, camera_ok, vision_ok}`. |

**Camera handling.** One background capture thread/loop holds the webcam open
(`cv2.VideoCapture(device)`) and continuously stores the latest JPEG frame in a
shared buffer. Both `/stream` and `/scan` read that buffer — no device
contention between streaming and scanning.

**Config (flags + env)**
- `--hospital a|b` (required) → Redis id `hospital_a` / `hospital_b`.
- `--item` (default `Saline`) — must match the Redis inventory field name.
- `--capacity` (default `10`), `--reserve` (default `2`) — for pct + surplus.
- `--device` (default `0`) — OpenCV camera index.
- `--port` (default `8765`).
- `--mock-count N` — **demo safety**: skip camera+Vision, write a fixed count.
  Also the automatic fallback if the webcam or `ANTHROPIC_API_KEY` is missing,
  so the worker degrades to a deterministic value instead of erroring.
- Env: `REDIS_URL` (default `redis://localhost:6379`), `ANTHROPIC_API_KEY`.

**Redis writes** (identical schema to today's `sync_to_redis.py`, single side):
- `hospital:{id}:inventory` (hash) field `{item}` → JSON
  `{"qty": int, "pct": float, "status": "low|warning|ok", "updated_at": ISO8601}`.
- `hospital:{id}:surplus` (hash) field `{item}` → `str(max(0, qty - reserve))`.
- Derivations: `pct = clamp(qty/capacity*100, 0, 100)`,
  `status = low (<25) | warning (<50) | ok (>=50)`, `surplus = max(0, qty-reserve)`.

### 4.2 New single-shelf Vision prompt

The current `bottle_counter.py` prompt assumes a green-straw divider splitting A
(left) / B (right) in one frame. The two-camera design needs a new prompt that
counts **one shelf**, no divider. Implemented as a self-contained
`vision_count.py` in `adyan-agent-communication-layer/` (model
`claude-sonnet-4-6`, `max_tokens=256`), so the worker does not depend on the
awkwardly-pathed `hardware/camera connection/bottle_counter.py`.

```
You are a hospital supply inventory scanner. This image shows ONE hospital's
supply shelf. Count the {item} units visible — saline bags, IV fluid bags,
bottles, or similar liquid hospital-supply containers.

Respond in EXACTLY this format, nothing else:
COUNT: <integer>
NOTES: <one short sentence, or "None">

If nothing is visible, return 0.
```

Parser pulls the integer after `COUNT:`; on any parse failure it falls back to
`--mock-count` (or 0) and the response flags `vision_ok: false`.

### 4.3 `scan_dashboard.py` — the web dashboard (FastAPI on MacBook A)

| Method/Path | Behavior |
| :-- | :-- |
| `GET /` | Serves the single HTML page (live feeds + buttons + result cards + negotiation feed). |
| `POST /scan` | `asyncio.gather` POST `/scan` to worker A (localhost) + worker B (hotspot IP); return combined counts. |
| `POST /scan-and-negotiate` | Run `/scan`, then `RPUSH` a trigger `{item, requester, quantity}` onto Redis list `baymax:trigger`; return an ack with the item/quantity. |
| `GET /events` | **SSE** (`text/event-stream`) tailing Redis channel `baymax:narration`; pushes each negotiation milestone to the browser live. |
| `GET /health` | Reports reachability of both workers + Redis. |

**Config (env):** `WORKER_A_URL` (default `http://localhost:8765`),
`WORKER_B_URL` (e.g. `http://<B-hotspot-ip>:8765`),
`REDIS_URL` (default `redis://localhost:6379`), `DASHBOARD_PORT` (default `8000`),
`BAYMAX_ITEM` (default `Saline`).

**The page** (server-rendered HTML + vanilla JS; no build step):
- Two panels side by side; each panel's live feed is
  `<img src="{WORKER_x}/stream">` and a result card (qty, pct bar, status,
  surplus) updated from `/scan` responses.
- Buttons: **Scan Hospitals** (`POST /scan`) and **Scan & Negotiate**
  (`POST /scan-and-negotiate`).
- A negotiation feed `<ul>` appended to from an `EventSource('/events')`.

```
┌──────────────────────────  BAYMAX · Supply Operations  ──────────────────────────┐
│  ┌──────── Hospital A ────────┐      ┌──────── Hospital B ────────┐               │
│  │  [ live MJPEG feed ]       │      │  [ live MJPEG feed ]       │               │
│  │  Saline:  2   ▓▓░░░ 20% LOW│      │  Saline: 6   ▓▓▓▓░ 75% OK  │               │
│  │  surplus: 0                │      │  surplus: 4                │               │
│  └────────────────────────────┘      └────────────────────────────┘               │
│            [  ⛶ Scan Hospitals  ]    [  ⚡ Scan & Negotiate  ]                      │
│  ─ Negotiation feed ───────────────────────────────────────────────────────────── │
│   • requesting … broadcasting need for 150 saline                                  │
│   • collecting_offers … Hospital B offers 80, Hospital C offers 70                 │
│   • proposing … 80 from B + 70 from C                                              │
│   • settling … ✅ confirmed (simulated FET ref tx_…)                                │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.4 FRONT agent additions (minimal, additive, flag-gated)

All changes are additive and off by default, so the ASI:One path and existing
offline harnesses (`wave2_e2e_check.py`, `front_agent.py --selftest`, etc.) are
untouched.

1. **Redis trigger poller** on the FRONT agent: an `@agent.on_interval(period≈1s)`
   handler (added in `run_front.py`) that `LPOP`s `baymax:trigger`; on a present
   trigger `{item, requester?, quantity?}` it calls `start_negotiation(ctx, item,
   requester=…, quantity_needed=…, reply_to=None, source="dashboard")`. Runs in
   the agent's own event loop with a real `ctx`, so no extra port and no
   REST-alongside-mailbox dependency. (A uAgent REST `/negotiate` on a distinct
   port is an equivalent alternative if polling latency ever matters; ~1s is fine
   for the demo.) A short in-handler guard ignores a second trigger while one
   dashboard-sourced negotiation is still running.

2. **Narration tap** in the negotiation core's milestone emitter
   (`baymax_agents.py`). When `BAYMAX_NARRATE_REDIS=1`, each milestone also
   `publish`es `{req_id, state, text}` to Redis channel `baymax:narration`. When
   a negotiation's `reply_to is None`, the emitter **skips the chat `ctx.send`**
   and taps Redis only. Default (env unset, `reply_to` set) → behaves exactly as
   today.

3. **Settlement branch.** The negotiation state records `source`
   ("chat" default | "dashboard"). `settle_transfer` settles **dashboard**-
   sourced deals with the existing stub reference (simulated) and narrates
   "confirmed (simulated)", instead of firing `settle_via_payment_protocol`
   (which needs a wallet `reply_to`). Chat-sourced deals are unchanged → real
   FET via ASI:One.

## 5. Data flow

**Scan only**
```
Operator → [Scan] → dashboard POST /scan
  → gather(workerA POST /scan, workerB POST /scan)
      each: frame buffer → Claude Vision count → write hospital:{id}:inventory + :surplus
  → dashboard returns combined counts → result cards update
```

**Scan & negotiate**
```
Operator → [Scan & Negotiate] → dashboard POST /scan-and-negotiate
  → (scan as above)
  → dashboard RPUSH baymax:trigger {item, requester, quantity}
      → FRONT on_interval LPOP baymax:trigger
          → start_negotiation(reply_to=None, source="dashboard")
              → milestones published to Redis baymax:narration (and stub-settled)
  → browser EventSource('/events') tails baymax:narration → live feed updates
```

The negotiation reads the **fresh** counts because the workers wrote Redis
before `/negotiate` fired, and `redis_inventory.py` (under `BAYMAX_REDIS=1`,
already the default in `run_front.py`) reads exactly those keys.

## 6. Networking / runbook (hotspot)

```
# MacBook A (server)
docker compose -f ../tracks/redis/docker-compose.redis.yml up -d   # Redis on :6379, bound 0.0.0.0
./.venv/bin/python run_front.py                                     # FRONT agent + /negotiate
./.venv/bin/python camera_worker.py --hospital a                   # worker A (:8765)
WORKER_B_URL=http://<B-hotspot-ip>:8765 ./.venv/bin/python scan_dashboard.py   # dashboard (:8000)

# MacBook B
REDIS_URL=redis://<A-hotspot-ip>:6379 ANTHROPIC_API_KEY=... \
  ./.venv/bin/python camera_worker.py --hospital b                 # worker B (:8765)

# Smoke tests
redis-cli -h <A-hotspot-ip> ping            # PONG
curl http://<B-hotspot-ip>:8765/health      # {ok:true, hospital:"b", ...}
```

Redis must listen beyond localhost (the compose file already maps `6379:6379`).
All three machines on the same hotspot/LAN; Tailscale works if WiFi blocks
peer ports.

## 7. The settlement constraint (why simulated on the dashboard)

`settle_via_payment_protocol` sends a `RequestPayment` to the negotiation's
`reply_to`, which must be the ASI:One user's wallet-bearing agent — that is what
renders + signs the FET card. A dashboard is not a wallet, so a real on-chain
signature **cannot** originate from the dashboard alone. Decision: the dashboard
path settles **simulated** (stub ref, labeled "simulated"); the **real signed
FET tx remains the unchanged ASI:One flow** (type the intent in ASI:One, approve
in the wallet). Two clean, reliable demos rather than one fragile coupled one.

## 8. New dependencies

Add to `adyan-agent-communication-layer/requirements.txt`: `fastapi`,
`uvicorn[standard]`. Already present: `opencv-python` (hardware reqs),
`anthropic`, `redis`, `httpx`. MacBook B needs a slim install:
`fastapi uvicorn[standard] opencv-python anthropic redis python-dotenv`.

## 9. File layout (all new code in `adyan-agent-communication-layer/`)

| File | New/changed | Purpose |
| :-- | :-- | :-- |
| `camera_worker.py` | new | Per-MacBook camera HTTP server (`/stream`, `/scan`, `/health`). |
| `vision_count.py` | new | Self-contained single-shelf Claude Vision count + parse. |
| `scan_dashboard.py` | new | Dashboard server + embedded HTML page + SSE. |
| `run_front.py` | changed | Add Redis trigger poller (`on_interval` → `start_negotiation`); enable Redis narration tap. |
| `baymax_agents.py` | changed | Narration tap (Redis publish; tolerate `reply_to=None`); `source` flag + simulated settlement branch. |
| `requirements.txt` | changed | Add `fastapi`, `uvicorn[standard]`. |

## 10. Testing / verification

In the project's self-exiting-harness style (no pytest):
- `vision_count.py`: a `--image <path>` / `--mock` CLI path that prints the
  parsed count, runnable against the existing `capture_*.jpg` fixtures.
- `camera_worker.py --hospital a --mock-count 2`: start, `curl /scan`, assert
  Redis `hospital:hospital_a:inventory` / `:surplus` updated; `curl /health`.
- `scan_dashboard.py`: with both workers in `--mock-count` mode, `POST /scan`
  returns both counts; `POST /scan-and-negotiate` produces `baymax:narration`
  milestones and a simulated confirmation visible on `/events`.
- Regression: `wave2_e2e_check.py` and `BAYMAX_SELFTEST=1 front_agent.py` still
  pass unchanged (narration tap + `source` are off/"chat" by default).

## 11. Risks & mitigations

- **macOS camera permission / device contention** → single background capture
  loop + shared frame buffer; `--device` flag; `--mock-count` fallback.
- **Hotspot blocks peer ports** → Tailscale fallback; documented smoke tests.
- **Vision latency or quota** → `/scan` is on-demand (not a loop); `--mock-count`
  fallback keeps the demo deterministic.
- **Touching the negotiation core** → all additions flag-gated / default-off;
  regression harnesses must still pass before merge.
- **Chat-relay 429s** unaffected — dashboard narration uses Redis, not the
  ASI:One relay.
```
