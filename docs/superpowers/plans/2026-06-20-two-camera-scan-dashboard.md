# Two-Camera Live Scan + Dashboard + One-Click Negotiate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two per-MacBook camera workers feed live shelf counts (Claude Vision) into Redis; a web dashboard shows both live feeds, scans on demand, and one-click triggers a full negotiation that narrates live back onto the page.

**Architecture:** Each MacBook runs `camera_worker.py` (MJPEG `/stream` + `/scan` → Claude Vision → Redis). MacBook A also runs `scan_dashboard.py` (FastAPI) + the FRONT agent (`run_front.py`). Redis on A is the bidirectional bus: the dashboard `RPUSH`es a negotiation trigger onto `baymax:trigger`; the FRONT agent pops it in an `on_interval` and runs `start_negotiation(reply_to=None)`, which (per existing code) auto-approves + stub-settles; every milestone is published to Redis `baymax:narration`, which the dashboard streams to the browser via SSE.

**Tech Stack:** Python 3.12+, FastAPI + uvicorn, httpx, OpenCV (`opencv-python`), `anthropic` (`claude-sonnet-4-6`), `redis`, uAgents 0.25.2.

## Global Constraints

- All new code lives in `adyan-agent-communication-layer/`. Run from there; venv at `.venv`.
- `import agent_base` FIRST before constructing any Agent/Protocol (event-loop rule). `run_front.py` already does this; do not reorder its imports.
- Testnet only; never touch mainnet. Do not redefine any `protocol.py` model.
- Redis schema (read by `redis_inventory.py`) is fixed: `hospital:{id}:inventory` hash, field = item name, value = JSON `{qty:int, pct:float, status:"low|warning|ok", updated_at:ISO8601}`; `hospital:{id}:surplus` hash, field = item, value = `str(int)`. `id` ∈ `hospital_a|hospital_b`.
- Item names are matched case-insensitively by `redis_inventory._canon_field`. Workers write field `"Saline"`; the dashboard trigger uses `"saline"`. Both resolve.
- All additions to the negotiation core are flag/sink-gated and default-off so `wave2_e2e_check.py`, `wave3_order_e2e_check.py`, and `BAYMAX_SELFTEST=1 front_agent.py` are unchanged.

---

## File Structure

| File | New/changed | Responsibility |
| :-- | :-- | :-- |
| `dashboard_bus.py` | new | Redis bus: `push_trigger`/`pop_trigger` (list `baymax:trigger`), `publish_narration` (sync) + `narration_events()` (async gen) on channel `baymax:narration`. Shared by `run_front` + `scan_dashboard`. No web/uagents import. |
| `vision_count.py` | new | Single-shelf Claude Vision count (`claude-sonnet-4-6`, new no-divider prompt) + `parse_count` + `--image` CLI. Pure function of JPEG bytes. |
| `camera_worker.py` | new | Per-MacBook FastAPI server: background capture loop → latest-frame buffer; `GET /stream` (MJPEG), `POST /scan` (Vision/mock → Redis), `GET /health`. |
| `scan_dashboard.py` | new | FastAPI: `GET /` (HTML page), `POST /scan` (fan out to both workers), `POST /scan-and-negotiate` (scan + push trigger), `GET /events` (SSE narration), `GET /health`. |
| `baymax_agents.py` | changed | Add `register_narration_sink` + a ~6-line tap in `_step`; store `req_id` in the neg dict (start_negotiation + start_order). Nothing else. |
| `run_front.py` | changed | Wire `dashboard_bus.publish_narration` as the narration sink; add an `on_interval` trigger poller calling `start_negotiation(reply_to=None)`. |
| `requirements.txt` | changed | Add `fastapi`, `uvicorn[standard]`. |

---

## Task 1: `dashboard_bus.py` — the Redis bus

**Files:** Create `adyan-agent-communication-layer/dashboard_bus.py`.

**Interfaces — Produces:**
- `TRIGGER_LIST = "baymax:trigger"`, `NARRATION_CHANNEL = "baymax:narration"`
- `push_trigger(item: str, requester: str|None=None, quantity: int|None=None) -> dict`
- `pop_trigger() -> dict | None`
- `publish_narration(payload: dict) -> None`  (sync, swallows all errors)
- `async narration_events() -> AsyncIterator[dict]`

- [ ] Write the module (complete content):

