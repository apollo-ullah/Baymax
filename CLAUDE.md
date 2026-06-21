# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Baymax** — a network of Fetch.ai uAgents that detect hospital supply shortfalls and autonomously negotiate + settle inter-facility transfers. A supply manager states an intent in natural language via ASI:One ("Hospital A is short on IV fluids"); the FRONT agent broadcasts the need, ranks offers from surplus facilities, composes a (possibly split) transfer, and settles it as a real Fetch **testnet** FET transaction — narrating each step back into the chat. Built for the Fetch.ai "From Intent to Action" challenge.

**The agent code lives in `agent-communication-layer/`** — that's where every command below is run from. The repo is a multi-track monorepo: alongside it sit `redis/` (the Redis state bus + seed scripts), `hardware/` (camera → Claude Vision → Redis), `arize/` / `tracks/` (observability), `ui/` and `fetch/`, plus `baymax_PRD_v2.md` (the product spec).

> ⚠️ **venv + secrets live in a sibling directory, not in the source dir.** Due to a directory rename during a `main` merge, the virtualenv and secrets are in `../adyan-agent-communication-layer/` (`.venv`, `.env`, `private_keys.json`) — *not* inside `agent-communication-layer/`. So: `cd agent-communication-layer && source ../adyan-agent-communication-layer/.venv/bin/activate`, then run `python <script>`.
>
> **Live mode needs `.env` co-located with `agent_base.py`.** `agent_base.py` calls `load_dotenv(Path(__file__).parent / ".env")` and the other scripts use bare `load_dotenv()` (cwd-relative) — neither reaches the sibling dir. The **offline harnesses run fine as-is** (they fall back to the public dev seeds in `agent_base.py`), but any **live** run (real seeds, `ANTHROPIC_API_KEY`, Browserbase, on-chain settlement) needs the secrets reachable from `agent-communication-layer/`: `ln -s ../adyan-agent-communication-layer/.env .env` (and likewise `private_keys.json`).

## Commands

```bash
cd agent-communication-layer
source ../adyan-agent-communication-layer/.venv/bin/activate   # Python 3.12+ (developed on 3.14); venv lives in the sibling dir
# first-time only, if recreating the venv from scratch:
#   python -m venv ../adyan-agent-communication-layer/.venv && source ../adyan-agent-communication-layer/.venv/bin/activate
#   pip install -r requirements.txt
```

There is **no test runner, linter, or build step.** The verification harnesses below *are* the test suite — each is a self-contained, self-exiting script. Commands below assume the sibling venv is **activated** (so `python` is the right interpreter); otherwise prefix each with `../adyan-agent-communication-layer/.venv/bin/python`.

| Goal | Command |
| :-- | :-- |
| Run the full 3-agent negotiation in one process (Bureau) | `BAYMAX_EXIT_WHEN_DONE=1 python baymax_agents.py` |
| Pick the scenario | prefix with `BAYMAX_ITEM="IV fluids"` (split, default) / `"saline"` (full cover) / `"sutures"` (no offer → escalation) |
| FRONT chat→negotiate→narrate loop, no network | `BAYMAX_SELFTEST=1 python front_agent.py` |
| Offline end-to-end incl. settlement (chat→negotiate→settle→pay), no ASI:One/wallet | `python wave2_e2e_check.py` |
| Payment-protocol handshake in isolation (2 agents, fake tx) | `PAYMENT_VERIFY_ONCHAIN=false python two_agent_payment_spike.py` |
| Re-derive agent addresses from seeds | `python -c "import agent_base; print(*(f'{f}: {agent_base.address_for(f)}' for f in ('Hospital A','Hospital B','Hospital C')), sep=chr(10))"` |
| Negotiation against **live Redis** inventory (split sourced from `tracks/redis`) | `BAYMAX_REDIS=1 BAYMAX_ITEM="IV fluids" BAYMAX_NEED=200 BAYMAX_EXIT_WHEN_DONE=1 python baymax_agents.py` |
| Offline admin-gate + external-order E2E (chat→negotiate→admin orders→supplier→settle) | `python wave3_order_e2e_check.py` |

**Live Redis bring-up** (needed once before the Redis-backed row above; `redis` + `python-dotenv` must be in `.venv`):

