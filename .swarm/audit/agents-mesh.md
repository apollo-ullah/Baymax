# agents-mesh audit

Track: the Fetch uAgent negotiation + settlement core in `agent-communication-layer/`.
Measured against the north-star: crisis prompt -> research agent -> at-risk item -> shortfall -> negotiate split -> external order fallback -> FET settlement -> ingest loop, served identically on a dashboard AND ASI:One chat.

## Aligned with demo story

The negotiation engine itself is mature and is the strongest asset in the whole repo. It already covers north-star steps 3-6 end to end, in one shared engine, with two surfaces (chat + dashboard) genuinely wired.

- **The full PRD §10 chain works** (`baymax_agents.py`): `shortfall_detected -> requesting -> collecting_offers -> evaluating -> AWAITING_APPROVAL -> proposing -> settling -> confirmed`, with `re_planning` on leg rejection (`_replan_rejected_leg`, bounded by `MAX_REPLAN_ATTEMPTS`) and partial-cover settlement. The canonical 150+50 split is produced by the mock greedy ranker. Idempotence guards (`evaluated`, `settled`, `decided`, per-leg `pending`) and an offer-timeout watchdog (`offer_timeout` on_interval) make it robust to dropped/duplicate messages — this is north-star step 4, done.
- **One engine, two surfaces (north-star "same engine serves dashboard + chat").** `start_negotiation(..., reply_to, source)` is the single entry. Chat sets `reply_to=<sender>` (narration streams to ASI:One); dashboard sets `source="dashboard", reply_to=None` (narration goes to the Redis `dashboard_bus` SSE feed via `register_narration_sink`). The approval gate surfaces on both: chat replies route through `parse_decision`/`resume_after_admin_decision`; dashboard buttons RPUSH `baymax:decision` and `run_front._poll_dashboard_decision` resumes the same function. `wave4_dashboard_e2e_check.py` proves the dashboard path offline. **This is the realignment's biggest head start** — the seam architecture a research/ingest layer needs already exists.
- **Wave 3 admin gate + external order (north-star step 5)** is real: `_request_admin_decision` halts at `AWAITING_APPROVAL`; `approve` -> `_begin_trade`, `order` -> `_order_path` (calls `order_from_supplier` seam off the event loop via `asyncio.to_thread`, then settles), `reject` -> cancel. A proactive `order N <item>` chat intent bypasses the shortfall guard via `start_order`.
- **Real testnet FET settlement (north-star step 6)** is implemented to the documented contract in `settlement.py`: seller role, bounded on-chain verification with `VERIFIED/NOT_FOUND/INCONCLUSIVE/SKIPPED` distinction, `RequestPayment.metadata` carries `provider_agent_wallet` + `fet_network` (the ASI:One card requirement), wallet resolved via `register_recipient_wallet`/`resolve_recipient_wallet` (the `ctx.agent.wallet` gotcha). One-way hook wiring (`register_settlement_hook`) keeps the core free of the payment layer.
- **Live seams already swappable behind frozen signatures.** `interfaces.py` has `get_inventory` (Redis via `redis_inventory.py`, north-star step 3 inventory), `rank_offers` (Claude via `claude_ranking.py`), `order_from_supplier` (Browserbase via `supplier_order.py`), `approve_release` — all opt-in by env, all fail-closed to deterministic mocks. This is exactly the extension pattern the realignment wants (add a NEW seam, never alter signatures).
- **Intent parsing is hardened** (`front_agent.parse_intent`) against the ASI:One echo loop (milestone/meta regexes, `_looks_like_echo_chatter`, per-sender cooldown), and the optional `claude_intent.py` LLM parser drops in behind `_resolve_parser()` with the same `str -> dict` signature, fail-closed.

## Redundant / dead / off-track

