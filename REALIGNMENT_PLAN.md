# REALIGNMENT_PLAN.md — Stockpile/Baymax

Derived from `SCOPE_AUDIT.md`. Organized by the **Wave DAG** from the task body (A → B → C → D), tagged with **P0/P1/P2**. Each task lists **files · seam · harness · deps · owner**. Frozen constraints from the spec apply to every task (never edit `protocol.py`; testnet only; payment metadata; `import agent_base` first; extend via new seams; harnesses are the test suite).

**Guiding principle (from the audit):** prefer *wiring + relabeling existing code* over building new subsystems. The only genuinely new component is the `research_crisis` seam.

---

## Wave A — Foundation (serial; lead reviews before unlocking B)

### A1 · `research_crisis` seam — NEW SEAM 5 · **P0**
- **What:** Add `research_crisis(crisis_text: str, region: str = "bay_area") -> CrisisBrief` to `interfaces.py`, mirroring the existing seam pattern (plain dataclass return, env-gated Claude backend, deterministic mock fallback, fail-closed).
- **Contract (new dataclasses in `interfaces.py`, no uagents dep):**
  ```python
  @dataclass
  class AtRiskSupply:
      item: str        # canonical KNOWN_ITEM where possible ("IV fluids"|"saline"|"sutures")
      risk: str        # "high" | "elevated" | "moderate"
      rationale: str
  @dataclass
  class CrisisBrief:
      crisis_text: str
      crisis_type: str            # "wildfire"|"heatwave"|"flu_surge"|"earthquake"|"unknown"
      region: str
      at_risk: list[AtRiskSupply] # ranked, highest risk first
      rationale: str
      present: bool = True        # False => could not classify
  ```
- **Mock:** keyword → at-risk map over KNOWN_ITEMS (wildfire→saline/IV fluids/sutures; heatwave→IV fluids/saline; flu/surge→IV fluids/saline; earthquake/trauma→sutures/IV fluids/saline; default→IV fluids/saline). **Always returns ≥1 known item** so the offline demo always finds a negotiable item.
- **Claude backend:** new `claude_research.py` (mirror `claude_ranking.py`): `research_crisis_via_claude(crisis_text, region) -> CrisisBrief`, gated by `BAYMAX_CLAUDE_RESEARCH=1` + `ANTHROPIC_API_KEY`, fail-closed to the mock. Constrain Claude's output to the known items (or map free-text → KNOWN_ITEMS defensively, like `_match_item`).
- **Files:** `interfaces.py` (add seam + dataclasses), new `claude_research.py`, `requirements.txt` (no new dep — anthropic already present).
- **Seam:** this *is* the new seam. **Do not alter** `get_inventory`/`rank_offers`/`order_from_supplier`/`approve_release`.
- **Harness:** new `check_research_crisis.py` (offline, mock-only, asserts crisis-type classification + ≥1 known at-risk item, mirrors `check_interfaces_order.py`). Must pass with no network/key.
- **Deps:** none (pure foundation).
- **Owner:** Wave A (lead-reviewed).

### A2 · Redis `crisis:active` key + contract doc · **P0**
- **What:** Define `crisis:active` = `{crisis_text, crisis_type, region, at_risk:[…], rationale, created_at}`; add a writer helper `write_crisis(...)`/`get_crisis()` to `redis/src/scenario.py` (or a new `crisis.py`). Document `crisis:active`, `reasoning:latest`, and `baymax:narration` in `redis_contract.md`/`schema.py` (they exist in code but not the contract).
- **Files:** `redis/src/schema.py` (+`CRISIS_ACTIVE`), `redis/src/scenario.py` or new `crisis.py`, `redis/redis_contract.md`, `redis/HANDOFF.md`.
- **Seam:** consumes A1's `CrisisBrief`; additive key only (no rename).
- **Harness:** covered by A1's check (mock) + the Wave-C dashboard render; optional round-trip in `redis_smoke_test.py`.
- **Deps:** A1 (CrisisBrief shape).
- **Owner:** Wave A (can run alongside A1; both lead-reviewed before B).

