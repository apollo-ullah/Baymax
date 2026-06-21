# Baymax Live Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the full Baymax pipeline live end-to-end — multi-Mac cameras → shared Redis → Fetch.ai negotiation → real autonomous agent-to-agent FET testnet settlement → live Browserbase supplier order → the Next.js `web/` app — fail-closed to mock/simulated at every seam.

**Architecture:** Redis is the substrate; the Next.js app proxies the Flask backend (`ui/app.py`) which spawns the 3-agent Bureau (`run_dashboard_demo.py`). New code: a `send_fet()` cosmpy transfer + dashboard settlement hook (real FET, reply_to-independent), Hospital-C camera path, Next route handlers, and an offline E2E harness.

**Tech Stack:** Python 3.14 / uagents 0.25.2 / cosmpy (dorado-1 testnet), Flask, Redis (redis-stack), Stagehand 0.5.14 + Browserbase, Next.js 16.2.9 / React 19.2.4 / TypeScript.

## Global Constraints

- **Testnet only, fail-closed.** `FETCH_NETWORK=testnet`; cosmpy pinned to `NetworkConfig.fetchai_stable_testnet()` (chain `dorado-1`, denom `atestfet`). Never mainnet. `agent_base` raises on any other network.
- **`import agent_base` FIRST**, before constructing any `Agent`/`Protocol` (Python 3.14 event-loop side effect).
- **Never call sync cosmpy in an async handler** — always `await asyncio.to_thread(...)`.
- **Every seam fails closed** to mock/simulated and **never hangs** (bounded timeouts).
- **No pytest/lint/build step exists.** Verification = self-contained, self-exiting harness scripts (`wave*_e2e_check.py`): exit `0`=PASS, `1`=FAIL, `3`=watchdog timeout. Run from `agent-communication-layer/` with the sibling venv: `source ../adyan-agent-communication-layer/.venv/bin/activate`.
- **Narration payload is frozen at 4 keys:** `{req_id, state, detail, final}`. Never add/expect others.
- **The `BaymaxApi` TypeScript contract is fixed** (`web/lib/api.ts`); route handlers conform to it. Live mode falls back to mock on ANY error except `AbortError` (which must propagate).
- **Secrets live in the sibling dir** `../adyan-agent-communication-layer/` (`.env`, `private_keys.json`, `.venv`). Stagehand pinned `==0.5.14`. Run Flask single-worker.

---

## ⚠️ C3 design note (read before Phase 3) — discovered during interface extraction

The interface dig surfaced two facts not visible when the settlement mechanism was chosen:

1. **`settle_transfer` (`baymax_agents.py:903`) fires the registered hook ONLY when `neg['reply_to']` is truthy.** The dashboard path sets `reply_to=None`, so today it ALWAYS returns the stub ref — no `RequestPayment` is ever sent.
2. **There is no autonomous agent-to-agent FET-send anywhere.** `settlement.py`'s Payment Protocol is a buyer↔seller *chat-wallet* handshake whose buyer is a human ASI:One wallet that signs `CommitPayment`. The on-chain *send* is delegated to that human wallet; the repo only *verifies* (`query_tx`), never *sends*.