- **No crisis/research seam exists anywhere** (`grep crisis|research|wildfire` = 0 hits). North-star steps 1-2 (state a crisis -> research agent infers crisis type + ranked at-risk supplies) are entirely unbuilt. This is the single biggest gap for the track.
- **No ingest/forecast loop** (`grep ingest|forecast` = 0 hits in this track). North-star step 7 (button + chat command run WHO/weather/illness/CDC agents -> Redis -> Claude -> proactive recommendations) does not exist here at all. WHO/weather/illness forecast presumably lives in sibling `tracks/`/`fetch/` but is not wired to this engine.
- **Two-camera / Tailscale demo scaffolding is a parallel, now-orphaned track**: `tailscale_hosts.py`, `tailscale_smoke_test.py`, `camera_worker.py`, `vision_count.py`, `scan_dashboard.py`, `run_dashboard_demo.py`, `dashboard_bus.py`. The dashboard bus + scan dashboard are load-bearing for the "dashboard surface" north-star, but the Tailscale/two-MacBook camera plumbing is hackathon-night logistics, not product. Tailscale files are cut candidates; camera/vision are owned by the vision track (see deps).
- **`hello_world_agent.py`** — Phase-0 scaffold, pure dead code now. **Cut.**
- **`two_agent_payment_spike.py`** — served its purpose proving the seller-role decision; superseded by `wave2_e2e_check.py`. Keep only as a payment-isolation debugging aid; otherwise cut.
- **Naming drift**: every file/env here is `baymax_*` / `BAYMAX_*`; the root `README.md` documents `stockpile_*` / `STOCKPILE_*` and `stockpile_PRD_v2.md`. The README commands (`python stockpile_agents.py`, `STOCKPILE_EXIT_WHEN_DONE`) do not exist and will fail. Treat `baymax_*` as live truth; README is stale.
- **Redundant duplicate runners** `run_hospital_b.py` / `run_hospital_c.py` are near-identical (could be one parametrized runner) but are cheap and clear — leave as-is.

## Buggy / untested / risky

- **`order N <item>` quantity dropped at the approval gate.** `run_front.py` banner and `wave3` docstring tell the admin to reply `order N <item>`, but `parse_decision` only returns the bare string `"order"` (no quantity), and `_order_path` orders `neg["need"]` (the original shortfall), not N. So a quantity in a gate reply is silently ignored. The proactive `start_order` path (a fresh intent, not a gate reply) DOES honor quantity. Mismatch between the prompt text and behavior — fix the prompt or parse the qty.
- **In-process `NEGOTIATIONS` global is single-process only** (acknowledged in code). The Bureau and `run_front` work because everything is one process, but a true multi-process Mailbox deploy of A/B/C would lose negotiation state — would need `ctx.storage`/Redis. Fine for the demo, a real risk if the realignment splits processes.
- **`_PAYMENT_PENDING` lone-key fallback** (`_resolve_pending_key`) routes a commit to the single in-flight payment when the reference doesn't match, gated by buyer-address match. Correct for one concurrent user; with two simultaneous chat users it could mis-route if both buyers differ but references are both blank — low risk at demo scale, worth noting.
- **`BAYMAX_REQUIRE_FACILITY_APPROVAL` is fail-closed to DENY** (`approve_release` returns `not require`): turning the reserved per-facility gate on currently rejects every leg (no real channel exists). Documented, but a footgun if someone flips it for the demo.
- **No automated test runner**; the four wave harnesses + `BAYMAX_SELFTEST` ARE the suite (per project constraints). Coverage is good for negotiation/settlement/dashboard, but the live ASI:One signed-payment leg and the live Browserbase/Redis/Claude backends are only manually verifiable. The crisis/ingest north-star steps have zero tests because they have zero code.
- **`run_front.py` sets `BAYMAX_SPARSE_NARRATION` twice** (lines 58 and 74) — harmless, but a sign of merge cruft.
- **`distance_between` returns `0.0` for unknown facilities** -> `eta_minutes_for(0)=0`; a facility missing from `_FACILITY_META`/Redis silently looks "adjacent." Cosmetic for the demo, could mislead the ranker.

## File inventory (path -> 1-line purpose -> keep/cut/refactor)

