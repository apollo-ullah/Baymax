# .swarm/STATE.md — Stockpile/Baymax realignment coordination

**Lead orchestrator:** Claude (Opus 4.8, ultracode). **Branch:** agent-communication-layer.
**Last updated:** Phase 0 launch.

## North-star demo story (the bar every audit measures against)
Two surfaces, ONE engine (dashboard + ASI:One). Target end-to-end flow:
1. User states a **crisis** in natural language ("wildfires near Hospital A") on dashboard OR ASI:One.
2. **Research agent (Claude)** infers crisis type + ranked at-risk supplies + rationale.
3. **Vision/Redis inventory** reveals a shortfall for a selected at-risk item.
4. **Agents negotiate** an inter-facility reallocation (split transfer + re-plan on partial offers).
5. If no internal surplus → **Browserbase external supplier order** fallback.
6. **Settlement** as a real testnet FET payment (payment card in ASI:One).
7. **"Ingest Data" loop**: button/chat command runs all forecast agents (WHO + weather + illness + CDC) → Redis → Claude reasoning → proactive reallocation/purchase recommendations.

## FROZEN constraints (never violate; propagated to every subagent)
- `agent-communication-layer/protocol.py` is the frozen wire contract. Never redefine Fetch protocol models locally or edit it.
- Testnet only (`FETCH_NETWORK=testnet`).
- Payment Protocol seller role; `RequestPayment.metadata` MUST include `provider_agent_wallet` + `fet_network`.
- `import agent_base` before constructing any uAgent.
- venv + secrets live in sibling `adyan-agent-communication-layer/`; never commit secrets; `private_keys.json` stays out of git.
- Seam signatures in `interfaces.py` are the contract — extend via NEW seams, never bypass.
- Harnesses ARE the test suite: `wave2_e2e_check.py`, `wave3_order_e2e_check.py`, `BAYMAX_SELFTEST=1 python front_agent.py`.

## RESOLVED ISSUE
- The "FULL SPEC" placeholder was filled in by the user mid-Phase-0. It CONFIRMS the working spec on every material point (incl. "adyan-... is secrets/venv only, do not delete"). No conflicts to reconcile. Success gate = `wave2` + `wave3` (not wave4).

## Phase status
| Phase | Status |
| :-- | :-- |
| 0 — Audit swarm (8 read-only agents) | ✅ DONE (8/8 reports landed, 611k tokens) |
| 1 — Realignment plan | ✅ DONE (SCOPE_AUDIT.md + REALIGNMENT_PLAN.md written) |
| GATE — present both docs | ⏸️ AWAITING USER REVIEW |
| 2 — Implement (Wave A→B→C→D DAG) | 🔒 BLOCKED on approval |
| 3 — Runbook | pending |

## Key synthesis findings (see SCOPE_AUDIT.md)
- Realignment is NARROW: ingest orchestrator (`who_agent/fetcher.run_who_update`), "Ingest Data" button (`/api/refresh_who`), research-output shape (`reasoning:latest`), and dual-surface engine (`start_negotiation(reply_to/source)`) ALL already exist.
- True P0 gap = research_crisis seam (A1) + crisis/ingest chat kinds (B1) + one dashboard w/ crisis prompt (C1) + forecast→negotiate wire.
- 🔴 SECURITY: `adyan-agent-communication-layer/private_keys.json` is COMMITTED (real testnet keys) → D1 untrack+rotate.
- 🔴 CONTRACT: `fetch/shared/protocol.py` redefines Payment models (0 importers) → D2 delete.

## Audit agents
| Track | Target | Report | Status |
| :-- | :-- | :-- | :-- |
| agents-mesh | agent-communication-layer/ | .swarm/audit/agents-mesh.md | dispatched |
| redis-bus | redis/ | .swarm/audit/redis-bus.md | dispatched |
| vision | hardware/camera connection/ | .swarm/audit/vision.md | dispatched |
| forecast | fetch/agents/ | .swarm/audit/forecast.md | dispatched |
| dashboard | ui/ + agent-communication-layer dashboard_* | .swarm/audit/dashboard.md | dispatched |
| observability | arize/, tracks/arize/ | .swarm/audit/observability.md | dispatched |
| approval | fetch/approval/ | .swarm/audit/approval.md | dispatched |
| duplication-detective | sibling/stale-dir comparison | .swarm/audit/duplication-detective.md | dispatched |

