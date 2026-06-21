# Design: Admin Approval Gate + External Supplier Order (Wave 3)

**Date:** 2026-06-20
**Status:** Approved (design); pending implementation plan
**Scope:** `adyan-agent-communication-layer/` — the Baymax Fetch.ai uAgents negotiation system

---

## 1. Summary

Add two capabilities to the existing autonomous hospital supply-negotiation system:

- **(A) External supplier order branch.** An alternative to inter-hospital trade: place an order with an outside vendor via Browserbase (Stagehand/Playwright), then settle a FET testnet payment representing the purchase. Used when no hospital can supply the item, or for proactive (non-emergency) restocking.
- **(B) Human-in-the-loop admin approval.** Today the only HITL is the ASI:One user signing the FET payment. We add an admin who must approve the **trade before it commits**, and who may choose to **order instead of pay**.

Both features are built behind seams that mirror the existing `get_inventory` / `rank_offers` / `register_settlement_hook` idiom, so all offline harnesses stay green without API keys or network.

### Decisions locked during brainstorming

| Decision | Choice |
| :-- | :-- |
| Admin surface | Inside ASI:One chat (no new web UI). The admin **is** Hospital A's chat user. |
| Admin scope | **Hybrid** — Hospital A's admin approves the plan/decision; Hospitals B/C (headless) auto-offer but expose a per-agent "confirm release" seam, env-gated to auto-approve + notify now. |
| When "order" is offered | **Always alongside pay** when a plan exists; **order-only** when no hospital can offer. |
| Browserbase target | **Seam + mock now, real later** — deterministic mock impl plus a Browserbase impl wired at deploy time, fail-closed to mock. |

---

## 2. Architecture (Approach A: pause-and-resume in the negotiation core)

The negotiation core halts after evaluation, narrates the decision to the admin in chat, and resumes on a chat reply. The "order" path is a sibling terminal branch. No new processes are required for the demo.

New flow (changes in **bold**):

```
shortfall → requesting → collecting_offers → evaluating
   → AWAITING_APPROVAL                         ← halt, narrate options to admin, arm watchdog
        ├─ "approve" → proposing → (B/C confirm) → settling → confirmed   [existing trade path]
        ├─ "order"   → ORDERING → (supplier seam) → settling → ORDERED     [new supplier path]
        ├─ "reject"  → failed
        └─ (timeout) → failed                  ← watchdog auto-fail, no zombie

Proactive entry (no shortfall):
intent "order N <item>" → start_order() → ORDERING → settling → ORDERED   [bypasses shortfall guard]
```

**Rejected alternatives:** (B) dedicated admin uAgents per hospital — too heavyweight, doesn't fit the chat surface; (C) gate only at payment time — legs already committed at B/C before the human decides, violates "approve before trade."

---

## 3. Components

### 3.1 `protocol.py` — new negotiation states (documented contract edit)

Add additive values to `NegotiationState`: `AWAITING_APPROVAL`, `ORDERING`, `ORDERED`.

`NegotiationState` is used **only** in the in-process `NEGOTIATIONS` dict — it is **not** a field on any `uagents.Model` wire message, so adding values causes **no Agentverse schema-digest drift**. However, `protocol.py` is the frozen single source of truth per `CLAUDE.md`; this addition is therefore made deliberately and documented here as an intentional contract edit. No wire models (`SupplyRequest`, `SupplyOffer`, `TransferProposal`, `TransferAccept`, `TransferReject`) change.

### 3.2 `baymax_agents.py` — the decision gate

- **`_evaluate()` halts before proposing.** After `rank_offers` produces the plan, store the plan + computed FET cost in `neg`, set state `AWAITING_APPROVAL`, set `neg["approval_deadline"]`, and narrate the options to `reply_to`. Do **not** run `_propose_leg` yet. Keep the "already evaluated" guard distinct from the "awaiting admin" state so the tick can still watchdog it (fixes review C2).
- **Watchdog in the existing 1s tick.** Add a branch: if state is `AWAITING_APPROVAL` and `monotonic() > approval_deadline`, auto-fail with a timeout narration and mark done. Bound by `BAYMAX_APPROVAL_TIMEOUT` (default 300s). Prevents permanent zombies / `NEGOTIATIONS` leak (fixes C2).
- **`resume_after_admin_decision(ctx, req_id, decision)`** — new entry point. `approve` → existing `_propose_leg` loop; `order` → `_order_path()`; `reject` → FAILED. Idempotence guard `neg["decided"]` blocks double-fire. The trade and order paths are **mutually exclusive** per `req_id`.
- **`_order_path(ctx, req_id)`** — calls the supplier seam (in a thread), then settles via the order settlement entry, narrating each step.
- **`start_order(ctx, item, quantity, *, requester, reply_to)`** — proactive entry that **bypasses** `start_negotiation`'s shortfall guard, sets up `reply_to` narration, creates a minimal session record, and calls `_order_path()` directly (fixes C4).

