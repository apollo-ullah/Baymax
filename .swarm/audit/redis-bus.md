# redis-bus audit

Track = the Redis state bus + seed scripts under `redis/` (plus its two cross-track bridges: `agent-communication-layer/redis_inventory.py` reads it, `fetch/shared/redis_io.py` lets forecast agents write it). It is the integration seam every other workstream shares. Verdict up front: the **core read/write helpers and key schema are solid and the dashboard genuinely depends on them**, but the published "contract" (`redis_contract.md` / `schema.py` / `HANDOFF.md`) has **drifted out of sync with what writers and readers actually use** — most notably the entire crisis-research output already exists in Redis (`reasoning:latest`) but is undocumented, and several contract keys (`transfers`/`alerts:log` streams, `channels:events`/`channels:alerts`, `history:usage`) have **no live producer or consumer** in the negotiation/dashboard path.

## Aligned with demo story

- **Inventory + surplus + meta are the live shortfall source of truth (steps 3-4).** `redis/src/inventory.py` (`write_inventory`/`get_inventory`/`write_surplus`) and `seed_demo_data.py` seed `hospital:{id}:inventory` (hash, item -> JSON `{qty,pct,status,updated_at}`), `:surplus`, `:meta`. The agent reader `agent-communication-layer/redis_inventory.py` reuses `schema.*` (single source of truth for key names) and maps display names -> ids and "IV fluids"/"saline" -> "IV Fluids"/"Saline" case-insensitively. **Keys + casing match** between seed, camera writer, and agent reader. The seeded `hospital_a` IV Fluids (pct=20, surplus=0) reads as a real shortfall, and B(150)/C(80) surplus produces the canonical 150+50 split the negotiation core expects.
- **Forecast key feeds the proactive/ingest signal (step 7).** `forecast:{region}` JSON string, written by `seed_demo_data.seed_forecast` and merged (not clobbered) by `fetch/shared/redis_io.upsert_forecast_items`, which the weather + illness agents call. WHO agent (`fetch/agents/who_agent/fetcher.py`) is the real ingest orchestrator: WHO/disease.sh + Open-Meteo + illness feed -> merge into `forecast:{region}` -> Claude reasoning.
- **`reasoning:latest` IS the crisis-research output the north-star asks for (step 2/7) — it already exists.** `who_agent/fetcher.py:502` writes `{risk_level, priority_items, reasoning, recommended_action, updated_at}` and `ui/app.py:247` reads it onto the dashboard. This is exactly the "ranked at-risk supplies + rationale + proactive recommendation" shape. It is **not a missing seam — it is an undocumented one** (see drift section).
- **`scenario:{id}` + `vision:latest` are real and consumed.** `ui/app.py` renders `scenario:heatstroke` and `vision:latest`; WHO reasoning also pulls both as Claude context. `vision_sync.py` and the camera `sync_to_redis.py` both write inventory from the camera.
- **Resilience matches the fail-closed contract.** `redis_inventory._client()` adds socket timeouts (the track's own `get_redis()` sets none) and the seam falls back to mock on any failure, so a Redis outage never hangs a handler.

## Redundant / dead / off-track

- **`channels:events` / `channels:alerts` pub/sub: no live subscriber.** `inventory.py`/`forecast.py`/`transfers.py` publish to `channels:events`; `alerts.py` publishes to `channels:alerts`. But the dashboard (`ui/app.py:145`, `dashboard_bus.py:19`) subscribes to a **different, undocumented channel `baymax:narration`** (published by the FRONT agent, not by this track). No code anywhere subscribes to `channels:events`/`channels:alerts`. The publishes are dead fan-out.
- **`transfers` stream + `alerts:log` stream: written only by the demo, never by the live system.** `ui/app.py:275/285` reads both (`xrevrange ... count=5`), but the **negotiation layer never writes them** — `redis_inventory.py` only reads inventory; no agent imports `log_transfer` / `detect_shortfall_and_publish`. So in a real run the dashboard's transfer/alert panels stay empty unless `demo_run.py` seeded them. These streams are demo-only audit logs today.
- **`vector_history.py` / `history:usage` / `HISTORY_INDEX`: orphaned.** Only `demo_run.py` calls `seed_history`/`find_similar_periods`; no agent, dashboard, or research path uses it. `HISTORY_INDEX = "history:usage:index"` is defined and never used (no RediSearch index built). Pulls in the heavy `sentence-transformers` + `numpy` deps for an unused feature.
- **`alerts.py` (`detect_shortfall_and_publish`): off the live path.** Shortfall detection in the demo is duplicated by the agent layer's own `interfaces.get_inventory` + ranking; this Redis-side detector isn't invoked outside `demo_run.py`.
- **`HEATSTROKE_SCENARIO` is a placeholder shell.** Its `inventory`/`forecast`/`recommendation` fields are all `None`/`awaiting_*` and `scenario.py`'s own docstring says no agent fills them in. It's read as Claude context but never updated — half-wired toward the crisis story.
- **`ping.py`** duplicates `redis_client.ping_redis` with a thin CLI wrapper; trivial, keep or fold.

## Buggy / untested / risky

- **No test/harness inside this track.** `demo_run.py` is the only exerciser and it is a print-driven manual script, not a self-asserting harness; the project's real harnesses (`wave*_e2e_check.py`) live in `agent-communication-layer/` and only touch this track through `redis_inventory.py`. Schema drift here is caught by nothing.
- **Writer schema drift on the inventory record — the highest-risk bug.** Three writers disagree on fields:
  - `seed_demo_data` / `inventory.write_inventory`: `{qty, pct, status, updated_at}` (no `reserve`).
  - camera `hardware/.../sync_to_redis.py`: calls `write_inventory`, so `{qty, pct, status, updated_at}` + writes `:surplus` (still **no `reserve`**).
  - `vision_sync.sync_vision_counts_to_redis`: `{qty, status, updated_at, source}` — **no `pct`, no `reserve`**.
  But the reader `redis_inventory.py:155/163` does `capacity = round(qty/(pct/100))` and `reserve = record.get("reserve",0)`. With `vision_sync` records `pct` is absent -> `capacity` falls back to meta capacity (ok), but **no writer ever sets `reserve`**, so the reader's preferred `safety_threshold = reserve` branch is dead and it always falls through to `qty - surplus` or `capacity*0.5`. The CLAUDE.md narrative ("camera workers store a fixed `reserve`") does not match any code in the repo. Confirm whether a `reserve`-writing camera path is intended or remove the dead branch.
- **`vision_sync` and the camera `sync_to_redis` are two competing vision->inventory writers** with different record shapes writing the same `hospital:{id}:inventory` hash field ("Saline"). Last writer wins; their schemas differ (`pct` present vs absent). Pick one.
- **`forecast:{region}.items` is a heterogeneous bag, and `alerts.py` silently assumes one shape.** Seed writes per-item `{predicted_demand_increase_pct, reason}`; the live agents write **flat scalar keys** (`weather_temperature_c`, `who_covid_30d_cases`, `illness:{...}`, etc.) into the same `items` dict. `alerts.detect_shortfall_and_publish` does `forecast_items.get(item,{}).get("predicted_demand_increase_pct",0)` — after a live ingest run those structured per-item entries are gone, so the demand-rising branch silently never fires (no crash, just wrong). The contract says `forecast:{region}` is `{items{...}}` but doesn't pin the per-item shape; writers have diverged.
- **`redis_client.get_redis()` sets no socket timeout** — a slow/unreachable Redis blocks any caller using the track's own client (the agent reader works around this with its own client; `fetch/shared/redis_io` and the dashboard inherit the no-timeout client).
- **`.env.example` ships `REDIS_URL=` empty.** `redis_client` would feed that to `from_url("")` and fail; only `redis_inventory.py` guards it (`os.getenv(...) or DEFAULT`). The track's own client does `getenv("REDIS_URL", DEFAULT)` which does NOT catch the empty-string case. Minor, but it's the documented default.

## File inventory (path -> 1-line purpose -> keep/cut/refactor)

- `redis/redis_contract.md` -> locked schema doc (owners/readers/purpose) -> **refactor** (add `reasoning:latest` + `baymax:narration`; mark streams/pubsub/history as demo-only or cut).
- `redis/src/schema.py` -> key-name/stream/channel constants, no I/O -> **keep** (add `reasoning:latest`; `HISTORY_INDEX` unused).
- `redis/src/seed_demo_data.py` -> seeds meta/inventory/surplus/forecast/scenario -> **keep** (load-bearing for offline demo; align forecast item shape with live agents).
- `redis/src/inventory.py` -> inventory+surplus R/W, publishes `channels:events` -> **keep R/W; refactor** out dead publish or wire a subscriber.
- `redis/src/forecast.py` -> forecast R/W (JSON string), publishes event -> **keep**.
- `redis/src/alerts.py` -> shortfall detect + publish to stream/channel -> **refactor/cut** (off live path; demand shape assumption broken by live forecast).
- `redis/src/transfers.py` -> transfer stream log + event -> **keep but wire** (negotiation layer must call `log_transfer` for the dashboard panel to be real).
- `redis/src/scenario.py` -> scenario JSON R/W + logging -> **keep** (read by dashboard + WHO; scenario object itself is a placeholder).
- `redis/src/vision_sync.py` -> camera counts -> inventory + `vision:latest` -> **refactor** (reconcile record shape with camera `sync_to_redis`; one writer).
- `redis/src/vector_history.py` -> embedding similarity recall over `history:usage` -> **cut** (orphaned; drops `sentence-transformers`+`numpy`) unless a research path will use it.
- `redis/src/redis_client.py` -> connection + ping helper -> **keep; refactor** (add socket timeout; handle empty `REDIS_URL`).
- `redis/src/demo_run.py` -> print-driven E2E demo runner -> **keep** (only exerciser; not a real test).
- `redis/src/ping.py` -> CLI connectivity check -> **keep/fold** into demo_run.
- `redis/src/__init__.py` -> empty package marker -> **keep**.
- `redis/docker-compose.redis.yml` -> redis-stack on 6379 + Insight 8081 -> **keep** (comment notes 8001 clash with FRONT agent — good).
- `redis/README.md` / `redis/HANDOFF.md` -> setup + teammate key reference -> **refactor** (reference `tracks/redis` paths that don't exist post-restructure; `reasoning:latest`/`baymax:narration` missing).
- `redis/.env.example` / `.gitignore` / `requirements.txt` -> config/deps -> **keep** (drop ST/numpy if vector_history cut).
- `agent-communication-layer/redis_inventory.py` (bridge, read) -> live inventory seam -> **keep** (well-built; remove dead `reserve` branch or wire a writer).
- `fetch/shared/redis_io.py` (bridge, write) -> forecast/inventory write re-export + `upsert_forecast_items` -> **keep**.

## Dependencies on other tracks

- **agent-communication-layer (reader):** `redis_inventory.py` reads `:inventory`/`:surplus`/`:meta` via `schema.*`; gated by `BAYMAX_REDIS=1`. Hard dependency on key names + record shape. It also expects a `reserve` field no writer produces.
- **fetch/agents (forecast writers):** weather + illness agents write `forecast:{region}.items` via `upsert_forecast_items`; WHO agent writes `forecast:{region}` AND the undocumented `reasoning:latest`, and READS `:inventory`/`:surplus`/`vision:latest`/`scenario:heatstroke`. This is where the crisis-research output already lives.
- **ui/ (dashboard, the real reader):** `ui/app.py` reads `forecast:{region}`, `reasoning:latest`, `hospital:{id}:inventory`/`:surplus`, `scenario:heatstroke`, `vision:latest`, and `transfers`/`alerts:log` streams; subscribes to `baymax:narration` (NOT the contract's `channels:*`). The dashboard, not the contract's stated readers, is the true consumer.
- **hardware/ (camera writer):** `hardware/camera connection/sync_to_redis.py` imports `inventory.write_inventory`/`write_surplus` from this track's `src/`; competes with `vision_sync.py`.
- **Path drift:** every bridge adds `repo-root/redis/src` to `sys.path`, but `README.md`/`HANDOFF.md`/several docstrings still say `tracks/redis` / `tracks/fetch` (pre-restructure). Imports work; docs lie.

## Open questions for the lead

1. **Is `reasoning:latest` the official crisis-research key?** It already carries `{risk_level, priority_items, reasoning, recommended_action}` and the dashboard renders it. If yes, it MUST be added to `redis_contract.md`/`schema.py`/`HANDOFF.md` and any future `research_crisis` seam should write it. Should a crisis *prompt* (the user's "wildfires near Hospital A" text) also get a key, e.g. `crisis:active` -> `{prompt, type, region, created_at}`? Nothing stores the prompt today.
2. **Who writes the `transfers` and `alerts:log` streams in a live run?** Right now only `demo_run.py` does; the negotiation layer never calls `log_transfer`. Should `settlement.py`/`baymax_agents.py` emit to `transfers` so the dashboard panel is real, or are those panels demo-only?
3. **Pub/sub split: keep `channels:events`/`channels:alerts` or standardize on `baymax:narration`?** Two parallel event systems exist; the contract's channels have no subscriber. Recommend retiring `channels:*` or pointing the dashboard at them.
4. **`reserve` field:** is a camera path meant to write `reserve` into the inventory record (per CLAUDE.md), or should the dead branch in `redis_inventory.py` be removed? This affects whether vision-driven shortfalls compute correctly.
5. **Two vision writers (`vision_sync.py` vs camera `sync_to_redis.py`) with different record shapes** write the same hash. Which is canonical? `sync_to_redis` writes `pct`; `vision_sync` does not.
6. **Cut `vector_history`/`history:usage`?** Orphaned and pulls heavy deps. Keep only if a research/recall path is planned.
7. **Forecast item shape:** the live agents flatten scalar keys into `forecast.items`, breaking `alerts.py`'s per-item `predicted_demand_increase_pct` assumption. Should the contract pin two sub-namespaces (e.g. `items.signals.*` vs `items.demand.{item}`) so detection and ingest don't collide?