---

## Wave B — Parallel (both depend only on Wave A)

### B1 · `crisis` + `ingest` chat-intent kinds + `start_crisis`/`run_ingest` · **P0** · owner: **Agent B1**
- **What:**
  - `front_agent.parse_intent`: add `_CRISIS_CUE_RE` (wildfire|fire|smoke|flood|earthquake|quake|outbreak|surge|pandemic|heat\s?wave|storm|disaster|mass casualty|crisis) and `_INGEST_CUE_RE` (ingest data|ingest|refresh forecast|run forecast|update forecast|pull (the )?data). Detect **crisis BEFORE item-matching** (a crisis prompt won't name a KNOWN_ITEM). Return `{"kind":"crisis","crisis_text":text,"requester":…}` and `{"kind":"ingest","region":…}`. Keep all echo/meta/cooldown hardening.
  - `front_agent.on_intent`: add `crisis` branch (narrate "Researching…" → `start_crisis`) and `ingest` branch (→ `run_ingest`) **before** the `kind not in (...)` fallback.
  - `baymax_agents.start_crisis(ctx, crisis_text, *, requester, reply_to=None, source=None)`: call `interfaces.research_crisis` (off the event loop via `asyncio.to_thread`) → write `crisis:active` → narrate the brief → pick the **top at-risk item that is `present` + below threshold** (else top-ranked) → call existing `start_negotiation(...)` with the same `reply_to`/`source`.
  - `baymax_agents.run_ingest(ctx, region, *, reply_to=None, source=None)`: call the ingest seam (B2) off-loop → narrate `reasoning:latest` (`risk_level` + `priority_items` + `recommended_action`).
- **Files:** `front_agent.py`, `baymax_agents.py`. **Never** touch `protocol.py`.
- **Seam:** consumes A1 (`research_crisis`) + B2 (`ingest_forecast`). Reuses `start_negotiation` unchanged.
- **Harness:** extend `BAYMAX_SELFTEST` to also drive a crisis intent (`BAYMAX_SELFTEST_INTENT="wildfires near Hospital A"` → CONFIRMED); add `wave5_crisis_e2e_check.py` (offline: crisis text → research mock → negotiate → approve → settle). `wave2`/`wave3` must still pass unchanged.
- **Deps:** A1, B2 (for the ingest branch only — crisis branch needs only A1).
- **Owner:** Agent B1.

### B2 · Promote the ingest orchestrator + `ingest_forecast` seam + CDC stub · **P0** · owner: **Agent B2**
- **What:**
  - Add `ingest_forecast(region: str) -> ForecastRecommendation` to `interfaces.py` (NEW SEAM 6) that **lazily imports** `fetch/agents/who_agent/fetcher.run_who_update` (mirror how `get_inventory` lazily imports `redis_inventory`), gated by `BAYMAX_INGEST=1`/availability, **fail-closed to a mock** so `wave2/3` need no `fetch` deps. Return shape = `reasoning:latest` (`{risk_level, priority_items, reasoning, recommended_action}`).
  - Promote/alias `run_who_update` → `run_ingest` (keep the old name as an alias so `ui/app.py` keeps working).
  - Fix `run_who_update`'s raw `r.set` → `redis_io.upsert_forecast_items` (clobber bug, §3).
  - Wire `mock_cdc_feed.json` as a distinct 4th source inside the orchestrator (satisfies "+ CDC stub").
- **Files:** `interfaces.py` (+seam +`ForecastRecommendation` dataclass), `fetch/agents/who_agent/fetcher.py`, `fetch/agents/illness_agent/` (CDC read), `fetch/agents/README.md`.
- **Seam:** NEW `ingest_forecast` seam (additive); no existing signature changes.
- **Harness:** `fetch/scripts/redis_smoke_test.py` (fix the illness schema mismatch first), + the B1 `ingest` self-test path; offline mock path must work with no network.
- **Deps:** A1/A2 (none hard — can start once A is reviewed). **Decouple from B1** via the seam so they build in parallel.
- **Owner:** Agent B2.

---

## Wave C — Parallel (depend on B)

### C1 · Dashboard: crisis prompt + "Ingest Data" + consolidate to one surface · **P0** · owner: **Agent C1**
- **What:**
  - Make **`ui/` (Flask) canonical.** Add a **crisis text input** → `POST /api/crisis` → push onto a new `baymax:crisis` bus key (RPUSH); the FRONT poller in `run_dashboard_demo.py`/`run_front.py` LPOPs it → `start_crisis(source="dashboard")`. Mirror the existing `baymax:trigger` flow exactly.
  - **Relabel** `POST /api/refresh_who` → **"Ingest Data"** in `index.html` (engine unchanged); render `crisis:active` + `reasoning:latest` cards.
  - **Consolidate:** demote/cut the FastAPI `scan_dashboard.py` surface (kill the orphaned :8079 boot when launched from Flask); harvest `dashboard_page.html`'s visual polish (transfer corridor, Baymax voice console, fonts) into `index.html`. Keep `dashboard_bus.py` as the shared seam.
  - **Fix:** SSE replay/disconnect leak (adopt per-connection pub/sub from `scan_dashboard.py`); LPUSH→RPUSH on decision/trigger; remove dead `_parse_negotiation_log`.
- **Files:** `ui/app.py`, `ui/templates/index.html`, `agent-communication-layer/dashboard_bus.py` (+`baymax:crisis` key), `run_dashboard_demo.py` / `run_front.py` (crisis poller), `agent-communication-layer/scan_dashboard.py` (demote/cut). Borrow assets from `dashboard_page.html` + `static/fonts`.
- **Seam:** consumes B1 (`start_crisis`, `run_ingest`) via the Redis bus; no logic duplication.
- **Harness:** extend `wave4_dashboard_e2e_check.py` to push a `baymax:crisis` trigger → assert research → negotiation → CONFIRMED narration over the bus. `wave2/3` unaffected.
- **Deps:** B1 (`start_crisis`/`run_ingest`), A2 (`crisis:active`).
- **Owner:** Agent C1.

### C2 · Arize: wire crisis/ingest/supplier stages via a one-way hook · **P1** · owner: **Agent C2**
- **What:** Add `register_trace_hook(...)` to `baymax_agents.py` (mirror `register_settlement_hook`) so the core never imports `arize`. Register `arize` spans at deploy time (in `run_front.py`/`run_dashboard_demo.py`). Extend `trace_schema.py` with `TRACE_CRISIS_RESEARCH` (crisis_type + ranked at-risk) and `TRACE_SUPPLIER_ORDER` (vendor/url/qty/wallet); reuse `forecast_signal` per ingest source. Add a shared `trace_id` so Phoenix renders one connected crisis→…→outcome trace. Fix `trace_store.TRACES_DIR`, the deprecated `utcnow`, and filename collisions.
- **Files:** `baymax_agents.py` (+hook), `run_front.py`/`run_dashboard_demo.py` (register), `arize/src/{trace_schema,trace_store,trace_reasoning,demo_trace}.py`, new `arize/src/trace_supplier_order.py`.
- **Seam:** NEW one-way `register_trace_hook` (mirrors settlement hook); core stays `arize`-free → offline harnesses unaffected.
- **Harness:** `demo_trace.py` smoke (fix docstring) + a no-Phoenix run asserting spans build; `wave2/3` must still run with no Phoenix.
- **Deps:** B1 (stage call sites exist).
- **Owner:** Agent C2.

---

## Wave D — Serial cleanup + bug-fixes (lead does it; highest blast radius)

### D1 · Security: untrack + rotate the committed key · **P0** 🔴
- `git rm --cached adyan-agent-communication-layer/private_keys.json`; add `private_keys.json` + `dump.rdb` + `*.jpg` + `tracks/` to root `.gitignore`; **rotate** the leaked testnet keys; (optional) `git filter-repo` to scrub history. **File stays on disk** in the sibling dir.

### D2 · Delete dead + contract-violating code · **P0**
- Delete `fetch/shared/protocol.py` (frozen-contract violation, 0 importers). Delete `fetch/approval/{service,state,inventory_seam}.py` + `serve_with_ngrok.sh` (orphaned 2nd gate; fakes settlement) — **keep `imessage_client.py`**. Delete `hello_world_agent.py`, `tailscale_*.py`, `two_agent_payment_spike.py`.
- **Verify after each delete:** `wave2_e2e_check.py` + `wave3_e2e_check.py` + `BAYMAX_SELFTEST` still import & pass (the spec's gate).

### D3 · Purge bloat from git · **P0 (hygiene)**
- Remove `dump.rdb`, `Ch-1.jpg`, `hardware/camera connection/capture_*.jpg` (24) + `.json`, tracked `__pycache__/*.pyc`, `tracks/arize/traces/*.json`, root `__init__.py`. Gitignore the patterns.

### D4 · Consolidate writers + fix correctness bugs · **P1**
- Vision: make `camera_worker`+`vision_count` canonical; reconcile `reserve`/`pct`/`status`/`surplus` across writers; remove the dead `reserve` branch in `redis_inventory.py` (or wire a writer). Fix the `order N` gate quantity bug (§3). Wire `transfers` stream from live settlement. Retire dead `channels:*`/`vector_history`/`alerts.py` or mark demo-only in the contract.

### D5 · Docs/naming · **P1**
- Fix root `README.md` + stale docstrings `stockpile_*`→`baymax_*` (or execute the rename if the lead chooses §7.4). Reconcile the divergent `docs/superpowers/` copies. Update CLAUDE.md's false "camera writes `reserve`" claim.

---

## P2 (cut if behind)
- Redis vector search / `history:usage` historical analogs (orphaned today; FR13 — Redis-prize only).
- Sensor fusion (Arduino ground-truth, FR12).
- Generalize `count_shelf` per arbitrary at-risk item (today saline-specific) — only if a non-saline item must be camera-backed.

---

## Build order & verification gates

```
A1 ─┬─> B1 ─┬─> C1 ──┐
A2 ─┘       │        ├─> D1..D5 (lead, serial)
            └─> C2 ──┘
        B2 ─┘
```
- **Gate after A:** `check_research_crisis.py` green; `wave2`+`wave3`+`BAYMAX_SELFTEST` still green (seam added, nothing altered).
- **Gate after B:** `wave5_crisis_e2e_check.py` + crisis `BAYMAX_SELFTEST` green; `wave2`+`wave3` green; ingest mock path green.
- **Gate after C:** `wave4` (extended) green; both surfaces narrate a crisis run.
- **Gate after D:** all of `wave2`+`wave3`+`wave4`+`BAYMAX_SELFTEST` green post-deletion; no secrets tracked (`git ls-files | grep -i private_keys` empty).
- **Rule:** never advance a wave on a red harness. Record pass/fail in `.swarm/STATE.md` after each wave.

## Definition of done → task mapping
- [ ] Dashboard crisis prompt → research → vision → negotiate/order, narrated → **A1, B1, C1**
- [ ] Same in ASI:One with FET payment card → **A1, B1** (settlement already done)
- [ ] "Ingest Data" on dashboard + "ingest data" in chat → **B1, B2, C1**
- [ ] `wave2` + `wave3` e2e pass → **gates after A/B/C/D**
- [ ] One clean layout, runbook, no secrets committed → **D1–D5**, then `DEMO_RUNBOOK.md` (Phase 3)
