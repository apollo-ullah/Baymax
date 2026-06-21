# duplication-detective audit

Scope: whole-repo READ-ONLY survey of the canonical layout, what is safe to
consolidate/delete, and rebrand drift. Evidence below is from `git ls-files`,
`git log`, `diff`, `du`, and importer greps. Live truth = `baymax_*` /
`BAYMAX_*` (per the swarm brief); the root README's `stockpile_*` naming is
stale.

## Aligned with demo story

The canonical monorepo layout is clean and maps to the north-star tracks. There
is **one** authoritative engine dir and the per-track dirs are legitimately
separate, not duplicated:

- **`agent-communication-layer/`** (54 tracked files) — the canonical agent
  engine. `protocol.py` here is the FROZEN contract and is imported by all 7
  live modules (`agent_base.py`, `baymax_agents.py`, `settlement.py`,
  `two_agent_payment_spike.py`, `wave2/3/4_*_check.py`). This is the keep-it dir.
- **`redis/`** (20 files) — state bus + `seed_demo_data.py`; referenced by
  CLAUDE.md bring-up. Real track, keep.
- **`fetch/agents/`** (WHO/weather/illness/CDC-stub forecast agents) — these are
  exactly the "INGEST DATA" forecast agents the demo story wants wired to the
  dashboard. `fetch/agents/who_agent/fetcher.py::run_who_update` is already
  imported by `ui/app.py:458` (the `/api/refresh_who` route) — a real
  cross-track seam aligned with step 7.
- **`fetch/approval/`** — iMessage/ngrok human-approval pipeline; live, imports
  `fetch/shared/redis_io` (the *only* live consumer of `fetch/shared/`).
- **`hardware/camera connection/sync_to_redis.py`** — the vision→Redis chain
  (step 3). Real.
- **`arize/`** (16 files) — observability track; `arize/src/*.py` is real code.

These dirs share names but hold distinct, imported code — **this is correct
monorepo structure, not duplication.** The multiple `requirements.txt` (5) and
`README.md`/`HANDOFF.md` (11) are per-track and legitimate; do **not**
consolidate them.

## Redundant / dead / off-track

Ranked by confidence (all verified — importer greps run before any "orphan"
call):

1. **`fetch/shared/protocol.py` — DEAD + CONTRACT-VIOLATING duplicate.** Zero
   importers anywhere in the repo (grep for `shared.protocol` / `from
   fetch.shared.protocol` / `import protocol` returns nothing pointing here; the
   only thing imported out of `fetch/shared/` is `redis_io`). It is a *second,
   incompatible* copy of the negotiation + payment models: it hand-defines
   `RequestPayment`/`CommitPayment`/`CompletePayment`/`CancelPayment`/
   `RejectPayment` as plain `uagents.Model` subclasses (lines ~64-86) instead of
   re-exporting them from `uagents_core.contrib.protocols.payment`. That is
   exactly the FROZEN-contract violation the brief forbids — a local copy is a
   different schema digest and would not match ASI:One. Its `SupplyRequest` etc.
   also use different field names (`from_`, `quantity_needed`,
   `quantity_available`/`distance`/`eta`) than the frozen
   `agent-communication-layer/protocol.py` (`from_facility`, `quantity_needed`,
   `quantity_available`/`distance_km`/`eta_minutes`). Pure liability.
2. **`tracks/arize/traces/*.json` (12 files) — generated output.**
   `arize/src/trace_store.py:3` docstring: "Saves trace events as
   pretty-printed JSON files under tracks/arize/traces/." `save_trace()` writes
   timestamped `decision_chain_*`, `forecast_signal_*`, etc. — exactly the
   committed filenames (`*_20260621T030633.json`). Regenerable; should be
   gitignored, not committed. (Minor wrinkle: the live code path now resolves to
   `arize/traces/`, so even the directory is stale.)
3. **`dump.rdb` at repo root — 760 KB committed Redis snapshot.** No code or
   compose file references it (grep `dump.rdb` = empty). Pure bloat; Redis
   regenerates it. Off-track.