## Harness baseline (pre-change, GREEN)
- `wave2_e2e_check.py` ✅ (WAVE2 E2E SUCCESS)
- `wave3_order_e2e_check.py` ✅ (WAVE3 ORDER E2E SUCCESS)
- `BAYMAX_SELFTEST=1 BAYMAX_OFFER_TIMEOUT=3 python front_agent.py` ✅ (CONFIRMED + clean exit)
- venv interpreter: `agent-communication-layer/ → ../adyan-agent-communication-layer/.venv/bin/python` (Python 3.14.6)
- NOTE: selftest needs `BAYMAX_OFFER_TIMEOUT=3` or it waits on the default offer window; always pass it in the gate.

## Implementation progress
- ✅ D1 (security): `private_keys.json` untracked (`git rm --cached`), preserved on disk, gitignored. (Leaked keys = `baymax_hello` testnet only; history-scrub flagged to user.)
- ✅ Wave A DONE + GATE GREEN:
  - A1: SEAM 5 `research_crisis` + SEAM 6 `ingest_forecast` + dataclasses in `interfaces.py`; new `claude_research.py`, `ingest_orchestrator.py`, `check_research_crisis.py`.
  - A2: `crisis:active` + `REASONING_KEY` + `NARRATION_CHANNEL` in `redis/src/schema.py`; new `redis/src/crisis.py`; `redis_inventory.write_crisis_active`/`get_crisis_active`; `redis_contract.md` documented.
  - Gate: check ✅ | wave2 ✅ | wave3 ✅ | selftest ✅ (CONFIRMED+split, 0 tracebacks).
- ✅ Wave B DONE + GATE GREEN:
  - B1 (lead): `front_agent.py` — `_CRISIS_CUE_RE`/`_INGEST_CUE_RE`, `crisis`+`ingest` kinds in parse_intent, on_intent branches, echo-guard extended. `baymax_agents.py` — `_emit`, `_select_crisis_item`, `start_crisis`, `run_ingest`. New `wave5_crisis_e2e_check.py`.
  - B2 (subagent, in-scope ✅): `who_agent/fetcher.py` clobber fix (upsert) + CDC stub wired + `run_ingest` alias; `redis_smoke_test.py` illness-schema fix; `fetch/agents/README.md`. `run_who_update` return contract preserved.
  - Gate: wave5 ✅ | check ✅ | wave2 ✅ | wave3 ✅ | selftest ✅ (150B+50C, full 200, 0 tracebacks) | parse_intent kinds ✅ | echo-guard ✅ | ingest path ✅.
- ✅ Wave C DONE + GATE GREEN:
  - C1 backend (lead): `dashboard_bus.py` `baymax:crisis` + push/pop_crisis; `_poll_dashboard_crisis` in `run_front.py` + `run_dashboard_demo.py`. New `wave6_crisis_dashboard_e2e_check.py`.
  - C1 frontend (subagent, in-scope ✅): `ui/app.py` `POST /api/crisis` (RPUSH baymax:crisis + SET crisis:active) + `/api/ingest` alias + crisis in `/api/state`; LPUSH→RPUSH bus fix. `index.html` crisis panel + crisis card + "Ingest Data" relabel.
  - C2 (Arize, P1): ✅ DONE (added after P0 commit, on request) — one-way `register_trace_hook` in baymax_agents (core never imports arize); `arize_hook.py` emitter (JSON always + OTel/Phoenix when avail, fail-open, opt-in `BAYMAX_ARIZE=1`); 7 `_trace` sites (crisis_research/inventory_low/reasoning_decision/transfer_recommendation/supplier_order/forecast_signal/decision_outcome); trace_schema + trace_store fixes; new `check_arize_trace.py`. Gate: C2 check ✅ + full regression ✅ (trace no-op when unregistered); arize/traces gitignored.
  - Gate: wave6 ✅ | wave5 ✅ | wave2 ✅ | wave3 ✅ | selftest ✅ | ui/app.py compiles ✅.
