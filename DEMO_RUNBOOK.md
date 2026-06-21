# DEMO_RUNBOOK.md — Stockpile/Baymax

Exact commands for the two demos + the "Ingest Data" walkthrough, plus the offline
verification suite. **Two surfaces, one engine.**

> **Interpreter.** The venv + secrets live in the sibling dir. Everywhere below:
> `PY=adyan-agent-communication-layer/.venv/bin/python` (run from the repo root), or
> from inside `agent-communication-layer/` use `../adyan-agent-communication-layer/.venv/bin/python`.
> **Naming:** product = *Stockpile*; code/env = `baymax_*` / `BAYMAX_*` (live truth).

> **Live Claude / settlement secrets.** The offline mocks need no keys. For LIVE
> Claude research/ranking/ingest, on-chain FET, or Browserbase, the secrets in
> `adyan-agent-communication-layer/.env` must be on the environment. Quick way:
> `set -a && source adyan-agent-communication-layer/.env && set +a` in the shell
> before launching, and for Demo 2 also `ln -s ../adyan-agent-communication-layer/.env agent-communication-layer/.env`.

---

## Prereqs (both demos, for live inventory) — Redis up + seeded

```bash
# from repo root
docker compose -f redis/docker-compose.redis.yml up -d            # redis-stack on :6379
(cd redis/src && REDIS_URL=redis://localhost:6379 \
   ../../adyan-agent-communication-layer/.venv/bin/python seed_demo_data.py)
```

This seeds hospital inventory/surplus/meta + forecast. The seeded `hospital_a` runs
short on IV Fluids; B can spare 150, C 80 → the canonical 150+50 split. (Skip Redis
entirely to run the **offline** path — see the verification section; the agents
fail-closed to deterministic mocks.)

---

## Demo 1 — Dashboard (table judges): crisis prompt → research → negotiate, live

```bash
# from repo root — pick up live secrets first if you want live Claude:
set -a && source adyan-agent-communication-layer/.env 2>/dev/null; set +a
adyan-agent-communication-layer/.venv/bin/python ui/app.py
# Flask dashboard on http://localhost:5001
# It auto-spawns the agent Bureau (run_dashboard_demo.py: Hospital A+B+C, BAYMAX_REDIS=1,
# with the crisis / trigger / decision pollers). Bureau log: /tmp/baymax_bureau.log
```

In the browser at **http://localhost:5001**:
1. In the **Crisis** box type: `wildfires near Hospital A` → **Run Crisis Response**.
2. Watch the live feed: `researching → researched (crisis type: wildfire; at-risk: saline…) → shortfall → offers → AWAITING_APPROVAL`.
3. Click **Approve** (or **Order externally**) on the gate → `settling → confirmed`. The transfer lands in the transfers panel.
4. (Or click **Ingest Data** → WHO+weather+illness+CDC → Claude → the reasoning card updates with a proactive recommendation.)

**Optional live vision** (otherwise inventory = seeded Redis): in two more terminals,
```bash
cd agent-communication-layer
../adyan-agent-communication-layer/.venv/bin/python camera_worker.py --hospital a
../adyan-agent-communication-layer/.venv/bin/python camera_worker.py --hospital b --port 8766
```

**Live Claude crisis research** (default is the deterministic mock): set
`BAYMAX_CLAUDE_RESEARCH=1` (+ `ANTHROPIC_API_KEY`) and `BAYMAX_INGEST=1` in the
environment before launching `ui/app.py`.

---

## Demo 2 — ASI:One (Fetch judges): crisis chat → negotiate → FET payment card

```bash
cd agent-communication-layer
ln -s ../adyan-agent-communication-layer/.env .env          # one-time: secrets co-located
PY=../adyan-agent-communication-layer/.venv/bin/python
# three terminals (order doesn't matter):
$PY run_hospital_b.py
$PY run_hospital_c.py
$PY run_front.py            # Hospital A — chat + Payment Protocol (seller), BAYMAX_REDIS=1
```

One-time per agent: open the **Agent Inspector URL** each prints, and in Agentverse do
**Connect → Mailbox → Finish**. Then on **https://asi1.ai** find the FRONT agent and chat:

- `wildfires near Hospital A` → research → at-risk item → negotiation milestones stream back.
- On **AWAITING_APPROVAL** reply `approve` (inter-facility trade), `order` (external supplier, full shortfall), or `reject`.
- On **CONFIRMED / ordering** a **RequestPayment** appears — approve the **TestFET** payment card (open the wallet in ASI:One if no prompt shows). On-chain settle on Dorado testnet.
- Also works: `ingest data` (forecast loop + recommendation), `order 500 saline` (proactive purchase), `Hospital A is short on IV fluids` (classic direct request).