4. **`hardware/camera connection/capture_*.jpg` (24 files, ~10 MB) + `Ch-1.jpg`
   at root (936 KB).** Transient debug frames written by
   `sync_to_redis.py:70` (`cv2.imwrite(... capture_{timestamp}.jpg)`). No code
   reads them back (grep for `capture_178` / `Ch-1` in `*.py` = empty; the
   `--image`/`--from-json` flags take a user-supplied path, not these). ~11 MB of
   demo-snapshot bloat.
5. **`hardware/camera connection/__pycache__/bottle_counter.cpython-311.pyc` —
   tracked compiled artifact.** Committed before `*.pyc` was ignored, so it
   lingers in the index. Dead.
6. **Root `__init__.py` (0 bytes) — orphan.** Makes the repo root a Python
   package, but nothing imports `CalHax.*` (grep empty). Harmless but pointless.
7. **`hello_world_agent.py` — Phase-0 proof, off the demo path.** Standalone
   ASI:One hello agent; no importers (only doc mentions). Not dead code per se
   (it's a manual sanity tool), but irrelevant to the north-star flow. Note: its
   `name="baymax_hello"` matches the leaked key below.

## Buggy / untested / risky

- **SECRET LEAK — `adyan-agent-communication-layer/private_keys.json` is
  COMMITTED.** It is the single tracked file in the otherwise
  secrets/venv-only sibling dir, added in the most recent commit `a0d55e4
  "starting demo mode"`. It contains a real `identity_key`
  (`ac84376b...52970d`) and `wallet_key`
  (`gXwopgOGDQOZ2C5goSbaiwr3hoKm3+z4HvEqWb978hc=`) for `baymax_hello`. Root
  `.gitignore` lists `.venv/ .env __pycache__/ *.pyc` but **NOT
  `private_keys.json`** — only `agent-communication-layer/.gitignore` covers it,
  and that gitignore does not govern the sibling path. This is the exact failure
  mode CLAUDE.md warns about ("if you ever copy/symlink that file into the
  tracked dir, add it to `.gitignore` first"). Must be removed from the index +
  history and the keys rotated. (Testnet-only keys, so blast radius is limited,
  but still a leak.)
- **Root README is broken / out of sync.** It documents
  `STOCKPILE_EXIT_WHEN_DONE=1 python stockpile_agents.py` and `STOCKPILE_ITEM`
  (README.md:112-117), but **no `stockpile_agents.py` exists** and **no code
  reads any `STOCKPILE_*` env var** (the live entrypoint is `baymax_agents.py`
  reading `BAYMAX_*`). The only `STOCKPILE_` strings in code are in two
  docstrings (`ui/app.py:472`) — also non-functional. Anyone following the root
  README cannot run the demo.
- **`fetch/shared/protocol.py` (see above) is a standing trap:** if any future
  fetch-side code imports it instead of the frozen contract, settlement silently
  breaks at ASI:One ingestion (schema-digest mismatch) with no local test
  catching it.
- **Sibling-dir fragility is real but intentional.** `.venv/` (bin/include/lib/
  pyvenv.cfg present) and `.env` exist on disk in
  `adyan-agent-communication-layer/` and are correctly gitignored. This dir
  **must be preserved** — "delete the duplicate" is the WRONG action for it (it
  is not a code fork; it is the live venv + secrets store). Only the
  *committed* `private_keys.json` inside it should be purged from git.

## File inventory (path -> 1-line purpose -> keep/cut/refactor)

| Path | Purpose | Verdict |
| :-- | :-- | :-- |
| `adyan-agent-communication-layer/` (dir) | sibling secrets + venv store (`.env`, `.venv`, `__pycache__`) — NOT a code fork | **MUST KEEP** (preserve; untracked parts) |
| `adyan-agent-communication-layer/private_keys.json` | committed real testnet identity/wallet keys | **CUT from git + rotate keys** |
| `agent-communication-layer/` (dir) | canonical agent engine; `protocol.py` is FROZEN, imported by 7 modules | **KEEP** (canonical) |
| `fetch/shared/protocol.py` | orphan 2nd copy of negotiation+payment models; 0 importers; violates frozen contract | **CUT** |
| `fetch/shared/redis_io.py` | live Redis helper; imported by fetch agents/approval/scripts | **KEEP** |
| `tracks/arize/traces/*.json` (12) | generated trace output (`arize/src/trace_store.py`); regenerable | **CUT + gitignore** |
| `dump.rdb` (root, 760 KB) | committed Redis snapshot; no references | **CUT** |
| `Ch-1.jpg` (root, 936 KB) | stray demo image; no references | **CUT** |
| `hardware/camera connection/capture_*.jpg` (24, ~10 MB) | transient debug frames from `sync_to_redis.py`; never read back | **CUT + gitignore `capture_*.jpg`** |
| `hardware/camera connection/__pycache__/*.pyc` | tracked compiled artifact | **CUT** |
| `__init__.py` (root, 0 bytes) | makes repo root a package; nothing imports `CalHax.*` | **CUT** (low priority) |
| `hello_world_agent.py` | Phase-0 ASI:One proof; off demo path; no importers | KEEP (harmless tool) / optionally archive |
| `docs/superpowers/**` vs `agent-communication-layer/docs/superpowers/**` | same 4 filenames, **divergent content** (diff = DIFFERENT) | **REFACTOR** — pick one home, reconcile |
| `README.md` (root) | uses dead `stockpile_*` / `STOCKPILE_*` names; broken commands | **REFACTOR** — align to `baymax_*`/`BAYMAX_*` or rebrand both ways |
| `redis/`, `arize/src/`, `fetch/agents/`, `fetch/approval/`, `hardware/`, `ui/` | per-track real code/seams | **KEEP** |
| `*/requirements.txt` (5), `*/README.md`+`HANDOFF.md` (11) | per-track manifests/docs | **KEEP** (not duplication) |
| `.gitignore` (root) | missing `private_keys.json`, `dump.rdb`, `*.jpg`, `tracks/` | **REFACTOR** (add the leaked/bloat patterns) |

## Dependencies on other tracks

- **`ui/app.py` → `fetch/agents/who_agent/fetcher.run_who_update`** (line 458)
  and **→ `fetch/approval/imessage_client.notify`** (line 129). The dashboard
  already bridges into the fetch/ track — relevant to demo step 7 (ingest loop).
- **`fetch/approval/{service,state,inventory_seam}.py` →
  `fetch/shared/redis_io`** (3 importers) and **`fetch/agents/{illness,weather}/
  agent.py` → `...shared.redis_io`**. So `fetch/shared/redis_io.py` is load-
  bearing; only `fetch/shared/protocol.py` is the dead one.
- **`agent-communication-layer/redis_inventory.py` and
  `hardware/.../sync_to_redis.py`** both read the same Redis the `redis/` track
  seeds — the vision→Redis→agents chain spans three tracks.
- **`arize/src/trace_store.py`** writes the `tracks/arize/` output — the only
  producer of that generated dir.
- Naming: live code is `baymax_*`/`BAYMAX_*` across
  `agent-communication-layer/`; `stockpile`/`STOCKPILE_` survives only in root
  `README.md` (10 hits), `fetch/approval/*` (prose), and `ui/app.py` (docstrings).
  No `STOCKPILE_*` env var is read at runtime — pure doc drift.

## Open questions for the lead

1. **Purge + rotate now?** `adyan-agent-communication-layer/private_keys.json`
   was committed in `a0d55e4`. Confirm these are throwaway testnet keys; if not,
   rotate immediately and scrub from history (`git filter-repo`).
2. **`fetch/shared/protocol.py`: delete or repoint?** It is dead today. Was it
   meant to become the fetch-side contract import? If so it must `from protocol
   import ...` (the frozen one) or re-export `uagents_core` models — never
   hand-define payment models. Recommend delete.
3. **Which `docs/superpowers/` is canonical** — root or
   `agent-communication-layer/`? They have diverged; one should win.
4. **Rebrand direction:** rename code `baymax_*`→`stockpile_*` (large blast
   radius — `BAYMAX_*` env vars, agent seeds, harness names), or fix the README
   back to `baymax_*`? Cheapest correct fix today is the README.
5. **`tracks/` vs `arize/traces/` path mismatch** — the committed traces live at
   `tracks/arize/traces/` but `trace_store.py` now writes `arize/traces/`. Stop
   committing either and gitignore both.
6. **Keep `hello_world_agent.py`** as a sanity tool or archive it to reduce
   surface area? (It owns the leaked `baymax_hello` key name.)
