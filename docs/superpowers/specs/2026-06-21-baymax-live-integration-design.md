# Baymax Live Integration — Design Spec

**Date:** 2026-06-21
**Status:** Revised after code-review (awaiting sign-off)
**Goal:** Make the full Baymax pipeline run **live, end-to-end** for a demo: multiple
MacBook cameras → shared Redis → Fetch.ai negotiation → **real agent-to-agent FET
testnet settlement** → **live Browserbase supplier ordering** → narrated to the
**Next.js `web/` app**, which is the judge-facing surface. Flask `ui/app.py` is
demoted to a backend API + ops console.

---

## Revision note (corrections from the code-review of the first draft)

This revision fixes claims that did not hold against the code, and folds in two
scope decisions made after the first draft:

1. **Front-end surface changed to the Next.js `web/` app** (was "Flask dashboard
   only"). The premium UI now lives in this repo (`web/`, committed `28f3b39`) and is
   the demo surface; Flask becomes the backend/ops layer it proxies.
2. **Browserbase supplier ordering is now IN SCOPE** (was Non-Goal #5) and imperative:
   drive **one** vendor's on-site search + buy, live. (It turns out this is already
   implemented in `supplier_order.py` — the work is *enable + wire*, not build.)
3. **C1 seed claim corrected.** The first draft claimed `seed_for()` falls back
   `BAYMAX_* → STOCKPILE_* → dev seed` "so the funded wallets are actually used."
   **False:** `agent_base.py:96` is `os.getenv(seed_env) or _DEV_SEEDS[...]` with no
   `STOCKPILE_*` fallback. Today the agents run on **public dev seeds (unfunded)**.
4. **`FET_FUND` does not exist** in the repo (no faucet code anywhere). The funding
   step is rewritten around a real balance-check + external testnet faucet.

---

## 1. Context

~85% of the wiring already exists and works offline. The spine is **Redis-centric**:
neither front-end imports the agents in-process — they talk to them through Redis
lists/pubsub and the Flask process spawns the Bureau as a subprocess.

```
camera → Redis (vision:*, inventory) → agents (BAYMAX_REDIS) → narration → Flask SSE
Flask  → Redis (baymax:trigger/crisis/decision) → agents → settlement / supplier order
Next.js web/  ──(server-side proxy, BAYMAX_API_URL)──>  Flask :5001  (the only client of Flask)
```

What is **not** live today:
- The **Next.js UI runs on mock data** — `web/lib/api.ts` `live` mode is stubbed
  (it delegates straight to mock), there are **no Next route handlers**, and there is
  **no camera view** component.
- **Real on-chain FET settlement on the agent path** (the dashboard path uses a stub
  ref; real payment only existed on the now-out-of-scope ASI:One human-chat path).
- **Live Browserbase supplier ordering** — `supplier_order.py` exists and is complete,
  but `stagehand`/`browserbase` are **commented out** in `requirements.txt`, so the
  `order_from_supplier` seam silently runs as the deterministic mock.
- A confirmed multi-Mac live-vision setup with **Hospital C** on camera.
- **Funded, deterministic agent wallets** — blocked by a seed-env-name mismatch
  (see C1) that silently routes agents to public dev-seed wallets.

This spec closes those gaps. It does **not** rebuild the architecture — it makes the
existing seams real and points the new UI at them.

## 2. Confirmed decisions

| Decision | Choice |
| :-- | :-- |
| **Front-end surface** | **The Next.js `web/` app is the judge-facing surface.** Flask `ui/app.py` (:5001) becomes the backend API + ops console that the Next.js route handlers proxy. |
| **Supplier ordering** | **Live Browserbase, driving ONE vendor's own on-site search + buy** (no multi-vendor discovery). Mock fallback on any failure. |
| Settlement realness | **Real testnet FET, with automatic fallback to simulated** (never hangs the demo) |
| Payment mechanism | **Agent-to-agent via the Fetch Payment Protocol**, executed automatically — no human approval, no Agentverse Mailbox |
| Camera setup | **3 laptops, one per hospital (A+B+C)**, shared Redis over Tailscale/LAN; extend the live-camera path to Hospital C |
| FET amount | Keep existing flat `BAYMAX_PAYMENT_AMOUNT_FET` default (`0.1` FET / leg) |

## 3. Target topology (demo day)

```
┌── HOST MacBook = "Hospital A" (the server) ───────────────────────────────┐
│   • Redis (docker, :6379)            ← shared state bus                    │
│   • Flask backend/API   :5001        ← backend + ops console (not for judges)│
│   • Next.js web/ app    :3000        ← THE judge-facing surface            │
│       - route handlers proxy Flask server-side (BAYMAX_API_URL=:5001)     │
│       - live pipeline narration (SSE), forecast, decide, camera card      │
│   • Bureau (run_dashboard_demo.py): agents A + B + C, ONE process         │
│       - A = FRONT/requester + payment BUYER (pays B/C and suppliers)      │
│       - B, C = surplus + payment SELLER                                   │
│   • Hospital A camera (capture_single.py, HOSPITAL_ID=hospital_a)         │
│   • Browserbase cloud browser  ← driven for the live supplier-order leg   │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │ shared Redis over Tailscale/LAN (REDIS_URL → host)
        ┌──────────────┼──────────────┐
        ▼                              ▼
┌── MacBook "B" ──────────┐    ┌── MacBook "C" ──────────┐
│ capture_single.py --watch│    │ capture_single.py --watch│
│ HOSPITAL_ID=hospital_b   │    │ HOSPITAL_ID=hospital_c   │
└──────────────────────────┘    └──────────────────────────┘
```

All three agents live in **one Bureau process** on the host, so agent-to-agent Payment
Protocol messages flow locally — no Mailbox/Agentverse needed. The Next.js app is the
**only** client of Flask.

## 4. Component design

### C1 — Secrets & environment wiring (unblocks every live leg)

**Problem:** the venv + secrets live in the sibling `../adyan-agent-communication-layer/`.
`agent_base.py` loads `.env` from its own dir; `ui/app.py` loads repo-root `.env` (absent).
Neither reaches the sibling. Result: live runs silently fall back to mocks.

**Changes:**
- Symlink into `agent-communication-layer/`: `.env` → `../adyan-agent-communication-layer/.env`
  and `private_keys.json` → `../adyan-agent-communication-layer/private_keys.json`.
- **Add `private_keys.json` to `.gitignore` first** (it is not currently covered).
- Make the Flask process inherit the same env (symlink a repo-root `.env`, or point its
  `load_dotenv` at the agent dir). The Flask process, the Bureau subprocess it spawns, and
  the camera subprocess must all see `ANTHROPIC_API_KEY` + `REDIS_URL` (+ Browserbase creds, C7).
- **Fix the seed-name mismatch (verified real).** `agent_base.FACILITIES` reads
  `BAYMAX_FRONT_SEED` / `BAYMAX_HOSP_B_SEED` / `BAYMAX_HOSP_C_SEED` (`agent_base.py:81-83`),
  but `.env` defines `STOCKPILE_*_SEED` (a leftover from the project's old name) plus an
  `AGENT_SEED_PHRASE`. `seed_for()` (`agent_base.py:96`) is `os.getenv(seed_env) or _DEV_SEEDS`
  — **no `STOCKPILE_*` fallback** — so agents currently use the **public dev seeds**.
  Fix (minimal): add a fallback in `seed_for()` —
  `os.getenv(seed_env) or os.getenv(seed_env.replace("BAYMAX_", "STOCKPILE_")) or _DEV_SEEDS[...]`
  — or define `BAYMAX_*_SEED` in `.env`. Either way, **first resolve which wallet is funded**:
  derive the address from each candidate (`STOCKPILE_FRONT_SEED`, `AGENT_SEED_PHRASE`) and
  check its `atestfet` balance on dorado-1; wire the funded seed to A.

**Files:** `.gitignore`, `agent-communication-layer/` (symlinks), repo-root `.env`
(symlink), `agent_base.py` (`seed_for`).

**Risk:** which seed-derived wallet is funded is **unconfirmed** (STOCKPILE vs
`AGENT_SEED_PHRASE`). Mitigated by the C3 balance-check/funding preflight + simulated fallback.

### C2 — Multi-Mac live vision (3 laptops, Hospital C on camera)

**Changes:**
- Standardize the per-laptop watcher: each Mac runs `capture_single.py --watch` (mode
  already exists — `run_watch`, `capture_single.py:182`) with `HOSPITAL_ID=hospital_{a,b,c}`
  and `REDIS_URL` pointed at the host. The Flask capture endpoint publishes a
  `vision:capture_request`; all watchers respond; counts land in shared Redis
  (`vision:latest`, `vision:image:{id}`, `hospital:{id}:inventory|surplus`).
- **Extend the live-camera path to Hospital C.** `capture_single.py:43`
  `VALID_HOSPITALS = {"hospital_a","hospital_b"}` — add `hospital_c`. `vision_sync.py`
  `HOSPITAL_MAP` (`redis/src/vision_sync.py:24`) similarly covers a/b — extend. If no
  third laptop is present, C falls back to seed (additive, never breaking).
- **Fix the `refresh_vision_inventory` double-`sleep`** (verified: `vision_inventory.py:94`
  *and* `:96` both `time.sleep(BAYMAX_VISION_WAIT_S)`) and remove any stale leftover import
  from the in-flight diff.

**Files:** `hardware/camera connection/capture_single.py`, `redis/src/vision_sync.py`,
`agent-communication-layer/vision_inventory.py`.

**Risk:** networking on demo day. Mitigation: Flask still does a local host-camera capture
for A; B/C are remote-best-effort and degrade to last-known/seed.

### C3 — Real agent-to-agent FET settlement via the Payment Protocol

The supply transfer ships goods **B/C → A**; money flows **A → B/C**. In Payment-Protocol
terms the **payee = seller** (B/C) and the **payer = buyer** (A). Roles verified in code
(`settlement.py:26`): `seller` receives `{CommitPayment, RejectPayment}`, `buyer` receives
`{RequestPayment, CompletePayment, CancelPayment}`.

**Role assignment (changes from today, where FRONT carried the seller role for the
human-chat case):**
- **B and C** keep the existing **seller** protocol (`settlement.build_payment_protocol()`,
  `role="seller"`, `settlement.py:368`): they send `RequestPayment` and verify the committed
  tx on-chain.
- **A (FRONT)** gets a **new buyer** protocol (`role="buyer"`): on `RequestPayment` it
  executes a **real on-chain FET transfer** and replies `CommitPayment(transaction_id=<real
  tx hash>)`. Modeled on `two_agent_payment_spike.py`'s buyer (which today replies a
  *placeholder* tx id), with the placeholder replaced by an actual `cosmpy
  LedgerClient.send_tokens(...)` (run in a worker thread, bounded, on
  `NetworkConfig.fetchai_stable_testnet()` / dorado-1 / `atestfet`).

**Settlement sequence (per transfer leg):**
```
negotiation reaches `settling` for leg (supplier S, qty q)
  FRONT → S : "settle now" signal (reuse settle_transfer hook orchestration)
  S (seller) → A (buyer) : RequestPayment(amount, recipient = S's fetch1… wallet, ref=pay-<req_id>-<S>)
  A (buyer) : send_tokens(A.wallet → S.wallet, amount atestfet)  [worker thread, bounded]
  A → S : CommitPayment(transaction_id = <real tx hash>)
  S : verify_payment_onchain_with_retry(tx)  [existing, bounded]
        VERIFIED   → S → A: CompletePayment → finalize_after_payment → `confirmed`
        NOT_FOUND  → S → A: CancelPayment   → fallback (see below)
  Confirmed transfer → XADD `transfers` stream; tx hash narrated to baymax:narration
```

**Auto-fallback (never hangs, never hard-fails):** if A's wallet is unfunded, the RPC is
unreachable, or `send_tokens` errors/times out, A narrates a **simulated settlement** and the
negotiation still drives to `confirmed` with a stub reference (`stub-settlement-<req_id>`).
The seller's verify already treats `INCONCLUSIVE` leniently (`PAYMENT_VERIFY_STRICT=false`,
`settlement.py:51`). The UI timeline labels real (`tx …`) vs simulated.