```bash
docker compose -f ../redis/docker-compose.redis.yml up -d        # redis-stack on :6379 (track lives at repo-root redis/)
(cd ../redis/src && REDIS_URL=redis://localhost:6379 \
   ../../adyan-agent-communication-layer/.venv/bin/python seed_demo_data.py)   # seed hospitals/inventory/surplus/forecast
```

**Camera as the inventory source** (optional): `../hardware/camera connection/sync_to_redis.py`
counts saline per hospital via Claude Vision (green-straw divider → Hospital A left /
B right) and writes `qty`/`pct`/`status` + `surplus` straight into the same Redis the
negotiation reads. `surplus = max(0, count − reserve)` is what `redis_inventory` reads as
`spare_capacity`. Keyless test path: `python "../hardware/camera connection/sync_to_redis.py" --counts a=0,b=6`
(only `redis` needed); real run uses the camera + `ANTHROPIC_API_KEY` (see `../hardware/requirements.txt`).
Chain: shelf → camera → Redis → agents.

**Live ASI:One / Agentverse (Mailbox mode):** run each agent in its own terminal (order does not matter), then do the one-time browser Mailbox connect from each agent's Inspector URL (see `README.md` and `DELIVERABLES.md`):

```bash
python run_hospital_b.py     # surplus facility B
python run_hospital_c.py     # surplus facility C
python run_front.py          # Hospital A — ASI:One chat + payment entrypoint
```

## Architecture

### The Wave-0 frozen contract (zero model drift)

`protocol.py` is the **single source of truth** for every cross-agent message model and the negotiation state machine. All other modules import from it and must never redefine these models — changing a field is a contract change. Two model categories:

- **Official Fetch surfaces, re-exported *unchanged*:** the Chat Protocol (`AgentChatProtocol` v0.3.0) and Payment Protocol (`AgentPaymentProtocol` v0.1.0) classes come from `uagents_core.contrib.protocols.*` and are re-exported here. ASI:One/Agentverse match these by **schema digest**, so a local copy would be a different, incompatible protocol. Never hand-define `RequestPayment`, `ChatMessage`, etc.
- **Baymax negotiation models** (defined here): `SupplyRequest`, `SupplyOffer`, `TransferProposal`, `TransferAccept`, `TransferReject`, plus `NegotiationState` / `Urgency`.

### The negotiation core and its seams

`baymax_agents.py` runs the PRD §10 chain across three agents (Hospital A = FRONT/requester, B & C = surplus):

```
shortfall_detected → requesting → collecting_offers → evaluating
   → (re_planning if no single offer covers the need) → proposing → settling → confirmed
```

Two layers deliberately sit behind **stubbed seams** in `interfaces.py`, owned by other workstreams and shipping as deterministic mocks. **The function signatures are the contract:**
- `get_inventory(hospital, item) -> InventoryState` — Redis seam, **now live**. With `BAYMAX_REDIS=1` it delegates to `redis_inventory.py`, which reads the teammates' Redis (`tracks/redis`, keyed `hospital_a`/`"IV Fluids"` — `redis_inventory.py` maps the display names + canonicalises items) and derives `safety_threshold = qty − surplus` so `spare_capacity` equals the Redis surplus. On ANY failure (Redis down, lib missing, unknown hospital) it falls back to the hardcoded mock below — fail-closed, never hangs (FR1). Default (no env) = mock, so the offline harnesses need no Redis. `distance_between` likewise uses Redis `meta` lat/lng in Redis mode. The mock still encodes the IV-fluids/saline/sutures scenarios; note the seeded Redis has no `sutures`, so the no-offer scenario is mock-only.
- `rank_offers(need, offers) -> RankedPlan` — Claude/ranking seam. Mock = nearest-first greedy allocator that produces the canonical 150+50 split. `interfaces.py` has **no uagents dependency** by design; the agent layer adapts between its plain dataclasses and `protocol.py` wire models.

The settlement step is also a seam: `baymax_agents.settle_transfer()` delegates to a hook registered via `register_settlement_hook(...)`. The core **never imports the settlement layer** — it's a one-way dependency wired at deployment time (`run_front.py` registers `settlement.settle_via_payment_protocol`). With no hook (local Bureau demo), `settle_transfer` returns a stub reference so the chain still completes.