```python
"""dashboard_bus.py — tiny Redis bus between the scan dashboard and FRONT agent.

  * TRIGGER  (list  baymax:trigger)    dashboard RPUSHes a negotiation request;
                                        the FRONT agent LPOPs it in an on_interval.
  * NARRATION(pubsub baymax:narration)  the FRONT agent publishes each milestone;
                                        the dashboard SSE subscribes.

Keeps the negotiation core free of any Redis/web import: run_front wires
publish_narration() as a narration sink; the dashboard reads the same keys.
"""
from __future__ import annotations

import json
import os

TRIGGER_LIST = "baymax:trigger"
NARRATION_CHANNEL = "baymax:narration"
DEFAULT_REDIS_URL = "redis://localhost:6379"
_SOCKET_TIMEOUT_S = float(os.getenv("BAYMAX_REDIS_TIMEOUT", "2.0"))

_client = None


def _redis():
    global _client
    if _client is None:
        import redis
        url = os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
        _client = redis.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=_SOCKET_TIMEOUT_S, socket_timeout=_SOCKET_TIMEOUT_S,
        )
    return _client


def push_trigger(item, requester=None, quantity=None):
    payload = {"item": item, "requester": requester, "quantity": quantity}
    _redis().rpush(TRIGGER_LIST, json.dumps(payload))
    return payload


def pop_trigger():
    raw = _redis().lpop(TRIGGER_LIST)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def publish_narration(payload: dict) -> None:
    try:
        _redis().publish(NARRATION_CHANNEL, json.dumps(payload))
    except Exception:
        pass  # narration must never break the negotiation


async def narration_events():
    """Async generator of narration dicts for the dashboard SSE (one subscription
    per connection — fine for a demo's 1-2 browser tabs)."""
    import redis.asyncio as aredis
    url = os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
    client = aredis.from_url(url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(NARRATION_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                yield json.loads(message["data"])
            except (ValueError, TypeError):
                continue
    finally:
        await pubsub.unsubscribe(NARRATION_CHANNEL)
        await pubsub.aclose()
        await client.aclose()
```

- [ ] Smoke test: `./.venv/bin/python -c "import dashboard_bus as d; print(d.TRIGGER_LIST, d.NARRATION_CHANNEL)"` → prints the two keys (no Redis needed for import).
- [ ] Commit.

## Task 2: `vision_count.py` — single-shelf Vision count

**Files:** Create `adyan-agent-communication-layer/vision_count.py`.

**Interfaces — Produces:** `count_shelf(jpeg_bytes: bytes, item: str="Saline") -> tuple[int|None, str|None]`; `parse_count(raw: str) -> tuple[int|None, str|None]`; `MODEL = "claude-sonnet-4-6"`.

- [ ] Write the module (new no-divider prompt; `parse_count` pulls `COUNT:`/`NOTES:`; raises if no `ANTHROPIC_API_KEY`). `--image <path>` CLI prints the parsed count.
- [ ] Smoke test against an existing fixture frame **without** an API key (parser only): `./.venv/bin/python -c "import vision_count as v; print(v.parse_count('COUNT: 4\nNOTES: four bags'))"` → `(4, 'four bags')`.
- [ ] Commit.

## Task 3: `camera_worker.py` — per-MacBook camera server

**Files:** Create `adyan-agent-communication-layer/camera_worker.py`.

**Interfaces — Consumes:** `vision_count.count_shelf`. **Produces:** HTTP `GET /stream`, `POST /scan`, `GET /health`; `/scan` JSON `{hospital,item,source,notes,qty,pct,status,surplus,frame_b64}`.

Key requirements:
- Always start a daemon capture thread: open `cv2.VideoCapture(--device)`, 5-frame warmup, loop `read()` → `cv2.imencode(".jpg", q=80)` → store in a lock-guarded `_latest_jpeg`; set `_camera_ok`. Self-disables (no frames) if the camera can't open.
- `/stream`: `StreamingResponse(_mjpeg(), media_type="multipart/x-mixed-replace; boundary=frame")`, yielding the latest buffered JPEG ~15 fps.
- `/scan`: count = `--mock-count` if set, else (if a frame + `ANTHROPIC_API_KEY`) `vision_count.count_shelf`, else `0`. Compute pct/status/surplus and write Redis (`hospital:{id}:inventory` JSON + `:surplus` str). Return the result. Handlers are **sync `def`** so FastAPI runs the blocking Vision/Redis calls in its threadpool (never blocks the loop).
- Redis write helper (direct; matches `redis_inventory.py` exactly):

