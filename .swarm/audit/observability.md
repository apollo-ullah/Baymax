# observability audit

Track: Arize Phoenix decision-chain tracing. Source in `arize/src/`; emitted JSON in `tracks/arize/traces/`.

## Aligned with demo story

The trace **schema** (`arize/src/trace_schema.py`) already models, by name, most of the north-star decision chain. Six stages exist:

1. `inventory_low` — low-stock signal (maps to north-star step 3: VISION/Redis shortfall). `arize/src/trace_inventory.py:32`
2. `forecast_signal` — predicted demand increase for a region+item (maps to step 7's forecast/ingest loop). `arize/src/trace_forecast.py:32`
3. `reasoning_decision` — the agent's operational decision + free-text `reasoning` (maps to step 2's Claude reasoning and step 4's negotiation rationale). `arize/src/trace_reasoning.py:36`
4. `transfer_recommendation` — the transfer output: source/dest/qty/eta (maps to step 4's inter-facility reallocation). `arize/src/trace_transfer.py:36`
5. `decision_chain` — a single linking span with `inventory_signal_strength`, `forecast_signal_strength`, `decision_confidence` (the umbrella trace tying the chain together). `arize/src/trace_decision_chain.py:40`
6. `decision_outcome` — recommended vs actual quantity + `improvement_note` (the eval/feedback loop Arize's prize fit hinges on). `arize/src/trace_decision_outcome.py:36`

The narrative ordering (inventory → forecast → reasoning → transfer → outcome) already matches the demo story's spine and the root README's pitch (`README.md:42`). The Phoenix exporter (`arize/src/phoenix_client.py`) is real OTel: it lazily builds a `TracerProvider` + OTLP-HTTP exporter when `PHOENIX_COLLECTOR_ENDPOINT` is set, and is **fail-open** (always returns a tracer, never crashes if Phoenix is down) — consistent with the project's fail-closed/never-hang ethos.

## Redundant / dead / off-track

- **The entire track is currently disconnected from the live system.** Grep across `agent-communication-layer/` and `fetch/` for `arize`/`phoenix`/`trace_*`/`emit_trace`/`save_trace` returns **zero** hits. Nothing imports these modules. It is a standalone, post-hoc **demo writer**, not push-based instrumentation. The negotiation core (`baymax_agents.py`), the seams (`interfaces.py`), the FRONT agent, settlement, supplier-order, and the `fetch/agents/*` forecast agents emit **no** spans.
- **Duplicated / split-brain layout.** Source lives in `arize/src/`, but `arize/README.md` and `HANDOFF.md` both instruct `cd tracks/arize` to run — implying a `tracks/arize/src/` that does not exist (only `tracks/arize/traces/` is present). The docs point at a path the code isn't at.
- **`trace_store.py` write-path bug → emitted traces are orphaned output.** `TRACES_DIR = Path(__file__).resolve().parent.parent / "traces"` resolves to `arize/traces/` (which does **not** exist), **not** `tracks/arize/traces/` where the 12 committed JSON files actually live. So the committed traces under `tracks/arize/traces/` could not have been produced by the current `arize/src/` code at its current path — they were generated when the package lived under `tracks/arize/` (pre directory-rename, same rename that displaced the venv per CLAUDE.md). Running `demo_trace.py` today would silently create a *new* `arize/traces/` dir and write there.
- **`tracks/arize/traces/*.json` is generated output that is git-tracked.** All 12 files are committed (`git ls-files tracks/arize/traces/`). They are two identical runs (timestamps `030633` / `030637`) of the same hardcoded `demo_trace.py` fixture (IV Fluids, hospital_a←hospital_b, qty 150). `arize/.gitignore` ignores `traces/` *relative to arize/*, but the actual output dir `tracks/arize/traces/` is **not** gitignored — so generated artifacts are checked in. These should be gitignored; keep at most one sample run as a fixture if a reviewer wants to see shape.

## Buggy / untested / risky

- **No tests / harness.** Unlike the other tracks (`wave2/3/4_e2e_check.py`), there is no verification harness for observability. `demo_trace.py` is the only runnable entry and it only prints + writes fixtures; it asserts nothing.
- **Stale docstrings.** `demo_trace.py:4` says "simulates the four trace events" and "Does not connect to Phoenix or use OpenTelemetry **yet**" — both false: it runs **six** trace calls and every `trace_*` module *does* open an OTel span via `get_tracer()`. Misleading to the next reader.
- **`datetime.utcnow()` is deprecated** (Python 3.12+; repo developed on 3.14) at `trace_store.py:19`. Use `datetime.now(timezone.utc)`.
- **Timestamp filename collision risk.** `save_trace` keys files by second-granularity UTC timestamp + trace_name. Two spans of the same trace name within one second overwrite each other; in a live push-based loop (multiple negotiations) this loses traces. Needs a uuid/nanos suffix and/or a shared trace/correlation id.
- **No correlation id across stages.** Each `trace_*` call is independent; `decision_chain` re-states fields rather than carrying a shared `trace_id`/`span` parent. For Phoenix to render one connected trace across the six stages (and the future crisis→...→settlement chain), spans need a common parent context, which the current flat `start_as_current_span` per-call structure does not establish across module calls.
- **Schema has no stage for the north-star additions** — see Open Questions. Adding spans without extending `trace_schema.py` would scatter raw string keys.

## File inventory (path -> 1-line purpose -> keep/cut/refactor)

- `arize/src/trace_schema.py` -> canonical trace-name + span-attr string constants (single source of truth) -> **keep + extend** (add crisis/research, ingest, supplier-order stages).
- `arize/src/phoenix_client.py` -> OTel tracer + fail-open Phoenix OTLP exporter, env-driven (`PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_PROJECT_NAME`) -> **keep** (the reusable core; will back any live instrumentation).
- `arize/src/trace_store.py` -> writes trace dict as pretty JSON to a `traces/` dir -> **refactor** (wrong path → `arize/traces/` not `tracks/arize/traces/`; deprecated `utcnow`; filename collisions).
- `arize/src/trace_inventory.py` -> build+emit `inventory_low` span -> **keep/refactor** (good fixture; call site should move into the inventory/Redis seam).
- `arize/src/trace_forecast.py` -> build+emit `forecast_signal` span -> **keep/refactor** (hook to `fetch/agents/*` forecast writers + the future ingest loop).
- `arize/src/trace_reasoning.py` -> build+emit `reasoning_decision` span (+ free-text reasoning) -> **keep/refactor** (natural home for Claude crisis-research + ranking rationale).
- `arize/src/trace_transfer.py` -> build+emit `transfer_recommendation` span -> **keep/refactor** (hook into `baymax_agents` propose/settle).
- `arize/src/trace_decision_chain.py` -> umbrella span linking the four signals + confidence -> **keep/refactor** (should become the parent span establishing a shared trace context).
- `arize/src/trace_decision_outcome.py` -> recommended-vs-actual + improvement note span -> **keep** (the eval/feedback loop; differentiator for the Arize prize).
- `arize/src/demo_trace.py` -> hardcoded end-to-end six-stage fixture runner -> **keep but fix docstring** (useful as a manual smoke; rename "four"→"six", drop the "not yet" claim).
- `arize/src/__init__.py` -> package marker -> keep.
- `arize/README.md` / `arize/HANDOFF.md` -> track docs -> **refactor** (fix `cd tracks/arize` run path; "decision confidence" listed as a stage in prose but it's an attribute, not a trace).
- `arize/.gitignore` / `arize/.env.example` / `arize/requirements.txt` -> ignores `traces/`, declares `PHOENIX_*` env, pins `arize-phoenix`+OTel+`openinference-instrumentation`+`python-dotenv` -> keep.
- `tracks/arize/traces/*.json` (12 files) -> committed demo output (2 identical runs) -> **cut from git** (gitignore `tracks/arize/traces/`; keep ≤1 run as a sample if desired).

## Dependencies on other tracks

This track is a **consumer/observer** of every other track but currently wired to none. Future hook points (where push-based spans must be inserted):

- **agents/negotiation** (`agent-communication-layer/baymax_agents.py`, `interfaces.py`): `get_inventory` → `trace_inventory_low`; `rank_offers`/proposal → `trace_reasoning_decision` + `trace_transfer_recommendation`; settled deal → `trace_decision_outcome`. The umbrella `trace_decision_chain` should wrap one negotiation lifecycle.
- **forecast** (`fetch/agents/who_agent`, `weather_agent`, `illness_agent`, `fetch/shared/redis_io.py`): each forecast write → `trace_forecast_signal`. This is also where the **ingest-data loop** (north-star step 7) would emit a span per forecast agent run.
- **redis** (`tracks/redis`, `redis_inventory.py`): the inventory source the `inventory_low` stage observes.
- **vision/hardware** (`hardware/camera connection/sync_to_redis.py`): on-demand vision counts feed the same Redis → `inventory_low`.
- **settlement** (`agent-communication-layer/settlement.py`): FET testnet tx ref is a natural `decision_outcome` attribute.
- **ui/dashboard** (`fetch/`, `dashboard_page.html`, `run_dashboard_demo.py`): would *display* Phoenix traces; could also surface them as the "why" behind proactive recommendations.

The wiring respects the project's seam philosophy: instrumentation should be a **one-way, deployment-time hook** (mirror `register_settlement_hook` in `baymax_agents.py`) so the core never imports `arize.*` — keeping the frozen contract and offline harnesses clean.

## Open questions for the lead

1. **Push vs post-hoc.** Confirm the intended model: register an optional tracing hook at deploy time (so `agent-communication-layer` core stays free of `arize` imports and offline harnesses don't require Phoenix), rather than direct imports. Should there be a `register_trace_hook(...)` seam analogous to `register_settlement_hook`?
2. **New north-star stages.** The schema needs (a) **crisis-research** — extend `trace_reasoning_decision`/`trace_schema.py` with a `crisis_type` + ranked at-risk-supplies attribute, or add a new `TRACE_CRISIS_RESEARCH`; (b) **supplier-order fallback** — a new `TRACE_SUPPLIER_ORDER` (vendor/url/qty/wallet) since no current stage covers the Browserbase external-order branch; (c) **ingest** — likely reuse `forecast_signal` per agent but tagged with a source (WHO/weather/illness/CDC). Which of these does the lead want as first-class trace names vs attributes?
3. **Correlation id.** Do we want one `trace_id` threaded crisis→inventory→forecast→reasoning→transfer→outcome so Phoenix renders a single connected trace? Current code emits six independent flat spans.
4. **Path/dup cleanup.** Is `arize/` the canonical home and `tracks/arize/` just legacy output? If so: fix `trace_store.TRACES_DIR`, fix the README run path, gitignore `tracks/arize/traces/`, and decide whether to delete the committed fixtures.
5. **Rebrand.** Track docs and README say "Arize/Phoenix" plainly; no baymax/stockpile naming drift inside this track, but confirm whether the env prefix should join the `STOCKPILE_*` rename (currently `PHOENIX_*`, which is fine as it's the vendor's name).