**Wave 3 — admin approval gate + external supplier order.** After evaluation the negotiation halts at `AWAITING_APPROVAL` instead of auto-proposing. A chat reply (`approve` / `order` / `reject`) resumes via `resume_after_admin_decision`. The `approve` branch proposes and settles the inter-facility trade as before. The `order` branch calls the `order_from_supplier` seam (`interfaces.py`) — which delegates to `supplier_order.py` when `BAYMAX_BROWSERBASE=1` (Stagehand/Playwright over Browserbase), falling back to a deterministic mock — and settles to `BAYMAX_SUPPLIER_WALLET` (or the FRONT wallet, narrated as symbolic). The `reject` branch cancels and narrates. B/C release of each transfer leg is gated by the `approve_release` seam in `interfaces.py`, controlled by `BAYMAX_REQUIRE_FACILITY_APPROVAL`. A proactive `order N <item>` chat intent (no shortfall required) routes via `kind:"order"` → `start_order` in `front_agent.py`, bypassing the shortfall guard entirely.

### Run modes

- **Bureau (one process):** `baymax_agents.py`'s `__main__` and the various self-tests build all agents in a single `Bureau`. In-process negotiation state lives in the module-global `NEGOTIATIONS` dict (a real multi-process deploy would move this to `ctx.storage`/Redis). Importing `baymax_agents` has **no side effects** — agent/Bureau construction is guarded under `if __name__ == "__main__"`.
- **Mailbox (separate processes):** the `run_*.py` runners wrap each agent with `build_hospital_agent(..., mailbox=True)` for Agentverse/ASI:One reachability without a public endpoint.

### FRONT and settlement wiring