```python
record = {"qty": qty, "pct": pct, "status": status,
          "updated_at": datetime.now(timezone.utc).isoformat()}
r.hset(f"hospital:{hid}:inventory", item, json.dumps(record))
r.hset(f"hospital:{hid}:surplus", item, str(max(0, qty - reserve)))
```

- CLI: `--hospital a|b` (req), `--item Saline`, `--capacity 10`, `--reserve 2`, `--device 0`, `--port 8765`, `--mock-count N`. `uvicorn.run(app, host="0.0.0.0", port=...)`.

- [ ] Write the module.
- [ ] Smoke test (no camera, no key): `./.venv/bin/python camera_worker.py --hospital a --mock-count 2 --port 8765 &` then `curl -s -XPOST localhost:8765/scan` → JSON with `qty:2`; `curl -s localhost:8765/health` → `{ok:true,...}`. Requires a running Redis; if none, expect a Redis connection error on `/scan` only.
- [ ] Commit.

## Task 4: `scan_dashboard.py` — the dashboard

**Files:** Create `adyan-agent-communication-layer/scan_dashboard.py`.

**Interfaces — Consumes:** `dashboard_bus.push_trigger`, `dashboard_bus.narration_events`; the workers' `/scan`+`/stream`. **Produces:** HTTP `GET /`, `POST /scan`, `POST /scan-and-negotiate`, `GET /events`, `GET /health`.

- Env: `WORKER_A_URL` (default `http://localhost:8765`), `WORKER_B_URL` (default `http://localhost:8766` for local two-on-one testing; in the demo set `http://<B-ip>:8765`), `BAYMAX_ITEM` (default `saline`), `DASHBOARD_PORT` (default `8000`).
- `POST /scan`: `asyncio.gather` POST `/scan` to A + B (httpx, 30s timeout, errors captured per-worker); return `{hospital_a, hospital_b}`.
- `POST /scan-and-negotiate`: scan both, then `dashboard_bus.push_trigger(item=BAYMAX_ITEM, requester="Hospital A", quantity=None)`; return ack.
- `GET /events`: SSE; first yield a `connected` event, then `async for msg in dashboard_bus.narration_events(): yield f"data: {json.dumps(msg)}\n\n"`.
- `GET /`: serve a single dark-themed HTML page — two panels each with `<img src="{WORKER}/stream">` + a result card (qty / pct bar / status pill / surplus), **Scan Hospitals** + **Scan & Negotiate** buttons, and a negotiation feed `<ul>` fed by `new EventSource('/events')`. Buttons `fetch()` the POST endpoints and update cards from the JSON.

- [ ] Write the module + embedded HTML.
- [ ] Smoke test: with both workers in `--mock-count` mode + Redis up, start the dashboard; `curl -s -XPOST localhost:8000/scan` → both counts; `curl -s -XPOST localhost:8000/scan-and-negotiate` → ack; open `/` in a browser to see feeds + cards.
- [ ] Commit.

## Task 5: `baymax_agents.py` — narration tap (the only core edit)

**Files:** Modify `adyan-agent-communication-layer/baymax_agents.py`.

**Interfaces — Produces:** `register_narration_sink(fn)`; each milestone (`narrate or final`) calls `fn({"req_id", "state", "detail", "final"})`.

- [ ] After `NEGOTIATIONS: dict[str, dict] = {}` (line 114), add:

```python
# Narration sink (dashboard bus). When wired (run_front), every milestone is also
# handed here so the scan dashboard can show the negotiation live. Off by default
# -> the ASI:One path and the offline harnesses are unchanged.
_NARRATION_SINK = None


def register_narration_sink(fn) -> None:
    """Register a sink fn(payload: dict) -> None (sync) called on each milestone."""
    global _NARRATION_SINK
    _NARRATION_SINK = fn
```

- [ ] In `_step`, after the existing `if reply_to and should_narrate:` block (line 131-132), add:

```python
    if _NARRATION_SINK is not None and (narrate or final):
        try:
            _NARRATION_SINK({"req_id": neg.get("req_id"), "state": state.value,
                             "detail": detail, "final": final})
        except Exception:
            pass  # a narration sink must never break the negotiation
```

- [ ] In `start_negotiation`'s `neg = NEGOTIATIONS[req_id] = {` dict, add `"req_id": req_id,`. Do the same in `start_order`'s `NEGOTIATIONS[req_id] = {` dict.
- [ ] Regression: `./.venv/bin/python wave2_e2e_check.py` and `BAYMAX_SELFTEST=1 ./.venv/bin/python front_agent.py` still pass (sink unset → no behavior change).
- [ ] Commit.