**Funding preflight (rewritten — no `FET_FUND`):** at Bureau startup, derive A's address from
the resolved seed and query its `atestfet` balance via cosmpy. If below threshold, fund it via
the **Fetch testnet faucet** — this is an **external/manual step** (web faucet or documented
CLI), *not* an in-repo tool — then narrate the balance so we know on stage whether the leg
will be real or simulated.

**Wiring:** register the new buyer protocol + the real-transfer settlement hook in
`run_dashboard_demo.py` (replacing the stub-only setup). The negotiation core
(`baymax_agents.settle_transfer`) stays the one-way integration point; the seller/buyer
protocols and `finalize_after_payment` machinery in `settlement.py` are reused/extended.
**Factor the on-chain transfer into one shared helper** `send_fet(payer_wallet, payee_addr,
amount) -> tx_hash | None` (bounded, worker-thread, fail-soft → `None`) so both the
buyer-commit here (C3) and the external-supplier order settlement (C7) call the same path.

**Files:** `settlement.py` (new buyer protocol + real `send_tokens` + balance check),
`run_dashboard_demo.py` (wire seller on B/C, buyer on A, register hook), `baymax_agents.py`
(settle orchestration), `agent_base.py` (register payer wallet at construction).

**Risk:** Payment-Protocol role/digest correctness (documented landmine). Mitigation: reuse
the verified seller protocol unchanged; model the buyer on the working spike; keep the spike
runnable as a regression check.