- `front_agent.py` turns a chat utterance into a negotiation: `on_intent` → `parse_intent` (a deterministic keyword/regex parser; a commented ASI:One-LLM seam can drop in behind the same signature) → `start_negotiation(..., reply_to=<chat sender>)`. Setting `reply_to` makes every milestone stream back to ASI:One automatically as a `ChatMessage`. `parse_intent` has substantial hardening against the ASI:One LLM **echo loop** (it parrots our narration back) — milestone/meta regexes, an echo-chatter heuristic, and a per-sender cooldown.
- `run_front.py` is the live entrypoint. It **reuses** `front_agent.build_front_agent()` (so the two construction paths can't drift), then layers on the Payment Protocol, registers the FET wallet, and registers the settlement hook. `build_front_agent()` alone does **not** attach payments — if you're debugging live settlement, the running process is `run_front.py`.
- Settlement flow (seller side, `settlement.py`): on a settled deal the agent sends a standalone `RequestPayment` to the chat user → user's wallet replies `CommitPayment` → we verify the tx on-chain (cosmpy, in a worker thread) → reply `CompletePayment` / `CancelPayment`, then `finalize_after_payment` / `fail_after_payment` emit the terminal chat milestone.

## Hard contracts and non-obvious gotchas

These are easy to get wrong and have all bitten this codebase before:

- **Import `agent_base` *first*, before constructing any `Agent` or `Protocol`.** Python 3.14 removed the implicit current event loop, but `uagents` 0.25.2 calls `asyncio.get_event_loop()` in `Agent.__init__`. `agent_base` installs a loop as an import side effect. Every module that builds agents imports it first on purpose.
- **Testnet only, fail-closed.** `agent_base.py` raises if `FETCH_NETWORK` is anything other than `testnet`/empty; `network="testnet"` is forced on every agent. Payment verification pins `NetworkConfig.fetchai_stable_testnet()` (chain `dorado-1`, denom `atestfet`). Never route to mainnet.
- **Payment role is the inverse of the docs prose.** Use `Protocol(spec=payment_protocol_spec, role="seller")` for our service agent. The *installed* spec maps each role to the messages it may **RECEIVE**: `roles["seller"] = {CommitPayment, RejectPayment}` (what we receive after sending `RequestPayment`). Verify before trusting any doc:
  `python -c "from uagents_core.contrib.protocols.payment import payment_protocol_spec as s; print({r:sorted(m.__name__ for m in ms) for r,ms in s.roles.items()})"`
- **ASI:One renders the in-chat FET payment card from `RequestPayment.metadata`.** It reads `metadata["provider_agent_wallet"]` (the fetch1… payee) and `metadata["fet_network"]`. Send `RequestPayment` with `metadata=None` and ASI:One rejects it at ingestion with *"Failed to process payment response by agent"* — before any approval, no card renders. `settlement.request_payment()` always populates these keys (mirroring `fetchai/innovation-lab-examples/fet-example`). Adding metadata does **not** change the protocol schema digest, so manifest matching is unaffected.
- **`ctx.agent.wallet` does not exist inside a handler** — `ctx.agent` is an `AgentRepresentation` (address/identity only). Call `register_recipient_wallet(agent)` once at construction time (where `agent.wallet` is available) and use `resolve_recipient_wallet(ctx)` inside handlers.
- **Never call sync cosmpy in an async handler.** `LedgerClient.query_tx()` blocks the event loop (~20s on a slow/unreachable RPC); always wrap with `asyncio.to_thread(...)`.
- **Deterministic addresses from seeds.** Addresses are derived from `BAYMAX_*_SEED` env vars; the dev fallback seeds in `agent_base.py` are public (local only). Set the seed env vars for any real deployment — addresses change accordingly.

## Key environment variables

| Var | Effect |
| :-- | :-- |
| `BAYMAX_ITEM` | Bureau demo scenario: `"IV fluids"` (split, default) / `"saline"` (full cover) / `"sutures"` (no offer) |
| `BAYMAX_EXIT_WHEN_DONE` | Self-exit the process when a negotiation terminates (set in one-shot tests) |
| `BAYMAX_SELFTEST` | `front_agent.py`: run the in-process chat→negotiate→narrate self-test |
| `BAYMAX_OFFER_TIMEOUT` | Seconds to wait for offers before evaluating (Bureau ~4s; Mailbox needs 30s+) |
| `BAYMAX_SPARSE_NARRATION` | Only stream key milestones to ASI:One (avoids chat-relay 429s); set by `run_front.py` |
| `BAYMAX_MAX_REPLANS` | Bounded re-home attempts when a transfer leg is rejected (default 3) |
| `BAYMAX_REDIS` | `1`/`true` → `get_inventory`/`distance_between` read the live Redis (`tracks/redis`) via `redis_inventory.py`, falling back to the mock on failure. Unset = mock. |
| `REDIS_URL` | Redis endpoint for `BAYMAX_REDIS` mode (default `redis://localhost:6379`). `BAYMAX_REDIS_TIMEOUT` bounds the socket (default 2s). |
| `PAYMENT_VERIFY_ONCHAIN` | `false` skips the cosmpy tx query and trusts the commit — **dev/spike only**, no real settlement guarantee |
| `BAYMAX_PAYMENT_AMOUNT_FET` / `BAYMAX_PAYMENT_PER_UNIT_FET` | Flat vs per-unit FET pricing per settlement |
| `BAYMAX_BROWSERBASE` | `1`/`true` → `order_from_supplier` drives a vendor site via Stagehand/Playwright over Browserbase (`supplier_order.py`); unset = deterministic mock. Fail-closed to mock. |
| `BAYMAX_SUPPLIER_URL` | Vendor site to drive in Browserbase mode. |
| `BAYMAX_SUPPLIER_WALLET` | `fetch1…` payee for external-order FET settlement; unset → FRONT wallet, narrated as symbolic. |
| `BAYMAX_APPROVAL_TIMEOUT` | Seconds to wait for the admin's approve/order/reject decision before the watchdog auto-fails (default 300). |
| `BAYMAX_REQUIRE_FACILITY_APPROVAL` | Reserved per-facility (B/C) admin gate; default off (auto-approve + notify). When on, currently denies (fail-closed until a real channel exists). |
| `BAYMAX_DEFAULT_ORDER_QTY` | Default quantity for a proactive order when none is stated and there's no shortfall (default 100). |
| `BROWSERBASE_API_KEY` / `BROWSERBASE_PROJECT_ID` / `MODEL_API_KEY` | Browserbase/Stagehand credentials (only for `BAYMAX_BROWSERBASE=1`). |
| `BAYMAX_*_SEED`, `FETCH_NETWORK` | Agent seeds (loaded from `.env`); network guardrail (testnet only) |

Secrets and the venv currently live in the sibling `../adyan-agent-communication-layer/` (`.env`, `private_keys.json`, `.venv/`). `.gitignore` covers `.venv/`, `.env`, `__pycache__/`, `*.pyc` — note it does **not** list `private_keys.json`, so if you ever copy/symlink that file into the tracked `agent-communication-layer/`, add it to `.gitignore` first to avoid committing keys. `DELIVERABLES.md` tracks submission status and the manual, browser-gated Mailbox-connect checklist; the live ASI:One signed-payment leg is the one path not verifiable offline.