### 3.3 Chat routing — `front_agent.py`

- **Pre-filter in `on_intent` (fixes C1).** At the very top of `on_intent`, **before** any echo/cooldown/duplicate-negotiation logic, check whether the sender has a negotiation in `AWAITING_APPROVAL`. If so, route the text to `parse_decision()` and `resume_after_admin_decision()` and return — the approval reply never reaches the request parser.
- **`parse_decision(text) -> {"decision": "approve"|"order"|"reject"|"unclear"}`** — small keyword parser (e.g. approve/yes/trade; order/buy/purchase; reject/no/cancel). On `unclear`, re-prompt without closing the session.
- **`parse_intent` gains `kind: "order"`** for proactive utterances like "order 500 saline" → routes to `start_order()`.
- **Echo-hardening (fixes N1).** Add `awaiting_approval`, `ordering`, `ordered` to `_MILESTONE_ECHO_RE` so the admin's session isn't closed when ASI:One echoes the new narration back.

### 3.4 Supplier-order seam — `interfaces.py` + new `supplier_order.py`

Mirrors the `get_inventory` / `redis_inventory` fallback pattern.

```python
@dataclass
class SupplierOrder:
    item: str
    quantity: int
    vendor: str
    unit_price: float | None
    total_price: float | None
    currency: str            # e.g. "USD"
    confirmation_ref: str
    live_view_url: str | None  # Browserbase session/screenshot artifact
    status: str              # "prepared" | "confirmed" | "failed"

def order_from_supplier(item: str, quantity: int, *, hospital: str) -> SupplierOrder
```

- **Default = deterministic mock** (offline harnesses need no keys/network).
- `BAYMAX_BROWSERBASE=1` → delegates to `supplier_order.py`. **Fail-closed**: any error (missing key, anti-bot, timeout, layout drift) falls back to the mock and logs `backend=mock`.
- **`supplier_order.order_from_supplier` is a SYNC function** using the **sync** Playwright/Stagehand API, invoked from the async core via `asyncio.to_thread(...)` — exactly the cosmpy idiom (fixes review S4; async-native Playwright cannot be wrapped in `to_thread`).
- Browser flow: navigate → search product → add to cart → reach cart-review → `extract()` total + confirmation → capture `live_view_url`. For a controlled mock vendor it may click through to a real confirmation; for a real public site it stops at cart-review (no crypto checkout).

### 3.5 B/C per-agent confirmation (hybrid) — `baymax_agents.attach_hospital_handlers`

In `on_proposal`, before sending `TransferAccept`, call:

```python
def approve_release(facility: str, item: str, qty: int) -> bool
```

- **Default:** auto-approve and log a notification line ("each agent tied to an admin" satisfied structurally).
- `BAYMAX_REQUIRE_FACILITY_APPROVAL` is reserved for a future **real** gate. The real gate must **not** block the async `on_proposal` handler (would freeze the Bureau event loop — review S3); it would use the same deferred pause/resume pattern as the A admin. Documented as future work; only the synchronous auto-approve ships now.

### 3.6 Order settlement — `settlement.py`

- **New `settle_order_via_payment_protocol(ctx, req_id, order, *, user_address, recipient=None, reply_to=None)`** that takes an **explicit payee** (fixes review C3 — the trade path's `resolve_recipient_wallet` always returns FRONT's own wallet, wrong for an external order).
  - `recipient` defaults to `BAYMAX_SUPPLIER_WALLET`; if unset, falls back to FRONT's wallet but the `RequestPayment.description` and chat narration **explicitly state** "symbolic FET settlement representing external purchase."
  - Amount derived from `order.total_price` when present, else the existing `BAYMAX_PAYMENT_*` pricing (mock).
  - `metadata` keeps the ASI:One-required keys (`provider_agent_wallet`, `fet_network`) so the payment card renders.
- **Distinct pending key** `order-{req_id}` (vs trade `pay-{req_id}`) to avoid `_PAYMENT_PENDING` collisions (fixes review S5). Mutual exclusivity of the two paths per `req_id` prevents dual payment cards (review N3).

---

## 4. Data flow