### C6 — Next.js front-end goes live (the judge-facing surface)

`web/lib/api.ts` already defines the seam: `BaymaxApi` = `getNetwork()`, `runCrisis(prompt,
onEvent, opts)` (streams `PipelineEvent`s, returns `CrisisResult`), `getForecast()`,
`decide(approve|order|reject)`, with `mock`/`live` modes and a documented intent that `live`
calls **Next route handlers that proxy `BAYMAX_API_URL`**, failing closed to mock. Today
`makeLiveApi()` just delegates to mock and the route handlers don't exist.

**Changes:**
- **Add Next route handlers** under `web/app/api/*` that proxy Flask `ui/app.py` (:5001)
  server-side, and implement `makeLiveApi()` to call them:
  - `runCrisis` → POST Flask `/api/crisis` (or `/api/negotiate`) to start, then **stream
    Flask `/api/narration` (SSE)** and translate each event into a `PipelineEvent`
    (`stageId ∈ detect|research|inventory|negotiate|settle`, `status`, `title`, `detail`,
    `data`, `at`); resolve the `CrisisResult` from the terminal state / `/api/state`.
  - `decide(approve|order|reject)` → Flask `/req/<rid>/approve|order|reject`.
  - `getNetwork` / `getForecast` → derive from Flask `/api/state` (keep the seed forecast
    if the backend doesn't supply one).
- **Add a live camera view** to `web/app/app`: a component that polls a route-handler proxy
  of Flask `/image/latest` (and per-hospital `vision:image:{id}` where available) as a
  refreshing **still frame** — the "MacBook live view" in the new UI.
- Configure `NEXT_PUBLIC_BAYMAX_MODE=live` + `BAYMAX_API_URL=http://<host>:5001`. The
  existing fail-closed-to-mock behavior stays, so a backend hiccup never blanks the demo.

**Reference asset:** the old `ui/templates/index.html` is a **working consumer** of
`/api/state` + `/api/narration` — use it to pin the exact event field mapping. **Keep it
until C6 is verified**, then retire just the template (NOT `ui/app.py`, which is the backend).

**Files:** `web/lib/api.ts` (`makeLiveApi`), `web/app/api/**` (new route handlers),
`web/components/app/*` (new camera card; wire `Dashboard`/`PipelinePanel`/`ForecastPanel`/
`ApprovalActions` to live data), `web/.env*`.

**Risk:** the narration→`PipelineEvent` field mapping is the fiddly part and depends on the
exact `/api/narration` payloads. Mitigation: pin payload shapes against the running Flask app
+ the old template during the plan; mock fallback covers gaps.

### C7 — Live Browserbase supplier search + buy (imperative)

**Already built.** `supplier_order.py` drives the vendor's **own on-site search** —
`page.act("type '{item}' into the site search box and submit")` → open first result → set
quantity → add to cart → go to `<origin>/cart` → extract a KEY:value cart summary
(`supplier_order.py:128-158`) — and captures a **Browserbase session replay URL**
(`:117`). `interfaces.order_from_supplier()` (`interfaces.py:476`) already delegates to it
when `BAYMAX_BROWSERBASE=1`, falling back to the deterministic mock on **any** failure.

**Changes (enable + wire, not build):**
- **Enable the dependency:** uncomment/install `stagehand==0.5.14` + `browserbase`
  (`requirements.txt:44-45`); set `BROWSERBASE_API_KEY`, `BROWSERBASE_PROJECT_ID`,
  `MODEL_API_KEY` (falls back to `ANTHROPIC_API_KEY`), `BAYMAX_SUPPLIER_URL`, and
  `BAYMAX_BROWSERBASE=1`.
- **Pick a Shopify-style vendor** for `BAYMAX_SUPPLIER_URL` — the extractor assumes the
  canonical `<origin>/cart` page and Shopify's async add-to-cart (`supplier_order.py:125-141`).
- **Trigger from the Next.js UI:** `decide("order")` → Flask `/req/<rid>/order` →
  `resume_after_admin_decision("order")` → `order_from_supplier` (Browserbase). The
  `sutures`→escalation (no internal surplus) path auto-routes here too.
- **Settle the order leg** with a **direct** `send_fet()` transfer (the shared C3 helper) —
  A pays `BAYMAX_SUPPLIER_WALLET` (or the FRONT wallet, narrated symbolic) on dorado-1. This
  is **NOT** the seller-initiated Payment Protocol handshake: an external vendor has only a
  wallet, no in-process agent to send `RequestPayment`, so A sends directly (fail-soft to
  the symbolic/simulated note on any failure).
- **Surface in the UI timeline:** vendor, cart total + currency, confirmation ref, the
  **Browserbase session replay URL** (strong demo moment — "watch the agent shop"), and the
  FET tx for the order settlement.

**Files:** `requirements.txt` (enable deps), `.env` (creds + supplier URL), wiring in
`run_dashboard_demo.py`/`front_agent.py` order path (already present — verify), UI surfacing
in `web/components/app/*` (C6).

**Risk:** live Browserbase reliability (anti-bot, latency, store-layout drift). Mitigation:
pre-test against a known-good Shopify store; the seam already falls back to mock on failure;
optionally keep a pre-recorded session replay as a demo backup.

### C4 — Demo-scale correctness

The in-flight "water-bottle desk demo" diff changed signatures in lockstep
(`reserve→threshold` across `capture_single.py` ↔ `redis_inventory.py`;
`_vision_to_negotiation_params` return shape) but was never exercised end-to-end.

**Changes:** verify the chain *camera count → reserve → shortfall → need → split plan* holds
at demo scale: a low live count at A reads as a shortfall; B/C seed/live surplus satisfies it
(and the `sutures` scenario escalates to the **live** external order, C7). Confirm
`DEMO_INVENTORY`/`DEMO_SURPLUS` (`seed_demo_data.py`) agree with camera-written and mock values
(`interfaces._build_demo_mock_inventory`).

**Files:** verification only (see C5); fixes land in `redis_inventory.py` / `interfaces.py` /
`seed_demo_data.py` if numbers disagree.

### C5 — One-command bring-up + verification harness

**Changes:**
- A single host launch path (seed Redis → start Flask backend → Flask auto-spawns the Bureau
  → start Next.js) and a one-liner for B/C laptops (`capture_single.py --watch`). Capture the
  exact commands in the repo (script or README section).
- Extend `wave6_crisis_dashboard_e2e_check.py` into a full **offline** E2E that exercises
  camera(mock counts) → Redis → negotiate → **settle (fallback path)** → **order (mock
  supplier)** → narration, so the whole chain is provable green before real
  hardware/wallets/Browserbase are plugged in.

**Files:** a bring-up script (e.g. `scripts/`), `wave6_*` (or a new
`wave7_live_integration_check.py`).

## 5. Non-goals (explicitly out of scope)

- **Multi-vendor supplier discovery/comparison** — we drive **one** configured vendor's
  on-site search only.
- ASI:One chat surface / Agentverse Mailbox (not needed; agents auto-settle).
- Live video/MJPEG streaming (still-frame captures only; the camera card polls
  `/image/latest`).
- Real CDC illness feed (stays mock); mainnet (testnet-only guardrail enforced).
- Durable/multi-worker backend state (run Flask single-worker; in-memory narration is
  acceptable for the demo).

## 6. Verification plan

1. **Offline E2E (C5):** the extended wave check passes with simulated settlement + mock
   supplier — proves camera→redis→negotiate→order→narration without hardware/funded
   wallets/Browserbase.
2. **Payment regression:** `two_agent_payment_spike.py` still green (role/digest intact).
3. **Live settlement smoke:** with funded A wallet + reachable dorado-1 RPC, a UI-triggered
   negotiation produces a **real tx hash** in the narration and the `transfers` stream;
   verify on a testnet explorer.
4. **Fallback smoke:** with the RPC blocked / wallet drained, the same flow completes as
   **simulated** and the UI labels it (no hang).
5. **Browserbase smoke (C7):** with creds + a real Shopify vendor URL, `decide("order")`
   produces a **real cart summary + session replay URL** in the UI; with `BAYMAX_BROWSERBASE`
   unset it cleanly mocks.
6. **Next.js live smoke (C6):** with `NEXT_PUBLIC_BAYMAX_MODE=live` + Flask up, `runCrisis`
   streams **real narration** into the web UI, `decide` hits the real endpoints, and the
   camera card shows `/image/latest`.
7. **Multi-Mac:** B and C watchers update their live counts in the UI vision cards; a low A
   count drives a negotiation that sources from B/C.

## 7. Sequencing (build order)

1. **C1** secrets/env/seed (everything live depends on it; resolve the funded wallet).
2. **C5 (offline harness first)** — establish the green baseline.
3. **C3** real settlement (buyer protocol + real transfer + fallback + funding preflight).
4. **C7** live Browserbase order (imperative; enable deps + creds + vendor URL + wire + verify).
5. **C6** Next.js front-end live wiring + camera card (the judge-facing surface).
6. **C2** multi-Mac vision + Hospital C + race/import fixes.
7. **C4** demo-scale verification + number reconciliation.
8. Full live rehearsal end-to-end on the Next.js UI.

## 8. Open risks & mitigations (summary)

| Risk | Mitigation |
| :-- | :-- |
| Funded-wallet identity unconfirmed (STOCKPILE vs `AGENT_SEED_PHRASE`) | Derive + balance-check both addresses in C1; funding preflight + simulated fallback |
| Payment role/digest landmine | Reuse verified seller protocol; model buyer on working spike; keep spike as regression |
| Browserbase live reliability (anti-bot/latency/layout) | Known-good Shopify store, pre-test, mock fallback, optional pre-recorded session backup |
| Next.js↔Flask narration mapping | Pin `/api/narration` payloads against old template during plan; fail-closed to mock |
| Demo-day networking (3 Macs) | Local host capture for A; B/C degrade to last-known/seed |
| Demo-scale math never tested together | C5 offline E2E gate before live |
| Flask in-memory narration lost on restart | Single-worker, no restart mid-demo |