- `baymax_agents.py` -> negotiation state machine + Bureau + hooks; the engine -> **keep** (core; extend for crisis/ingest here).
- `front_agent.py` -> chat intent parsing (4-5 kinds) + on_intent + echo hardening + selftest -> **keep** (add `crisis`/`ingest` kinds here).
- `run_front.py` -> live entrypoint: chat+payment+settlement hooks+dashboard pollers -> **keep**.
- `settlement.py` -> seller-side Payment Protocol, on-chain verify, trade + order settlement -> **keep**.
- `interfaces.py` -> FROZEN-style seam contract (get_inventory/rank_offers/order_from_supplier/approve_release) -> **keep** (add `research_crisis` seam alongside, do not alter existing).
- `protocol.py` -> FROZEN wire contract (Fetch re-exports + negotiation models + state enum) -> **keep, never edit**.
- `agent_base.py` -> event-loop fix, facility registry, chat protocol shell, agent factory, testnet guardrail -> **keep**.
- `redis_inventory.py` -> live Redis inventory backend behind get_inventory -> **keep** (north-star step 3).
- `claude_intent.py` -> optional Claude NLU parser behind _resolve_parser -> **keep** (likely the home/sibling for a crisis parser).
- `claude_ranking.py` -> optional Claude offer-ranking backend behind rank_offers -> **keep**.
- `supplier_order.py` -> Browserbase/Stagehand external-order backend behind order_from_supplier -> **keep** (north-star step 5; real, gated by `BAYMAX_BROWSERBASE`).
- `dashboard_bus.py` -> Redis trigger/decision/narration bus between dashboard and FRONT -> **keep** (the dashboard surface depends on it).
- `run_hospital_b.py` / `run_hospital_c.py` -> Mailbox runners for surplus facilities B/C -> **keep** (consolidate into one parametrized runner = optional refactor).
- `scan_dashboard.py` -> FastAPI dashboard UI (two cam feeds + Scan & Negotiate + SSE) -> **keep/refactor** (the realignment's dashboard surface; needs crisis-prompt + ingest-button additions).
- `run_dashboard_demo.py` -> single-process 3-agents + dashboard launcher -> **keep** (demo convenience).
- `camera_worker.py` -> per-MacBook camera HTTP server -> writes Redis inventory -> **keep but owned by vision track**.
- `vision_count.py` -> Claude Vision shelf counter (single shelf) -> **keep but owned by vision track**.
- `tailscale_hosts.py` -> resolve demo peers over Tailscale -> **cut** (night-of logistics, not product).
- `tailscale_smoke_test.py` -> Tailscale connectivity check -> **cut**.
- `two_agent_payment_spike.py` -> isolated payment-handshake spike -> **cut** (superseded by wave2 check; keep only as debug aid).
- `hello_world_agent.py` -> Phase-0 chat scaffold -> **cut** (dead).
- `wave2_e2e_check.py` -> offline chat->negotiate->approve->settle->pay proof -> **keep** (test suite).
- `wave3_order_e2e_check.py` -> offline admin-gate + external-order + settle proof -> **keep** (test suite).
- `wave4_dashboard_e2e_check.py` -> offline dashboard-trigger + gate + narration proof -> **keep** (test suite).
- `claude_intent_check.py` -> deterministic + (optional) Claude intent parser battery -> **keep** (test suite).
- `claude_ranking_check.py` -> mock + (optional) Claude ranking invariants check -> **keep** (test suite).
- `check_parse_decision.py` -> offline parse_decision + order-intent asserts -> **keep** (cheap unit check).
- `check_interfaces_order.py` -> offline order_from_supplier + approve_release mock check -> **keep** (cheap unit check).
- `supplier_order_smoke_test.py` -> LIVE Browserbase order smoke test (flags real-vs-mock) -> **keep** (only live verifier for step 5).

## Dependencies on other tracks

- **redis track (`tracks/redis` / repo-root `redis/`)**: `redis_inventory.py` adds `redis/src` to `sys.path` and imports `schema` for key names; reads `hospital:{id}:inventory|surplus|meta`. The whole `BAYMAX_REDIS=1` inventory path (north-star step 3) depends on the redis track's schema + seed data. `dashboard_bus.py` and `scan_dashboard.py` also depend on Redis being up.
- **vision/hardware track**: `camera_worker.py` / `vision_count.py` write the SAME Redis keys `redis_inventory.py` reads (shelf -> camera -> Redis -> agents). The dashboard proxies camera-worker MJPEG streams. Inventory truth ultimately flows from the vision track.
- **research/crisis track (DOES NOT EXIST YET)**: north-star steps 1-2 need a new agent/seam that infers crisis -> ranked at-risk items. It must select an item then call the EXISTING `start_negotiation(ctx, item, requester, quantity_needed, reply_to/source)`. The clean attach point is a new `research_crisis(prompt) -> {item, requester, quantity, rationale}` seam in `interfaces.py` + a new `kind:"crisis"` branch in `parse_intent`/`on_intent` (and a dashboard prompt box).
- **forecast/ingest track**: north-star step 7 (WHO/weather/illness/CDC) is presumably in sibling `tracks/`/`fetch/`; it must write Redis and then either trigger `start_negotiation`/`start_order` or surface recommendations. Needs a new `kind:"ingest"` chat command + a dashboard "Ingest Data" button RPUSHing onto a new bus key, and a new seam to run the forecast agents.
- **Anthropic (Claude)**: `claude_intent.py`, `claude_ranking.py`, `supplier_order.py` (Stagehand model), `vision_count.py` all call Claude via `ANTHROPIC_API_KEY`. Model ids in-tree: `claude-haiku-4-5` (intent), `claude-sonnet-4-6` (ranking/supplier/vision).
- **Browserbase/Stagehand**: `supplier_order.py` needs `stagehand==0.5.x` + `BROWSERBASE_API_KEY`/`PROJECT_ID`/`MODEL_API_KEY`.
- **uagents / uagents_core / cosmpy**: the frozen protocol re-exports and on-chain verify; testnet `dorado-1` only.
- **Secrets/venv** live in sibling `adyan-agent-communication-layer/` (`.env`, `.venv`, `private_keys.json` confirmed present), NOT symlinked into `agent-communication-layer/` — any live run needs them co-located (per CLAUDE.md).

## Open questions for the lead

1. **Where should the crisis/research agent live** — a new uAgent in this track that does Claude reasoning then calls `start_negotiation`, or a new `interfaces.research_crisis` seam consumed by `on_intent`? The seam approach matches the frozen-signature extension rule and reuses all hardening; recommend that.
2. **How should a crisis map to a quantity/requester?** Research yields a ranked at-risk item list, but `start_negotiation` needs one `(item, requester, quantity)`. Does the research agent pick the top item and let inventory derive the shortfall (`quantity_needed=None`), or propose a need? Need a decision so the seam contract is right the first time.
3. **Ingest loop trigger surface**: a new `kind:"ingest"` chat command + dashboard "Ingest Data" button both RPUSHing onto a new `baymax:ingest` bus key, polled in `run_front` like the existing trigger poller — does that match the intended UX, and who owns running the WHO/weather/illness/CDC agents?
4. **The `order N <item>` gate-quantity bug**: fix `parse_decision` to capture N (and thread it into `_order_path`), or just correct the banner/docstring to say the gate orders the full shortfall? (Proactive `start_order` already honors N.)
5. **Naming rebrand**: do we rename `baymax_*` -> `stockpile_*` (touches every file, env var, seed-derived addresses change ONLY if seed env names change — they would, `BAYMAX_*_SEED`), or fix the README to match `baymax_*`? Renaming risks address drift + breaking the manual Mailbox connect; recommend fixing the README instead.
6. **Multi-process state**: if the realignment deploys A/B/C as separate Mailbox processes (not Bureau), the in-process `NEGOTIATIONS`/`_PAYMENT_PENDING` globals break. Is the demo staying single-process (`run_dashboard_demo.py`) or going distributed?