**Reactive order (admin diverts a trade):**
```
chat intent → start_negotiation → get_inventory → SupplyRequest broadcast
  → offers collected → rank_offers → AWAITING_APPROVAL (narrate plan + cost; arm watchdog)
  → admin replies "order" → resume_after_admin_decision → _order_path
  → asyncio.to_thread(order_from_supplier) → settle_order_via_payment_protocol
  → RequestPayment (supplier wallet, symbolic) → admin signs → CommitPayment
  → verify on-chain (bounded) → CompletePayment → finalize_after_payment → ORDERED
```

**Proactive order (no shortfall):**
```
chat intent "order 500 saline" → parse_intent kind:"order" → start_order
  → _order_path → (same as above from order_from_supplier onward)
```

**Trade (admin approves):** identical to today's flow, except gated behind `AWAITING_APPROVAL` → `approve`, with B/C `approve_release` before each `TransferAccept`.

---

## 5. Error handling

- **Supplier seam failure** → fail-closed to mock; never raises into the handler. A persistent failure narrates a FAILED milestone.
- **Admin never replies** → watchdog auto-fails after `BAYMAX_APPROVAL_TIMEOUT` with a timeout narration (no zombie).
- **`unclear` decision** → re-prompt without `end_session`.
- **Payment verification** → reuses the existing bounded `verify_payment_onchain_with_retry`; same accept/reject semantics.
- **Mailbox restart during admin wait** → in-process `NEGOTIATIONS` is lost; the admin's reply hits an empty dict and is treated as a stale/no-op. **Documented limitation** for the demo (consistent with the existing "a real multi-process deploy would move this to ctx.storage/Redis" note). Out of scope for this wave.

---

## 6. Testing

- **`wave3_order_e2e_check.py`** (new, offline, self-exiting) — mirrors `wave2_e2e_check.py`. Covers: intent → evaluate → admin `order` → mock supplier → mock FET settlement → `ORDERED`. **Must set `PAYMENT_VERIFY_ONCHAIN=false`, a short offer timeout, and supplier-mock mode BEFORE importing any agent module** (import-time env-var constraint — review N2).
- **Dedicated order/approval self-test** — not a flag on the existing `BAYMAX_SELFTEST` (which hard-codes a shortage intent and wires no order settlement hook — review N4).
- **Extend coverage** for the `approve` and `reject` decisions on the trade path.
- `requirements.txt` — add `stagehand` / `browserbase` / `playwright` as **optional** (documented; not needed for offline runs).

---

## 7. New environment variables

| Var | Effect |
| :-- | :-- |
| `BAYMAX_BROWSERBASE` | `1`/`true` → `order_from_supplier` uses the Browserbase impl; unset = deterministic mock (fail-closed). |
| `BAYMAX_APPROVAL_TIMEOUT` | Seconds to wait for the admin decision before watchdog auto-fail (default 300). |
| `BAYMAX_SUPPLIER_WALLET` | `fetch1…` payee for external-order settlements; unset → FRONT wallet, narrated as symbolic. |
| `BAYMAX_REQUIRE_FACILITY_APPROVAL` | Reserved — flips B/C `approve_release` to a real (future, non-blocking) gate. Default off (auto-approve). |
| `BROWSERBASE_API_KEY`, `BROWSERBASE_PROJECT_ID`, `MODEL_API_KEY` | Browserbase/Stagehand credentials (only for `BAYMAX_BROWSERBASE=1`). |

---

## 8. Files touched

- **New:** `supplier_order.py`, `wave3_order_e2e_check.py`, this spec.
- **Modified:** `protocol.py` (enum), `baymax_agents.py` (gate, watchdog, resume, `_order_path`, `start_order`, `approve_release` call), `front_agent.py` (pre-filter routing, `parse_decision`, `kind:"order"`, echo regex), `interfaces.py` (`SupplierOrder` + `order_from_supplier` seam), `settlement.py` (`settle_order_via_payment_protocol`, distinct pending key), `run_front.py` (wire order seam + supplier wallet), `requirements.txt`, `CLAUDE.md`, `README.md`/`DELIVERABLES.md`.

---

## 9. Out of scope (YAGNI)

- A web dashboard / literal buttons (admin stays in ASI:One chat).
- Real (non-blocking, deferred-resume) B/C facility-admin gate — seam reserved, not implemented.
- Cross-restart persistence of `NEGOTIATIONS` (ctx.storage/Redis) for the admin-wait window.
- Real crypto checkout on a vendor site (settlement stays a separate FET tx).