- ✅ Wave D DONE + FINAL GATE GREEN:
  - D1 ✅ (secret untracked+gitignored, done first).
  - D2 ✅ deleted: `fetch/shared/protocol.py` (frozen-contract violation, 0 importers), `fetch/approval/{service,state,inventory_seam}.py`+`serve_with_ngrok.sh` (orphaned 2nd gate, faked settlement), `hello_world_agent.py`. KEPT `imessage_client.py` (ui/ imports it).
  - D3 ✅ untracked bloat (kept on disk, gitignored): dump.rdb, Ch-1.jpg, 24 capture_*.jpg + 2 .json, tracked .pyc, tracks/arize/traces/*.json.
  - D4 ✅ transfers stream wired from live settlement (`write_transfer_record` fail-soft + `_log_confirmed_transfers` at both CONFIRMED paths).
  - D5 ✅ docs: README stockpile_*→baymax_* + crisis/ingest note; run_front banner "order N"→"order"; **bug fix**: `ui/app.py` AGENT_VENV_PYTHON now resolves the SIBLING venv (was pointing at a non-existent `agent-communication-layer/.venv` → would have broken Demo 1 backend spawn).
  - EVIDENCE-BASED DEVIATIONS from the plan's D2: KEPT `tailscale_hosts.py`/`tailscale_smoke_test.py` (camera_worker + scan_dashboard import tailscale_hosts) and `two_agent_payment_spike.py` (documented debug aid, 0 importers, harmless). Vision-writer consolidation + reserve-branch = P1, deferred.
  - FINAL GATE: compile ✅ | check ✅ | wave2 ✅ | wave3 ✅ | wave5 ✅ | wave6 ✅ | selftest ✅ (150B+50C, full 200, 0 tracebacks).
- ✅ Phase 3: DEMO_RUNBOOK.md written (Demo 1 dashboard, Demo 2 ASI:One, ingest walkthrough, offline suite, env knobs, follow-ups).

## DONE — all approved phases complete, all gates green. Nothing committed (CLAUDE.md: commit only when asked).
Definition of Done: dashboard crisis→research→negotiate/order narrated ✅ | same in ASI:One + FET card ✅ | Ingest Data on both surfaces ✅ | wave2+wave3 pass ✅ | one layout + runbook + no secrets committed ✅. C2 (Arize, P1) deferred per "P0 only".

## Note on selftest split ordering
Leg accept order is async-nondeterministic (B-then-C or C-then-B). Gate check must be ORDER-INSENSITIVE: assert both "150 IV fluids from Hospital B" AND "50 IV fluids from Hospital C" AND "full need of 200 IV fluids met" — never a fixed concatenation.

## Known quirks (not blockers)
- BAYMAX_SELFTEST self-exit (`os._exit`) is racy in the 4-agent Bureau (reaches CONFIRMED reliably; may not print "shutting down"). Pre-existing; does NOT affect the long-running demo runners. Gate signal = "CONFIRMED + canonical split, no Traceback". Always run selftest with `BAYMAX_OFFER_TIMEOUT=3`.

## Gate command (run after each wave, from agent-communication-layer/)
PY=../adyan-agent-communication-layer/.venv/bin/python
`$PY check_research_crisis.py` ; `$PY wave2_e2e_check.py` ; `$PY wave3_order_e2e_check.py` ; `BAYMAX_SELFTEST=1 BAYMAX_OFFER_TIMEOUT=3 $PY front_agent.py` (grep CONFIRMED)

## Locked files (write-gated during Phase 2)
- B1 (lead): `front_agent.py`, `baymax_agents.py`, new `wave5_crisis_e2e_check.py`.
- B2 (subagent): `fetch/agents/who_agent/fetcher.py`, `fetch/agents/illness_agent/*`, `fetch/agents/README.md`, `fetch/scripts/redis_smoke_test.py`. MUST NOT touch interfaces.py / ingest_orchestrator.py / baymax_agents.py / front_agent.py / protocol.py / fetch/shared/protocol.py.