## Task 6: `run_front.py` — wire sink + trigger poller

**Files:** Modify `adyan-agent-communication-layer/run_front.py`.

**Interfaces — Consumes:** `dashboard_bus.{publish_narration, pop_trigger}`, `sp.{register_narration_sink, start_negotiation, NEGOTIATIONS}`.

- [ ] In `build_agent()`, after `sp.register_order_settlement_hook(...)` (line 121), add:

```python
    # (6) Dashboard bus: publish every milestone for the scan dashboard, and poll
    # for dashboard-triggered ("Scan & Negotiate") negotiations. reply_to=None ->
    # the core auto-approves the trade and stub-settles (simulated); the real FET
    # path stays the ASI:One chat flow (reply_to=<chat sender>).
    import dashboard_bus
    sp.register_narration_sink(dashboard_bus.publish_narration)

    @agent.on_interval(period=1.0)
    async def _poll_dashboard_trigger(ctx):
        # One dashboard negotiation at a time; leave the trigger queued if busy.
        for neg in sp.NEGOTIATIONS.values():
            if neg.get("reply_to") is None and not neg.get("done"):
                return
        trig = dashboard_bus.pop_trigger()
        if not trig:
            return
        item = trig.get("item") or os.getenv("BAYMAX_ITEM", "saline")
        requester = trig.get("requester") or "Hospital A"
        qty = trig.get("quantity")
        ctx.logger.info(
            f"dashboard trigger -> start_negotiation({item!r}, "
            f"requester={requester!r}, qty={qty})")
        await sp.start_negotiation(
            ctx, item, requester=requester, quantity_needed=qty, reply_to=None)
```

- [ ] Verify import still loads: `./.venv/bin/python -c "import run_front; a,w = run_front.build_agent(); print('built', a.name, w[:10])"` (needs `.env`/seeds; if it builds without error, wiring is sound).
- [ ] Commit.

## Task 7: `requirements.txt` + docs

**Files:** Modify `adyan-agent-communication-layer/requirements.txt`; update `CLAUDE.md`/`README` runbook (optional).

- [ ] Append `fastapi` and `uvicorn[standard]`. Install: `./.venv/bin/pip install fastapi "uvicorn[standard]"`.
- [ ] Commit.

---

## End-to-end verification (mock, single machine)

```bash
cd adyan-agent-communication-layer
docker compose -f ../tracks/redis/docker-compose.redis.yml up -d
(cd ../tracks/redis/src && REDIS_URL=redis://localhost:6379 ../../../adyan-agent-communication-layer/.venv/bin/python seed_demo_data.py)
./.venv/bin/python camera_worker.py --hospital a --mock-count 1 --port 8765 &
./.venv/bin/python camera_worker.py --hospital b --mock-count 8 --port 8766 &
WORKER_B_URL=http://localhost:8766 ./.venv/bin/python scan_dashboard.py &   # :8000
# Bring B & C surplus agents up (run_hospital_b.py / run_hospital_c.py, mailbox) + run_front.py,
# OR validate the trigger->narration path against a local Bureau. Then:
curl -s -XPOST localhost:8000/scan | python -m json.tool          # A=1, B=8 written to Redis
curl -s -XPOST localhost:8000/scan-and-negotiate                  # pushes baymax:trigger
# open http://localhost:8000  -> live feeds + cards + negotiation feed
```

Regression (must stay green): `./.venv/bin/python wave2_e2e_check.py`; `BAYMAX_SELFTEST=1 ./.venv/bin/python front_agent.py`.

## Self-review notes
- **Spec coverage:** live feeds (Task 3 `/stream`), scan (Task 3/4), one-click negotiate (Task 4 trigger + Task 6 poller), live narration (Task 1 bus + Task 5 tap + Task 4 SSE), simulated settlement (existing `settle_transfer` stub via reply_to=None — verified, no task needed), MacBook-A-as-server topology (runbook). All covered.
- **No placeholders:** core edits given verbatim; new files specified with the exact Redis schema + signatures.
- **Type consistency:** `publish_narration(payload: dict)` ↔ `_NARRATION_SINK(payload)` ↔ SSE `json.dumps(msg)`; `push_trigger/pop_trigger` dict shape `{item,requester,quantity}` ↔ poller reads the same keys.