---

## "Ingest Data" walkthrough (both surfaces, same engine)

- **Dashboard:** click **Ingest Data** → `POST /api/refresh_who` → `who_agent.run_who_update`
  (WHO disease.sh + Open-Meteo + illness mock + CDC stub) → `forecast:{region}` + `reasoning:latest` → the Claude reasoning card updates.
- **ASI:One / chat:** type `ingest data` → `run_ingest` → `interfaces.ingest_forecast`
  (live `BAYMAX_INGEST=1`, else mock) → the proactive recommendation is narrated back.

---

## Offline verification (the test suite — no Redis / key / network)

```bash
cd agent-communication-layer
PY=../adyan-agent-communication-layer/.venv/bin/python
$PY check_research_crisis.py            # crisis + ingest seams (mock)
$PY wave2_e2e_check.py                  # chat → negotiate → settle → pay
$PY wave3_order_e2e_check.py            # admin gate → external order → settle
$PY wave5_crisis_e2e_check.py           # crisis chat → research → negotiate → CONFIRMED
$PY wave6_crisis_dashboard_e2e_check.py # dashboard crisis bus → research → gate → settle
BAYMAX_SELFTEST=1 BAYMAX_OFFER_TIMEOUT=3 $PY front_agent.py   # in-process chat→negotiate→narrate
```

Expected: `WAVE2/WAVE3/WAVE5 … SUCCESS`, `WAVE6 … RESULT: PASS`, `ALL CHECKS PASSED`,
and the selftest reaches **CONFIRMED** (150 from B + 50 from C). All run with the
deterministic mocks — this is the demo's "runs clean three times" backup.

> **Note:** the selftest's `os._exit` is racy in the 4-agent Bureau (reaches CONFIRMED
> reliably; may not always print "shutting down"). Gate on the CONFIRMED line, and pass
> `BAYMAX_OFFER_TIMEOUT=3` so it doesn't wait on the default offer window.

---

## Key env knobs

| Var | Effect |
| :-- | :-- |
| `BAYMAX_REDIS=1` | live Redis inventory (else mock). Set by `run_front`/`run_dashboard_demo`. |
| `BAYMAX_CLAUDE_RESEARCH=1` | live Claude crisis research (else deterministic mock). Needs `ANTHROPIC_API_KEY`. |
| `BAYMAX_INGEST=1` | live forecast ingest via the WHO fetcher (else mock recommendation). |
| `BAYMAX_CLAUDE_RANKING=1` | Claude offer ranking (else nearest-first greedy). |
| `BAYMAX_BROWSERBASE=1` | live external supplier order (else deterministic mock). |
| `BAYMAX_ARIZE=1` | register the Arize/Phoenix trace emitter in the runners (else no tracing). Pair with `PHOENIX_COLLECTOR_ENDPOINT` to export spans; JSON trace artifacts always land in `arize/traces/` (gitignored). |
| `UI_PORT` (default 5001) | dashboard Flask port. `BAYMAX_AGENT_PYTHON` overrides the Bureau interpreter. |

### Observability (Arize / Phoenix)
Tracing is wired through a one-way hook (`baymax_agents.register_trace_hook`) so the
core never imports `arize`. Turn it on by launching a runner with `BAYMAX_ARIZE=1`
(optionally `PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006` for a live Phoenix).
Each negotiation then emits the chain — `crisis_research → inventory_low →
reasoning_decision → transfer_recommendation`/`supplier_order → decision_outcome` —
with a shared `req_id` attribute per lifecycle. Verify offline:
`agent-communication-layer$ ../adyan-agent-communication-layer/.venv/bin/python check_arize_trace.py`.

---

## Known follow-ups (P1 / deferred, not blocking the demos)
- **Arize tracing (C2): DONE** — one-way `register_trace_hook` emits the full crisis→outcome chain (opt-in via `BAYMAX_ARIZE=1`). Future polish: true parent/child span nesting (currently correlated by a shared `req_id` attribute).
- **Dashboard consolidation:** `ui/` (Flask) is canonical and carries the crisis prompt; the FastAPI `scan_dashboard.py` still boots on :8079 when spawned (orphaned, harmless). Fold its camera/corridor polish into `ui/` and retire the duplicate.
- **Vision writers:** reconcile the camera→Redis writers + `reserve`/`status` schema (4 writers, 3 status vocabularies).
- **Secret history:** the leaked `baymax_hello` testnet keys were untracked + gitignored; a `git filter-repo` history scrub + force-push is optional (testnet-only, dead agent).
