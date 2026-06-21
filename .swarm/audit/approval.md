# approval audit

Track: **approval** — `/Users/adyan/Documents/GitHub/CalHax/fetch/approval/` (the iMessage human-in-the-loop pipeline).
Files: `service.py`, `imessage_client.py`, `inventory_seam.py`, `state.py`, `serve_with_ngrok.sh`, `README.md`, `__init__.py` (empty).

## Aligned with demo story

The track delivers one piece the north-star explicitly wants — **a real, phone-tappable Accept/Reject approval gate before a transfer fires** (story step 6's human gate). What is genuinely good and reusable:

- **`imessage_client.py` is the only durable asset.** A clean, never-raises `notify(hospital_id, message) -> bool` that drives Messages.app via `osascript`. It reads the AppleScript from stdin (`osascript -`) and passes recipient + body as argv so emoji/newlines/URLs/quotes are never string-interpolated — a deliberate, correct anti-escaping-bug design. **This is the one file the rest of the repo actually reuses**: `ui/app.py:129` imports `from fetch.approval.imessage_client import notify`. Per-hospital recipient env (`IMESSAGE_TO_HOSPITAL_A/_B`) with a shared `IMESSAGE_TO` fallback, and a "no recipient → log only" no-op path so the flow is demoable without a phone. Keep this.
- **The B-deny → external-buy handoff is the right shape for story step 5.** `service.py:b_deny` publishes an `approval_b_denied` event to `redis_io.EVENTS_CHANNEL` as the documented hook for the Browserbase "buy" fallback. The *intent* matches the north-star (no internal surplus → external order), even though nothing consumes that event today.
- **Dashboard-visibility wiring is correct in principle.** `state.py` persists each request at `approval:{id}` and publishes `approval_update` events so the dashboard could show the human-approval flow alongside agent negotiation — the "two surfaces, one engine" goal.

## Redundant / dead / off-track

This is the headline finding: **the approval track is a parallel, now-orphaned second approval mechanism.** The repo already converged on a *different* gate, and this FastAPI service was left behind.

- **`service.py` is effectively dead.** Nothing outside `fetch/approval/` imports or launches `service:app` (confirmed by repo-wide grep — the only external hit is `.swarm/STATE.md`). The live approval gate is the **agent layer's Wave 3/4 gate**: `baymax_agents.py` halts at `NegotiationState.AWAITING_APPROVAL`, the dashboard (`ui/app.py` `/req/<rid>/approve|order|reject`) pushes `{approve|order|reject}` onto the Redis `baymax:decision` list (`dashboard_bus.py:DECISION_LIST`), and `run_dashboard_demo.py` / `run_front.py` LPOP it and call `resume_after_admin_decision(ctx, req_id, decision)`. That path is exercised by `wave4_dashboard_e2e_check.py`. **`service.py` participates in none of it** — it has its own request IDs (`appr_…`), its own state keys (`approval:{id}`), its own A→B two-hop state machine (`A_NOTIFIED`→`A_ACCEPTED`→`B_NOTIFIED`→`SETTLED`/`B_DENIED`), and its own `/req/{id}/a/accept` endpoints that the agent layer never calls.
- **Two incompatible "/req/{id}/..." link schemes coexist.** `service.py` mints `…/a/accept`, `…/a/reject`, `…/b/accept`, `…/b/deny`. `ui/app.py` mints `…/approve`, `…/order`, `…/reject` (note: it has the **order** branch the approval service lacks — needed for story step 5). The approval README and `ui/app.py:_send_approval_imessage` build *different* link sets. Only the UI's links reach the live engine.
- **`service.py:b_accept` writes a transfer directly to Redis and declares it `"settled"/"approved_by_doctors"` with a fabricated 20-min ETA — it bypasses the agents and the real FET testnet settlement entirely.** This directly contradicts north-star step 6 (settlement is a real testnet FET payment). If this service ran, it would log a fake "confirmed" transfer that never touched the chain.
- **`README.md` and `service.py` docstring/index still say "Poke".** `service.py` module docstring ("Drives the approval state machine over Poke notifications"), and `index()` HTML ("over Poke (Hospital A ↔ Hospital B)") are stale — the code pivoted to iMessage (git `8f25862`). Cosmetic but misleading.

## Buggy / untested / risky

- **No harness covers this track.** The frozen test suite (`wave2/3/4_e2e_check.py`, `BAYMAX_SELFTEST`) lives in `agent-communication-layer/` and exercises the *other* gate. `fetch/approval/` has zero automated coverage; the README's "try it" is manual curl + tapping links.
- **Platform-locked + demo-fragile (both gates share this).** `imessage_client.py` is **macOS-only** (`osascript` + Messages.app signed into iMessage; texts send from *this* Mac's Apple ID). `serve_with_ngrok.sh` needs an **ngrok** tunnel + authtoken for links to be phone-tappable, scrapes the public URL from ngrok's local API (`127.0.0.1:4040`), and bakes it into `APPROVAL_BASE_URL`. Any judge machine without macOS + a configured iMessage account + ngrok degrades to log-only. This fragility is inherited by the live path too, since `ui/app.py` reuses the same `notify`.
- **`serve_with_ngrok.sh` points at the wrong venv.** It activates `.venv` at repo root (`source .venv/bin/activate`), but per CLAUDE.md the venv lives in the sibling `../adyan-agent-communication-layer/.venv`. It will silently run against system Python / miss `fastapi`+`uvicorn` unless a repo-root `.venv` exists.
- **State store unbounded / no TTL.** `state.py` writes `approval:{id}` with `client.set(...)` and never expires it; `get()` returns `None` for unknown IDs but the endpoints only guard the page case. Low risk for a demo, but stale records accumulate.
- **`__init__.py` is empty** yet `service.py` uses package-relative imports (`from ..shared import redis_io`), so the service must be launched as a module from repo root (`uvicorn fetch.approval.service:app`) — running the file directly breaks. README gets this right; worth noting it's load-bearing.

## File inventory (path -> 1-line purpose -> keep/cut/refactor)

| Path | Purpose | Verdict |
| :-- | :-- | :-- |
| `fetch/approval/imessage_client.py` | Outbound iMessage via `osascript`; `notify(hospital_id, msg)`; log-only fallback | **KEEP** — the one reused asset (imported by `ui/app.py`) |
| `fetch/approval/service.py` | Standalone FastAPI A↔B approval state machine + tappable links | **CUT** (or refactor into the agent gate) — orphaned, parallel mechanism, fakes settlement |
| `fetch/approval/state.py` | Persist `approval:{id}` + publish `approval_update` events | **CUT/refactor** — only used by the dead `service.py` |
| `fetch/approval/inventory_seam.py` | Shortage detection (short requester + surplus provider) for the dead service | **CUT** — duplicates agent-layer inventory logic (see below) |
| `fetch/approval/serve_with_ngrok.sh` | Launch service behind an ngrok tunnel for phone-tappable links | **CUT** — tied to the dead service; wrong venv path |
| `fetch/approval/README.md` | Docs for the iMessage approval flow | **REFACTOR** — stale "Poke" wording; describes the orphaned mechanism |
| `fetch/approval/__init__.py` | Empty package marker | KEEP (needed for `fetch.approval.imessage_client` import) |

### `inventory_seam.py` — does it duplicate `interfaces.py` / `redis_inventory.py`?

**Yes, it is a third, divergent inventory reader.** `inventory_seam.py:_detect_from_redis` reads Redis surplus via `fetch/shared/redis_io.py` → which re-exports the **Redis track's** `redis/src/inventory.py` (`get_inventory`/`get_surplus`). That is a *different* code path from the agent layer's `agent-communication-layer/redis_inventory.py` (the `BAYMAX_REDIS=1` seam behind `interfaces.get_inventory`). So three readers of the same Redis now exist:
1. `interfaces.get_inventory` → `redis_inventory.py` (agent layer; returns an `InventoryState` dataclass; maps display names + canonicalises item names).
2. `ui/app.py` `get_state()` (its own inline `r.hgetall` reads).
3. `fetch/approval/inventory_seam.py` (its own `redis_io` reads; hardcodes `_HOSPITAL_NAMES` SF General / UCSF / Kaiser and a `_MOCK` IV-Fluids fallback).

`inventory_seam.py` does **not** call `interfaces.py` and does not share its `InventoryState`/`safety_threshold`/`spare_capacity` derivation. It is a self-contained shortage detector for the dead service; its `make_shortage`/`Shortage` dataclass have no consumer outside `service.py`. **Cut with the service.** Note also `fetch/shared/protocol.py` is a *hand-redefined* duplicate of the frozen negotiation/payment models — a frozen-contract violation, but it belongs to the `shared` track, not approval (the approval track does not import it).

## Dependencies on other tracks

- **Redis track** (`redis/src`): via `fetch/shared/redis_io.py`. `state.py` and `service.py` write/publish to `redis_io.EVENTS_CHANNEL`; `inventory_seam.py` reads `get_inventory`/`get_surplus`. Schema is owned by `redis/` (`schema.py`, `redis_contract.md`).
- **UI track** (`ui/app.py`): the **only live consumer** of this track — imports `imessage_client.notify` only. The UI does NOT use `service.py`/`state.py`/`inventory_seam.py`; it reimplements approval links and pushes to `baymax:decision`.
- **Agent communication layer**: the *real* approval gate lives here (`AWAITING_APPROVAL` / `resume_after_admin_decision` / `dashboard_bus.DECISION_LIST="baymax:decision"`). The approval track does **not** integrate with it — no shared request IDs, no shared decision queue, no call into `resume_after_admin_decision`. The `approve_release` seam + `BAYMAX_REQUIRE_FACILITY_APPROVAL` (`interfaces.py:458`) is yet a *third* approval concept (per-facility B/C release gate, currently auto-approve or fail-closed-deny).
- **Browserbase/supplier track**: only via the unconsumed `approval_b_denied` event published in `service.py:b_deny`.

## Open questions for the lead

1. **One gate or two?** Confirm the intended single gate is the agent layer's `AWAITING_APPROVAL` → `baymax:decision` → `resume_after_admin_decision` path (the one the dashboard + `wave4` already use). If so, `service.py` + `state.py` + `inventory_seam.py` + `serve_with_ngrok.sh` should be **deleted**, keeping only `imessage_client.py`. The north-star "one gate" goal is *already met* by the agent layer; the FastAPI service is the redundant one.
2. **Where do the tappable iMessage links point?** Today `ui/app.py:_send_approval_imessage` sends links to `APPROVAL_BASE_URL/req/{id}/approve|order|reject` (Flask UI). For phone-tappability that base must be a public (ngrok) URL of the **Flask UI**, not of the dead FastAPI service. Confirm the demo plan ngroks `ui/app.py`, and retire `serve_with_ngrok.sh` (or repoint it at the UI + fix its venv path).
3. **`order` branch parity.** The live gate supports `approve|order|reject` (order = external Browserbase buy, story step 5). The approval service only has accept/deny. When consolidating, ensure the kept iMessage message template carries all three actions (the UI template already does).
4. **Crisis-flow reuse (north-star).** The realignment wants the crisis flow (research → shortfall → negotiate) to halt at the *same* gate before settlement/order. That already routes through `AWAITING_APPROVAL`, so reusing it requires **no new approval code** — just keep `imessage_client.notify` as the notification transport. Confirm no one is tempted to revive `service.py` for the crisis path.
5. **Settlement integrity.** `service.py:b_accept` logs a fake `"settled"` transfer with no FET payment. Confirm this is dead and will not be demoed — it conflicts with the real testnet-settlement requirement.
6. **macOS/ngrok dependency for judging.** Is the demo guaranteed to run on a macOS host with Messages.app + iMessage signed in and ngrok configured? If not, the team should plan to fall back to the log-only path (which still works) and not promise live phone delivery.