**Therefore "agent-to-agent FET, automatic, no human" is implemented as a direct cosmpy `send_tokens` from A's agent wallet to each supplier's agent wallet** — this IS the autonomous agent-to-agent payment (the Payment Protocol's human-signing handshake cannot run without a human). The real on-chain **tx hash is the Fetch.ai proof and is identical either way.** The existing Payment Protocol seller path is left intact for any future ASI:One human demo. Task **C3.5 (optional)** layers the Payment-Protocol message exchange on top for Fetch-native flavor; it can be skipped without affecting the real settlement.

---

## Phase / build order

`Phase 1 (C1)` → `Phase 2 (C5 offline harness)` → `Phase 3 (C3 real settlement)` → `Phase 4 (C7 Browserbase)` → `Phase 5 (C6 Next.js live)` → `Phase 6 (C2 multi-Mac + fixes)` → `Phase 7 (C4 demo-scale)` → live rehearsal. Each phase ends green before the next starts.

---

## Phase 1 — C1: Secrets & environment wiring

### Task C1.1: Gitignore the key file, then symlink secrets into the agent dir

**Files:**
- Modify: `/Users/adyan/Documents/GitHub/CalHax/.gitignore`
- Create (symlinks): `agent-communication-layer/.env`, `agent-communication-layer/private_keys.json`, repo-root `.env`

**Interfaces:**
- Produces: `agent-communication-layer/.env` resolvable by `agent_base.load_dotenv(Path(__file__).parent/".env")`; repo-root `.env` resolvable by `ui/app.py:43` `load_dotenv(parents[1]/".env")`.

- [ ] **Step 1: Add `private_keys.json` to `.gitignore`** (it is NOT currently covered). Append a line `private_keys.json` under the existing ignores. Verify: `git check-ignore private_keys.json` prints the path.
- [ ] **Step 2: Symlink secrets** (run from repo root):
```bash
ln -sf ../adyan-agent-communication-layer/.env agent-communication-layer/.env
ln -sf ../adyan-agent-communication-layer/private_keys.json agent-communication-layer/private_keys.json
ln -sf adyan-agent-communication-layer/.env .env   # repo-root .env for ui/app.py
```
- [ ] **Step 3: Verify** secrets resolve: `cd agent-communication-layer && ../adyan-agent-communication-layer/.venv/bin/python -c "import agent_base; print('FET_NETWORK', agent_base.FET_NETWORK)"` → prints `FET_NETWORK testnet` (and no missing-`.env` warning).
- [ ] **Step 4: Verify** `git status` shows the symlinks are ignored or are intended; confirm `private_keys.json` does NOT appear as untracked. Commit `.gitignore` only:
```bash
git add .gitignore && git commit -m "chore: gitignore private_keys.json before symlinking secrets"
```

### Task C1.2: Add `STOCKPILE_*`/`AGENT_SEED_PHRASE` fallback in `seed_for()` + funded-wallet resolution

**Files:**
- Modify: `agent-communication-layer/agent_base.py:94-96` (`seed_for`)
- Create: `agent-communication-layer/check_wallets.py` (one-shot balance probe)

**Interfaces:**
- Consumes: `FACILITIES[f]["seed_env"]` ∈ {`BAYMAX_FRONT_SEED`,`BAYMAX_HOSP_B_SEED`,`BAYMAX_HOSP_C_SEED`}; `.env` provides `STOCKPILE_FRONT_SEED`/`STOCKPILE_HOSP_B_SEED`/`STOCKPILE_HOSP_C_SEED` + `AGENT_SEED_PHRASE`.
- Produces: deterministic funded addresses for A/B/C via `address_for(facility)`.

- [ ] **Step 1:** Change `seed_for` to add the STOCKPILE fallback (exact):
```python
def seed_for(facility: str) -> str:
    """The agent seed for a facility: BAYMAX_* env, then STOCKPILE_* alias, else dev fallback."""
    env_name = FACILITIES[facility]["seed_env"]                      # e.g. BAYMAX_FRONT_SEED
    return (os.getenv(env_name)
            or os.getenv(env_name.replace("BAYMAX_", "STOCKPILE_"))  # .env legacy names
            or _DEV_SEEDS[facility])
```
- [ ] **Step 2:** Write `check_wallets.py` — derive `address_for("Hospital A"/"B"/"C")` AND the address from `AGENT_SEED_PHRASE`, then query each `atestfet` balance via `cosmpy LedgerClient(NetworkConfig.fetchai_stable_testnet()).query_bank_balance(addr, "atestfet")` in a try/except (print balance or "unreachable"). Import `agent_base` first.
- [ ] **Step 3: Run it:** `python check_wallets.py` → prints each facility address + balance. **HUMAN INPUT:** record which address is funded (this is the demo's payer = Hospital A). If none funded, note it (Phase 3 fails closed to simulated).
- [ ] **Step 4: Commit:** `git add agent_base.py check_wallets.py && git commit -m "feat(agents): seed_for STOCKPILE fallback + wallet balance probe"`

---

## Phase 2 — C5: Offline E2E harness (green baseline)

### Task C5.1: `wave7_live_integration_check.py` — camera(mock)→redis→negotiate→settle(fallback)→order(mock)→narration

**Files:**
- Create: `agent-communication-layer/wave7_live_integration_check.py`
- Reference (copy the pattern, do NOT import): `wave5_crisis_e2e_check.py` (hook-free construction), `wave6_crisis_dashboard_e2e_check.py` (sink + dashboard bus stub), `wave4_dashboard_e2e_check.py` (watchdog/exit).

**Interfaces:**
- Consumes: `agent_base.build_hospital_agent`, `build_chat_protocol`, `create_text_chat`, `REQUESTER`; `baymax_agents.{attach_front_handlers, attach_hospital_handlers, register_narration_sink, start_negotiation, NEGOTIATIONS, resume_after_admin_decision}`; narration payload `{req_id, state, detail, final}`.
- Produces: a self-exiting check (0/1/3).

**Critical gotchas (from interface extraction):**
- Set ALL `os.environ` keys BEFORE importing `agent_base`; import `agent_base` FIRST.
- Do NOT register a settlement hook and do NOT build via `run_front` → forces the stub/fallback settle path (`settle_transfer` returns `stub-settlement-<req_id>`, chain still reaches `confirmed`).
- The HARNESS owns exit: pop/clear `BAYMAX_EXIT_WHEN_DONE`; add an `on_interval` watchdog that `os._exit(3)` past a tick cap; `sys.stdout.flush()` before every `os._exit`.
- Clear the AWAITING_APPROVAL gate: it is REAL on `source='dashboard'`. Drive a decision via `resume_after_admin_decision(ctx, req_id, 'approve')` once a neg is `state==AWAITING_APPROVAL`.
- Leave `BAYMAX_BROWSERBASE` unset → `order_from_supplier` returns the deterministic mock.

- [ ] **Step 1:** Header + env (before imports): `BAYMAX_REDIS="0"` (mock inventory, no Redis dependency for the offline gate), `BAYMAX_DEMO_MODE="1"`, `BAYMAX_OFFER_TIMEOUT="3"`, `BAYMAX_SPARSE_NARRATION="0"`, pop `BAYMAX_EXIT_WHEN_DONE`. Then `import agent_base` first.
- [ ] **Step 2:** Register a module-level narration sink that appends every payload to `MILESTONES = []` and prints `[NARRATION] {state}: {detail}`. Build A/B/C via `build_hospital_agent`; `attach_front_handlers(front)`; `attach_hospital_handlers(hb,"Hospital B")`, `(hc,"Hospital C")`. Include chat proto on front.
- [ ] **Step 3:** `@front.on_event("startup")` → `await start_negotiation(ctx, "IV fluids", reply_to=None, source="dashboard")`.
- [ ] **Step 4:** `@front.on_interval(0.5)` watchdog: (a) if any neg `state==AWAITING_APPROVAL and not done` → `await resume_after_admin_decision(ctx, rid, "approve")`; (b) when a milestone has `final=True`, assert the milestone `state` set includes `shortfall_detected`, `evaluating`, `confirmed` (the split path) — print `RESULT: PASS`/`FAIL`, flush, `os._exit(0|1)`; (c) tick cap → `os._exit(3)`.
- [ ] **Step 5: Run + verify:** `python wave7_live_integration_check.py` → ends `RESULT: PASS`, exit 0.
- [ ] **Step 6: Commit:** `git add wave7_live_integration_check.py && git commit -m "test(wave7): offline E2E negotiate→settle(fallback)→narration"`

### Task C5.2: Extend wave7 with the mock-order branch

**Files:** Modify `wave7_live_integration_check.py`.

- [ ] **Step 1:** Add a second pass (or a sutures scenario) that drives `resume_after_admin_decision(ctx, rid, "order")` at the gate, asserting the milestone states include `ordering` then `ordered`, and that `NEGOTIATIONS[rid]["order"]` is a `SupplierOrder` with `confirmation_ref` starting `MOCK-PO-`.
- [ ] **Step 2: Run + verify** PASS. **Commit:** `git commit -am "test(wave7): cover mock external-order branch"`

---

## Phase 3 — C3: Real autonomous agent-to-agent FET settlement

### Task C3.1: `send_fet()` — bounded cosmpy transfer helper (shared by C3 + C7)

**Files:** Modify `agent-communication-layer/settlement.py` (add helper + payer-wallet registry).

**Interfaces:**
- Produces: `register_payer_wallet(agent) -> str` (stores the full `LocalWallet`); `resolve_payer_wallet() -> LocalWallet | None`; `async def send_fet(payee_addr: str, amount_fet: str, *, denom="atestfet") -> str | None` (returns tx hash or `None` on any failure).
- Consumes: `cosmpy.aerial.client.{LedgerClient,NetworkConfig}`, `cosmpy.aerial.wallet.LocalWallet`.

**Gotchas:** never block the loop — wrap the sync cosmpy send in `asyncio.to_thread`; bound it; testnet config pinned; return `None` (never raise) on unfunded/RPC/any error so callers fall back to simulated.

- [ ] **Step 1:** Add a payer-wallet registry (mirrors `register_recipient_wallet` but keeps the wallet OBJECT, captured at construction where `agent.wallet` exists):
```python
_PAYER_WALLET = {"wallet": None}  # module-global; the FRONT agent's LocalWallet

def register_payer_wallet(agent) -> str:
    """Capture the payer (FRONT) agent's LocalWallet at construction for autonomous sends."""
    _PAYER_WALLET["wallet"] = agent.wallet
    return str(agent.wallet.address())

def resolve_payer_wallet():
    return _PAYER_WALLET["wallet"]
```
- [ ] **Step 2:** Add the transfer helper:
```python
def _send_fet_sync(payee_addr: str, amount_fet: str, denom: str) -> str | None:
    from cosmpy.aerial.client import LedgerClient, NetworkConfig
    wallet = resolve_payer_wallet()
    if wallet is None or not payee_addr.startswith("fetch1"):
        return None
    try:
        client = LedgerClient(NetworkConfig.fetchai_stable_testnet())
        # FET has 18 decimals; amount_fet is a decimal FET string → atestfet integer.
        amount_atestfet = int(round(float(amount_fet) * 1e18))
        tx = client.send_tokens(payee_addr, amount_atestfet, denom, wallet)
        tx.wait_to_complete()
        return tx.tx_hash if tx.response.is_successful() else None
    except Exception:
        return None  # unfunded / RPC down / any error → caller simulates

async def send_fet(payee_addr: str, amount_fet: str, *, denom: str = "atestfet") -> str | None:
    """Autonomous on-chain FET transfer payer→payee on dorado-1. Bounded, fail-soft → None."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_send_fet_sync, payee_addr, amount_fet, denom),
            timeout=float(os.getenv("BAYMAX_SEND_TIMEOUT", "25")),
        )
    except Exception:
        return None
```
- [ ] **Step 3: Verify import-clean:** `python -c "import settlement; print(hasattr(settlement,'send_fet'))"` → `True`. (Cannot send without a funded wallet; covered in C3.4 smoke.)
- [ ] **Step 4: Commit:** `git commit -am "feat(settlement): autonomous send_fet transfer + payer-wallet registry"`

### Task C3.2: Dashboard settlement hook (reply_to-independent) in `baymax_agents.py`

**Files:** Modify `agent-communication-layer/baymax_agents.py` (`settle_transfer` ~890, add `register_direct_settlement_hook`).

**Interfaces:**
- Produces: `def register_direct_settlement_hook(fn) -> None` where `fn: async fn(ctx, req_id, plan) -> str` (returns a settlement ref, e.g. joined tx hashes or `simulated-<req_id>`).
- Consumes: `_SettlementPlan.allocations` (each `_Leg` has `.offerer:str`, `.quantity:int`); `NEGOTIATIONS[req_id]`.

**Gotcha:** keep the one-way dependency — the core must NOT import `settlement` at module load; the hook is injected from `run_dashboard_demo.py`. The existing reply_to-gated `_SETTLEMENT_HOOK` stays untouched (for the chat/human path).

- [ ] **Step 1:** Add a module global `_DIRECT_SETTLEMENT_HOOK = None` and `register_direct_settlement_hook(fn)`.
- [ ] **Step 2:** In `settle_transfer`, BEFORE the existing reply_to-gated branch, add: if `_DIRECT_SETTLEMENT_HOOK is not None and not neg.get("reply_to")` → `ref = await _DIRECT_SETTLEMENT_HOOK(ctx, req_id, plan)`; write the confirmed legs to the transfers stream with `tx_id=ref` (reuse the existing `_log_confirmed_transfers`/`write_transfer_record` path with `settlement_status` reflecting real vs simulated), and `return ref`. The terminal CONFIRMED milestone still flows through the normal post-`settle_transfer` path (stub-style: immediate CONFIRMED), so timing stays simple.
- [ ] **Step 3: Verify** the offline gate still passes (no hook registered there): `python wave7_live_integration_check.py` → PASS (the new branch is dormant without a registered direct hook).
- [ ] **Step 4: Commit:** `git commit -am "feat(agents): reply_to-independent direct settlement hook for dashboard path"`

### Task C3.3: Implement + register the direct-transfer hook; capture wallets in `run_dashboard_demo.py`

**Files:** Modify `agent-communication-layer/settlement.py` (add `settle_via_direct_transfer`), `agent-communication-layer/run_dashboard_demo.py` (wire it).

**Interfaces:**
- Produces: `async def settle_via_direct_transfer(ctx, req_id, plan) -> str`.
- Consumes: `send_fet`, `_amount_for_total`, `register_recipient_wallet` (B/C payees), `register_payer_wallet` (A), `agent_base.ADDRESS_TO_FACILITY`/facility→wallet mapping.

- [ ] **Step 1:** In `settlement.py`, add `settle_via_direct_transfer`: for each leg in `plan.allocations`, resolve the supplier facility (`leg.offerer`) → its registered fetch1 wallet; `amount = _amount_for_total(leg.quantity)`; `tx = await send_fet(payee_wallet, amount)`. Narrate per leg via the core's narration (return value carries the refs). Build ref = `";".join(tx or f"simulated-{leg.offerer}")`. Return it.
```python
async def settle_via_direct_transfer(ctx, req_id, plan) -> str:
    refs = []
    for leg in getattr(plan, "allocations", []) or []:
        payee = _facility_wallet(leg.offerer)            # fetch1… from register_recipient_wallet
        amount = _amount_for_total(getattr(leg, "quantity", 0))
        tx = await send_fet(payee, amount) if payee else None
        ctx.logger.info(f"[settle] {leg.offerer} {amount} FET → {payee}: {tx or 'SIMULATED'}")
        refs.append(tx or f"simulated-{req_id}-{leg.offerer}")
    return ";".join(refs) if refs else f"stub-settlement-{req_id}"
```
  Add `_facility_wallet(facility)` resolving the supplier wallet from a `{facility: fetch1addr}` dict populated when B/C call `register_recipient_wallet`.
- [ ] **Step 2:** In `run_dashboard_demo.build_bureau()`, after building A/B/C: `settlement.register_payer_wallet(front)`; `settlement.register_recipient_wallet(hb)`/`(hc)` and record their facility→wallet; `sp.register_direct_settlement_hook(settlement.settle_via_direct_transfer)`. (Import `settlement` here only — keeps core decoupled.)
- [ ] **Step 3:** Add a startup funding-balance narration: log A's `atestfet` balance so the operator knows real vs simulated on stage.
- [ ] **Step 4: Verify (offline, simulated):** with no funded wallet / `BAYMAX_REDIS=0`, run the Bureau headless: `BAYMAX_EXIT_WHEN_DONE=1 BAYMAX_ITEM="IV fluids" python run_dashboard_demo.py` is interactive; instead extend wave7 to register the direct hook and assert the settlement ref appears (`simulated-…` offline). Run wave7 → PASS.
- [ ] **Step 5: Commit:** `git commit -am "feat(settlement): direct agent-to-agent FET settlement wired on dashboard path"`

### Task C3.4: Live settlement smoke + fallback smoke

**Files:** none (verification task; needs funded wallet from C1.2).

- [ ] **Step 1 (live):** Seed Redis (demo), bring up the stack, trigger a negotiation from the dashboard; confirm the narration shows a real `tx <hash>` and the `transfers` stream entry carries it; verify the hash on a dorado-1 explorer. (Requires a funded Hospital A wallet.)
- [ ] **Step 2 (fallback):** Drain/point the RPC at an unreachable host (`FETCHAI_TESTNET_RPC` or block network); re-run; confirm settlement completes as `simulated-…`, the negotiation reaches CONFIRMED, no hang.
- [ ] **Step 3:** Record results in the spec's verification section.

### Task C3.5 (OPTIONAL): Payment-Protocol message exchange for Fetch-native flavor

**Files:** Modify `settlement.py` (add `build_buyer_payment_protocol`), `run_dashboard_demo.py`, `baymax_agents.py` (supplier-initiation seam).

**Gotcha:** This is additive showmanship — the real money already moved in C3.3. Only build if Phase 3 is green and there's time. On a fallback (send_fet→None), the buyer commits a `SIMULATED-<ref>` tx and the seller's `on_commit` treats a `SIMULATED-` prefix as SKIPPED (3-line change) so the handshake completes without a real chain hit.

- [ ] **Step 1:** Add `build_buyer_payment_protocol()` (`role="buyer"`): `on_message(RequestPayment)` → `tx = await send_fet(msg.recipient, msg.accepted_funds[0].amount)`; reply `CommitPayment(funds=msg.accepted_funds[0], recipient=msg.recipient, transaction_id=tx or f"SIMULATED-{msg.reference}", reference=msg.reference)`.
- [ ] **Step 2:** Attach buyer proto to A, seller proto (`build_payment_protocol`) to B/C in `run_dashboard_demo`; have the direct hook also `ctx.send` each supplier a request-payment trigger. Treat `SIMULATED-` as SKIPPED in `settlement.on_commit`.
- [ ] **Step 3: Verify** `two_agent_payment_spike.py` still green (role/digest intact): `PAYMENT_VERIFY_ONCHAIN=false python two_agent_payment_spike.py` → SPIKE SUCCESS. **Commit.**

---

## Phase 4 — C7: Live Browserbase supplier order

### Task C7.1: Enable the dependency

**Files:** Modify `agent-communication-layer/requirements.txt:44-45`; `.env` (creds).

- [ ] **Step 1:** Uncomment `stagehand==0.5.14` and `browserbase`; `pip install -r requirements.txt` in the sibling venv.
- [ ] **Step 2: HUMAN INPUT** — set in `.env`: `BROWSERBASE_API_KEY`, `BROWSERBASE_PROJECT_ID`, `BAYMAX_SUPPLIER_URL` (a real Shopify-style store; per memory, `saveritemedical.com` worked), `MODEL_API_KEY` (or rely on `ANTHROPIC_API_KEY`).
- [ ] **Step 3: Verify import:** `python -c "import supplier_order; print(supplier_order.browserbase_enabled())"` (False until `BAYMAX_BROWSERBASE=1`). **Commit** requirements.txt.

### Task C7.2: Order-leg settlement via direct `send_fet` + verify the existing order path

**Files:** Modify `baymax_agents.py` `_settle_order` (~930) / `run_dashboard_demo.py`.

**Interface gotcha:** `_settle_order` is also reply_to-gated. Mirror C3.2: add a reply_to-independent direct order-settlement that does `send_fet(BAYMAX_SUPPLIER_WALLET or A's own wallet, _amount_for_total(order.quantity))`, narrated symbolic. NOT the seller-initiated handshake (no supplier agent exists).

- [ ] **Step 1:** Add `register_direct_order_settlement_hook` + a `settle_order_via_direct_transfer(ctx, req_id, order)` that sends FET to `os.getenv("BAYMAX_SUPPLIER_WALLET")` (or A's wallet), returns the tx/ simulated ref; wire it in `run_dashboard_demo`.
- [ ] **Step 2: Verify (mock order, offline):** wave7 order branch still PASS.
- [ ] **Step 3 (live):** `BAYMAX_BROWSERBASE=1` + creds; drive `decide("order")` end-to-end; confirm `SupplierOrder` carries a real `live_view_url` (Browserbase session replay) + cart total, and the order settlement tx. **Commit.**

---

## Phase 5 — C6: Next.js front-end goes live

### Task C6.1: `web/app/api/crisis/route.ts` — start + SSE→PipelineEvent stream

**Files:** Create `web/app/api/crisis/route.ts`; modify `web/lib/api.ts` `makeLiveApi()`.

**Interfaces (exact, from `web/lib/api.ts`):**
- `runCrisis(prompt, onEvent: (e: PipelineEvent)=>void, opts?: {signal?: AbortSignal; speed?: number}): Promise<CrisisResult>`.
- `PipelineEvent = { stageId: "detect"|"research"|"inventory"|"negotiate"|"settle"; status: "pending"|"active"|"done"|"failed"; title: string; detail?: string; data?: Record<string,unknown>; at: number }`.
- `CrisisResult = { prompt; item; needQty; target; summary; legs: {from,to,item,qty}[]; settlement: {kind:"transfer"|"order"; reference; amountFet:number; network:string}; awaitingApproval:boolean }`.
- Flask contract: `POST /api/crisis {crisis_text,requester?,region?} → {ok,crisis_text}`; `GET /api/narration` SSE frames `data: {req_id,state,detail,final}\n\n` (replays full buffer, no event ids).

**Narration `state` → `PipelineEvent.stageId` mapping (frozen states):**
| narration `state` | stageId | status |
| :-- | :-- | :-- |
| `researching`/`researched` | research | active/done |
| `shortfall_detected` | detect | active |
| `requesting`/`collecting_offers` | inventory | active |
| `evaluating` | negotiate | active |
| `proposing` | negotiate | active |
| `awaiting_approval` | settle | active (set `awaitingApproval`) |
| `settling` | settle | active |
| `confirmed`/`ordered` | settle | done |
| `failed` | settle | failed |

- [ ] **Step 1:** Route handler `POST`: read `{prompt}`, POST Flask `${BAYMAX_API_URL}/api/crisis` with `{crisis_text: prompt}`. Then open `GET ${BAYMAX_API_URL}/api/narration` and re-stream as NDJSON/SSE `ReadableStream`, deduping replayed frames (track seen `req_id|state|detail`), translating each to a `PipelineEvent` (`at = Date.now()-start`). Close when a `final:true` frame arrives; emit a trailing JSON `CrisisResult` derived from the final narration + `/api/state` (legs from `transfers`, settlement ref/network).
- [ ] **Step 2:** Implement `makeLiveApi().runCrisis` to `fetch('/api/crisis', {method:'POST', body, signal: opts?.signal})`, read the stream, call `onEvent` per `PipelineEvent`, resolve the `CrisisResult`. Wrap in try/catch → on any non-Abort error delegate to `mock.runCrisis`; re-throw `AbortError`.
- [ ] **Step 3: Verify:** `cd web && npm run build` (clean TS). With Flask up + `NEXT_PUBLIC_BAYMAX_MODE=live BAYMAX_API_URL=http://localhost:5001`, `npm run dev`, run a prompt on `/app` → real narration streams into the pipeline log. Point `BAYMAX_API_URL` at a dead host → UI still completes via mock (fail-closed).
- [ ] **Step 4: Commit:** `git -C web add -A && git commit -m "feat(web): live crisis route handler + SSE→PipelineEvent stream"`

### Task C6.2: `decide` + `forecast` + `network` route handlers

**Files:** Create `web/app/api/decide/route.ts`, `web/app/api/forecast/route.ts`, `web/app/api/network/route.ts`; modify `makeLiveApi()`.

**Interfaces:** `decide(d:"approve"|"order"|"reject"): Promise<{ok:boolean;message:string}>`; `getForecast(): Promise<Forecast>`; `getNetwork(): Promise<MeshNetwork>`. Flask `GET /req/<rid>/{approve,order,reject}` is **GET, returns HTML** (keep GET) and needs the `rid` — capture it server-side from the crisis run (module-level `lastReqId` or a session cookie set during `runCrisis`, since `decide()` takes no id).

- [ ] **Step 1:** `decide` route: GET Flask `/req/${lastReqId}/${decision}`; return `{ok:true, message:"…"}`. `forecast` route: derive `Forecast` from `GET /api/state` `forecast`/`reasoning` (fall back to mock's seed forecast shape). `network`: derive `MeshNetwork` from `/api/state` inventory (or return mock `defaultNetwork`).
- [ ] **Step 2:** Wire all three into `makeLiveApi()` with fail-closed-to-mock.
- [ ] **Step 3: Verify** `npm run build`; live `/app` Approve/Order/Reject hit the real endpoints and resume the negotiation. **Commit.**

### Task C6.3: Live camera card

**Files:** Create `web/components/app/CameraCard.tsx` + a `web/app/api/image/route.ts` proxy; wire into `web/app/app/page.tsx`.

- [ ] **Step 1:** `image` route proxies Flask `GET /image/latest` (and `/image/hospital/<hid>`), returning the JPEG bytes. `CameraCard` polls it every 3s with a cache-buster (`?t=`), shows the still as the "MacBook live view".
- [ ] **Step 2: Verify** `npm run build`; live card shows the latest capture. **Commit.**

---

## Phase 6 — C2: Multi-Mac vision + Hospital C + fixes

### Task C2.1: Extend the camera path to Hospital C

**Files:** Modify `hardware/camera connection/capture_single.py:43` (`VALID_HOSPITALS`), `redis/src/vision_sync.py:24` (`HOSPITAL_MAP`).

- [ ] **Step 1:** `VALID_HOSPITALS = {"hospital_a","hospital_b","hospital_c"}`; add `"hospital_c":"hospital_c"` to `vision_sync.HOSPITAL_MAP`.
- [ ] **Step 2: Verify (keyless):** `HOSPITAL_ID=hospital_c python "hardware/camera connection/capture_single.py" --count 2` writes `hospital:hospital_c:inventory`/`surplus`/`vision:latest`; confirm via `redis-cli HGETALL hospital:hospital_c:inventory`.
- [ ] **Step 3: Commit.**

### Task C2.2: Fix the `refresh_vision_inventory` double-sleep + stale import

**Files:** Modify `agent-communication-layer/vision_inventory.py:93-96`.

- [ ] **Step 1:** Remove the duplicate `time.sleep(BAYMAX_VISION_WAIT_S)` so a remote-subscribed scan waits once (~2.5s), not twice. Remove any unused `write_inventory` import left from the in-flight diff.
- [ ] **Step 2: Verify** `python -c "import vision_inventory"` clean; `wave7` still PASS. **Commit.**

### Task C2.3: Multi-Mac bring-up scripts + README

**Files:** Create `scripts/host_up.sh` (seed Redis → `python ui/app.py`), `scripts/camera_watch.sh` (`HOSPITAL_ID=$1 REDIS_URL=$2 python "hardware/camera connection/capture_single.py" --watch`); README section.

- [ ] **Step 1:** Write the two scripts + a README block with the exact Tailscale/LAN `REDIS_URL` the B/C laptops use.
- [ ] **Step 2: Verify** host script brings up Redis + Flask + Bureau; a second machine's watch script updates its vision card. **Commit.**

---

## Phase 7 — C4: Demo-scale correctness

### Task C4.1: Reconcile camera→reserve→shortfall→split math

**Files:** Verify; fix in `redis_inventory.py`/`interfaces.py`/`seed_demo_data.py` only if numbers disagree.

**Reference (from extraction):** demo scale — `BAYMAX_DEMO_TARGET=3` (A on-hand target), `BAYMAX_DEMO_RESERVE=1` (B/C floor), `BAYMAX_DEMO_CAPACITY=4`. `capture_single.persist` stores `threshold` under the key `reserve`; `redis_get_inventory` priority: record `reserve`>0 wins as `safety_threshold`. A reads short when `qty<3`; B/C offer `qty-1`.

- [ ] **Step 1:** Bring up demo Redis: `docker compose -f redis/docker-compose.redis.yml up -d`; `(cd redis/src && BAYMAX_DEMO_MODE=1 REDIS_URL=redis://localhost:6379 python seed_demo_data.py)`. Set A's saline to 1 via `capture_single --count 1`. Run `BAYMAX_REDIS=1 BAYMAX_DEMO_MODE=1 BAYMAX_ITEM="saline" BAYMAX_EXIT_WHEN_DONE=1 python baymax_agents.py`.
- [ ] **Step 2:** Confirm the log shows A shortfall (need=2), B/C offer, split plan covers it, settles. Verify `interfaces.get_inventory("Hospital A","saline")` logs `qty=1 safety=3 spare=…`.
- [ ] **Step 3:** If numbers disagree (off by reserve), fix `_build_demo_mock_inventory`/`DEMO_INVENTORY`/`DEMO_SURPLUS` to agree. **Commit.**

---

## Self-review

**Spec coverage:** C1✓(C1.1-1.2) C2✓(C2.1-2.3) C3✓(C3.1-3.5) C4✓(C4.1) C5✓(C5.1-5.2) C6✓(C6.1-6.3) C7✓(C7.1-7.2). Non-goals (ASI:One/Mailbox, video streaming, CDC feed, multi-vendor discovery, durable state) intentionally untouched.

**Placeholder scan:** new-code tasks (C3.1, C3.3, C5.1, C6.1) carry real signatures/code; wiring tasks cite exact files:lines + symbols from interface extraction. No "TBD"/"add error handling"/"similar to".

**Type consistency:** `send_fet(payee_addr, amount_fet)`, `register_payer_wallet`/`resolve_payer_wallet`, `register_direct_settlement_hook`, `settle_via_direct_transfer(ctx,req_id,plan)`, `PipelineEvent`/`CrisisResult`/`BaymaxApi` used verbatim from `web/lib/api.ts`; narration payload `{req_id,state,detail,final}` consistent across C5/C6.
