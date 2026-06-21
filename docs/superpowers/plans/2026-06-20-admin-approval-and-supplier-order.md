# Admin Approval Gate + External Supplier Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a human-in-the-loop admin approval gate to the Baymax negotiation (admin approves the trade in ASI:One chat, or chooses to order externally instead), plus an external-supplier order branch (Browserbase seam, FET-settled) reachable both reactively (admin diverts a trade) and proactively (restock with no shortfall).

**Architecture:** Pause-and-resume in the negotiation core. After `_evaluate` ranks offers it halts in a new `AWAITING_APPROVAL` state, narrates the decision to the chat admin, and a chat reply resumes the flow via `resume_after_admin_decision`. The order path is a sibling terminal branch calling an `order_from_supplier` seam (deterministic mock now, Browserbase later, fail-closed) and settling a FET payment to a configurable supplier wallet. B/C expose a synchronous `approve_release` confirmation seam. All new work sits behind the existing seam/hook idioms so offline harnesses stay green.

**Tech Stack:** Python 3.12+ (dev on 3.14), uagents 0.25.2 / uagents_core, cosmpy (testnet), Stagehand/Playwright over Browserbase (optional), Redis (optional). No pytest — verification is self-exiting scripts run with `./.venv/bin/python`.

## Global Constraints

- **Run everything from `adyan-agent-communication-layer/`.** The venv is `adyan-agent-communication-layer/.venv`; prefer `./.venv/bin/python <script>`.
- **Import `agent_base` first**, before constructing any `Agent`/`Protocol` (installs the Python 3.14 event loop). Every new module that builds agents follows this.
- **Testnet only, fail-closed.** Never route to mainnet. Payment metadata MUST include `provider_agent_wallet` + `fet_network` or ASI:One rejects the card.
- **`protocol.py` is the frozen single source of truth.** Only additive `NegotiationState` enum values are allowed here (Task 1); never redefine wire models or the re-exported Fetch protocols.
- **Never call sync cosmpy/Stagehand/Playwright directly in an async handler** — wrap blocking sync calls in `asyncio.to_thread(...)`. `order_from_supplier` and its Browserbase impl are SYNC functions invoked via `asyncio.to_thread`.
- **`ctx.agent.wallet` does not exist inside a handler** — use `resolve_recipient_wallet(ctx)` / the registered wallet mapping.
- **Seams degrade to deterministic mocks** on any failure (missing lib, no key, network down), logging `backend=mock`, exactly like `get_inventory`/`redis_inventory`.
- **Spec:** `docs/superpowers/specs/2026-06-20-admin-approval-and-supplier-order-design.md`.

---

## File Structure

| File | Responsibility | Change |
| :-- | :-- | :-- |
| `protocol.py` | Frozen contract; `NegotiationState` enum | Add `AWAITING_APPROVAL`, `ORDERING`, `ORDERED` (Task 1) |
| `interfaces.py` | Plain-Python seams + mocks | Add `SupplierOrder`, `order_from_supplier` (mock), `approve_release` (Task 2) |
| `supplier_order.py` | **New** — Browserbase impl of the order seam, fail-closed | Create (Task 3) |
| `settlement.py` | Seller-side Payment Protocol | Add `recipient` param to `request_payment`, `settle_order_via_payment_protocol`, order-aware finalize/fail dispatch (Task 4) |
| `baymax_agents.py` | Negotiation core + state machine | Admin gate, watchdog, order path, order settlement hook, `approve_release` call (Task 5) |
| `front_agent.py` | ASI:One chat bridge | Pre-filter decision routing, `parse_decision`, `kind:"order"`, echo regex (Task 6) |
| `run_front.py` | Live FRONT wiring | Register order settlement hook (Task 7) |
| `wave3_order_e2e_check.py` | **New** — offline order E2E proof | Create (Task 8) |
| `requirements.txt`, `CLAUDE.md`, `README.md`, `DELIVERABLES.md` | Deps + docs | Update (Task 9) |

---

## Task 1: New negotiation states + narration wiring

**Files:**
- Modify: `adyan-agent-communication-layer/protocol.py:84-93`
- Modify: `adyan-agent-communication-layer/baymax_agents.py:88-96`

**Interfaces:**
- Produces: `NegotiationState.AWAITING_APPROVAL` (`"awaiting_approval"`), `NegotiationState.ORDERING` (`"ordering"`), `NegotiationState.ORDERED` (`"ordered"`) — consumed by every later task. The three new values are added to the `_NARRATE_STATES` frozenset so they stream to ASI:One even under `BAYMAX_SPARSE_NARRATION` (live mode).

- [ ] **Step 1: Add the enum values**

In `protocol.py`, replace the tail of the `NegotiationState` enum (currently ending at `FAILED`):

```python
    IDLE = "idle"
    SHORTFALL_DETECTED = "shortfall_detected"
    REQUESTING = "requesting"
    COLLECTING_OFFERS = "collecting_offers"
    EVALUATING = "evaluating"
    RE_PLANNING = "re_planning"
    PROPOSING = "proposing"
    SETTLING = "settling"
    CONFIRMED = "confirmed"
    FAILED = "failed"          # terminal: no/insufficient offers (extension)
    # --- Wave 3: admin approval gate + external supplier order ---------------
    AWAITING_APPROVAL = "awaiting_approval"  # halted for the chat admin's decision
    ORDERING = "ordering"                    # placing an external supplier order
    ORDERED = "ordered"                      # terminal: external order settled
```

- [ ] **Step 2: Narrate the new states under sparse mode**

In `baymax_agents.py`, replace the `_NARRATE_STATES` frozenset (lines ~88-96):

```python
_NARRATE_STATES = frozenset({
    NegotiationState.SHORTFALL_DETECTED,
    NegotiationState.EVALUATING,
    NegotiationState.PROPOSING,
    NegotiationState.SETTLING,
    NegotiationState.RE_PLANNING,
    NegotiationState.FAILED,
    NegotiationState.CONFIRMED,
    NegotiationState.AWAITING_APPROVAL,
    NegotiationState.ORDERING,
    NegotiationState.ORDERED,
})
```

- [ ] **Step 3: Verify the enum + import**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python -c "from protocol import NegotiationState as N; print(N.AWAITING_APPROVAL.value, N.ORDERING.value, N.ORDERED.value); import baymax_agents"`
Expected: prints `awaiting_approval ordering ordered` and no import error.

- [ ] **Step 4: Commit**

```bash
cd adyan-agent-communication-layer
git add protocol.py baymax_agents.py
git commit -m "feat(protocol): add AWAITING_APPROVAL/ORDERING/ORDERED states + narration"
```

---

## Task 2: Supplier-order seam + facility-release seam (mocks) in `interfaces.py`

**Files:**
- Modify: `adyan-agent-communication-layer/interfaces.py` (add dataclass after `RankedPlan` ~line 114; add functions after `rank_offers` ~line 321)

**Interfaces:**
- Produces:
  - `SupplierOrder` dataclass: `item:str, quantity:int, vendor:str, unit_price:float|None, total_price:float|None, currency:str, confirmation_ref:str, live_view_url:str|None, status:str`
  - `order_from_supplier(item:str, quantity:int, *, hospital:str) -> SupplierOrder` — dispatches to `supplier_order.py` when `BAYMAX_BROWSERBASE` is on (Task 3), else the deterministic mock; fail-closed to mock.
  - `approve_release(facility:str, item:str, qty:int) -> bool` — per-facility admin confirmation seam.
- Consumes (at runtime, lazily): `supplier_order.browserbase_enabled()` / `supplier_order.browserbase_order(...)` (Task 3).

- [ ] **Step 1: Write the failing test**

Create `adyan-agent-communication-layer/check_interfaces_order.py`:

```python
"""Throwaway check for the Wave-3 interfaces seams (mock path)."""
import os
os.environ.pop("BAYMAX_BROWSERBASE", None)  # force the mock path

from interfaces import order_from_supplier, approve_release, SupplierOrder

o = order_from_supplier("IV fluids", 200, hospital="Hospital A")
assert isinstance(o, SupplierOrder), o
assert o.item == "IV fluids" and o.quantity == 200, o
assert o.vendor and o.total_price and o.total_price > 0, o
assert o.status == "prepared", o
assert o.confirmation_ref, o
# Deterministic: same inputs -> same confirmation_ref.
o2 = order_from_supplier("IV fluids", 200, hospital="Hospital A")
assert o.confirmation_ref == o2.confirmation_ref, (o.confirmation_ref, o2.confirmation_ref)

# approve_release: default auto-approves; reserved flag denies (fail-closed).
assert approve_release("Hospital B", "IV fluids", 150) is True
os.environ["BAYMAX_REQUIRE_FACILITY_APPROVAL"] = "1"
assert approve_release("Hospital B", "IV fluids", 150) is False
os.environ.pop("BAYMAX_REQUIRE_FACILITY_APPROVAL", None)

print("OK check_interfaces_order")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python check_interfaces_order.py`
Expected: FAIL — `ImportError: cannot import name 'order_from_supplier' from 'interfaces'`.

- [ ] **Step 3: Add the dataclass**

In `interfaces.py`, after the `RankedPlan` dataclass (~line 114) add:

```python
@dataclass
class SupplierOrder:
    """An external-supplier purchase order. Returned by order_from_supplier().

    For the mock + Browserbase 'prepared' flow this represents a cart-review /
    prepared order (we do NOT pay the vendor in crypto; settlement is a separate
    FET tx). total_price/currency are the vendor's quote (display only); the FET
    charge is derived from quantity by the settlement layer."""

    item: str
    quantity: int
    vendor: str
    unit_price: Optional[float] = None
    total_price: Optional[float] = None
    currency: str = "USD"
    confirmation_ref: str = ""
    live_view_url: Optional[str] = None   # Browserbase session/screenshot artifact
    status: str = "prepared"              # prepared | confirmed | failed
```

- [ ] **Step 4: Add the mock + dispatcher + facility seam**

At the end of `interfaces.py` add:

```python
# ---------------------------------------------------------------------------
# SEAM 3 — external supplier order (Browserbase, Wave 3). Mirrors get_inventory:
# delegates to supplier_order.py when BAYMAX_BROWSERBASE is on, else a
# deterministic mock; fail-closed to the mock on ANY error so offline harnesses
# never need Browserbase/keys.
# ---------------------------------------------------------------------------

# item -> (vendor, unit price USD). Deterministic so the demo + tests are stable.
_MOCK_VENDORS = {
    "IV fluids": ("MedSupply Direct", 12.50),
    "saline": ("MedSupply Direct", 3.20),
    "sutures": ("SurgiSupply Co", 8.75),
}


def _mock_order_from_supplier(item: str, quantity: int, *, hospital: str) -> SupplierOrder:
    vendor, unit = _MOCK_VENDORS.get(item, ("Generic Medical Supplier", 10.0))
    qty = max(int(quantity), 1)
    total = round(unit * qty, 2)
    # Deterministic, human-readable PO ref (no hash() — that is per-process random).
    ref = f"MOCK-PO-{hospital.split()[-1]}-{item.replace(' ', '')[:4].upper()}-{qty}"
    return SupplierOrder(
        item=item, quantity=qty, vendor=vendor, unit_price=unit,
        total_price=total, currency="USD", confirmation_ref=ref,
        live_view_url=None, status="prepared",
    )


def order_from_supplier(item: str, quantity: int, *, hospital: str) -> SupplierOrder:
    """Place (prepare) an external-supplier order for `quantity` of `item`.

    BAYMAX_BROWSERBASE=1 -> drive a real vendor site via supplier_order.py
    (Stagehand/Playwright over Browserbase). On ANY failure (missing lib, no key,
    anti-bot, timeout) fall back to the deterministic mock. This is a SYNC
    function: callers in async handlers invoke it via asyncio.to_thread()."""
    import logging

    import supplier_order  # lazy: keeps Browserbase/playwright optional

    if supplier_order.browserbase_enabled():
        try:
            o = supplier_order.browserbase_order(item, quantity, hospital=hospital)
            logging.getLogger("baymax.order").info(
                "[order] backend=browserbase %s x%s vendor=%s total=%s ref=%s",
                item, quantity, o.vendor, o.total_price, o.confirmation_ref,
            )
            return o
        except Exception as exc:  # noqa: BLE001 — fail-closed to the mock
            logging.getLogger("baymax.order").warning(
                "[order] backend=browserbase FAILED for %s x%s (%s) -> mock fallback",
                item, quantity, exc,
            )
    return _mock_order_from_supplier(item, quantity, hospital=hospital)


# ---------------------------------------------------------------------------
# SEAM 4 — per-facility admin confirmation (Wave 3 hybrid HITL). The surplus
# facility's admin confirms releasing stock before a TransferAccept. Default:
# auto-approve + log a notification ("each agent tied to an admin"). The reserved
# BAYMAX_REQUIRE_FACILITY_APPROVAL flag denies (fail-closed) until a real,
# non-blocking deferred-approval channel is built — it must NOT block the
# on_proposal event-loop handler.
# ---------------------------------------------------------------------------

def approve_release(facility: str, item: str, qty: int) -> bool:
    import logging
    import os

    require = os.getenv("BAYMAX_REQUIRE_FACILITY_APPROVAL", "").strip().lower() in (
        "1", "true", "yes",
    )
    logging.getLogger("baymax.facility").info(
        "[facility-admin] %s: release %s %s -> %s",
        facility, qty, item,
        "REQUIRES APPROVAL (reserved gate: denying until channel exists)"
        if require else "auto-approved + notified",
    )
    return not require
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python check_interfaces_order.py`
Expected: PASS — prints `OK check_interfaces_order`.

> Note: `order_from_supplier` lazily imports `supplier_order`, created in Task 3. This check forces the mock path but still imports `supplier_order` to call `browserbase_enabled()`. If Task 3 is not yet done, temporarily guard with a try/except in the dispatcher is NOT needed — implement Task 3 next; the check passes once `supplier_order.py` exists. If running tasks strictly in order, do Step 6 after Task 3 Step 4. **Simplest: implement Task 3 before re-running this check.**

- [ ] **Step 6: Commit**

```bash
cd adyan-agent-communication-layer
git add interfaces.py check_interfaces_order.py
git commit -m "feat(interfaces): add SupplierOrder + order_from_supplier + approve_release seams"
```

---

## Task 3: Browserbase order backend `supplier_order.py`

**Files:**
- Create: `adyan-agent-communication-layer/supplier_order.py`

**Interfaces:**
- Produces:
  - `browserbase_enabled() -> bool` — true when `BAYMAX_BROWSERBASE` is `1`/`true`/`yes`.
  - `browserbase_order(item:str, quantity:int, *, hospital:str) -> SupplierOrder` — SYNC; drives a vendor site via Stagehand/Playwright over Browserbase. Raises on any failure (caller in `interfaces.order_from_supplier` catches → mock).
- Consumes: `interfaces.SupplierOrder` (top-level import is safe — `interfaces` imports this module only lazily, so it is fully initialised first, exactly like `redis_inventory`).

- [ ] **Step 1: Create the module (lazy heavy imports, sync, raises on failure)**

Create `adyan-agent-communication-layer/supplier_order.py`:

```python
"""supplier_order.py — the REAL implementation of the external-supplier order
seam (Wave 3), behind interfaces.order_from_supplier().

interfaces.order_from_supplier() ships a deterministic mock so the network is
never blocked on Browserbase. This module is the live backend: it drives a vendor
website with Stagehand/Playwright over a Browserbase cloud browser, navigates to
the product, adds it to the cart, reaches the cart-review step, and extracts the
order total + a confirmation/cart reference — returning the SAME SupplierOrder
shape. interfaces.order_from_supplier() delegates here when BAYMAX_BROWSERBASE=1
and falls back to the mock on ANY failure (the seam must never hang).

This is a SYNC module on purpose: the async negotiation core calls it via
asyncio.to_thread() (the cosmpy gotcha — Playwright's async API cannot be wrapped
in to_thread, so we use the SYNC Playwright/Stagehand API here). Heavy imports
(stagehand/playwright/browserbase) are done lazily INSIDE browserbase_order() so
this module imports cleanly even when those packages are absent.

Env:
    BAYMAX_BROWSERBASE        1/true -> use this backend (default off = mock)
    BAYMAX_SUPPLIER_URL       vendor site to drive (default a demo placeholder)
    BROWSERBASE_API_KEY       Browserbase credentials
    BROWSERBASE_PROJECT_ID
    MODEL_API_KEY             model key for Stagehand act/extract
    BAYMAX_ORDER_TIMEOUT_S    overall wall-clock budget (default 90)
"""

from __future__ import annotations

import os

# Safe top-level import: interfaces imports THIS module only lazily, so interfaces
# is fully initialised by the time we are first imported (mirrors redis_inventory).
from interfaces import SupplierOrder


def browserbase_enabled() -> bool:
    """True when the live Browserbase backend should be used (opt-in)."""
    return os.getenv("BAYMAX_BROWSERBASE", "").strip().lower() in ("1", "true", "yes")


def browserbase_order(item: str, quantity: int, *, hospital: str) -> SupplierOrder:
    """Drive a vendor site to a prepared order and return a SupplierOrder.

    SYNC + raises on any failure (the caller falls back to the mock). Uses the
    sync Stagehand API; heavy imports are lazy so the module loads without the
    optional deps installed.
    """
    from pydantic import BaseModel
    from stagehand.sync import Stagehand  # sync API (see Browserbase docs)

    url = os.getenv("BAYMAX_SUPPLIER_URL", "https://www.example-medical-supply.com")

    class _Quote(BaseModel):
        vendor: str
        total_price: float
        currency: str = "USD"
        confirmation_ref: str = ""

    sh = Stagehand(
        env="BROWSERBASE",
        api_key=os.environ["BROWSERBASE_API_KEY"],
        project_id=os.environ["BROWSERBASE_PROJECT_ID"],
        model_name="anthropic/claude-sonnet-4-6",
        model_api_key=os.environ["MODEL_API_KEY"],
    )
    sh.init()
    try:
        page = sh.page
        live_view_url = getattr(getattr(sh, "session", None), "live_view_url", None)
        page.goto(url)
        page.act(f"search for '{quantity} units of {item}' and add the first result to the cart")
        page.act("proceed to the cart review page")
        quote = page.extract(
            "extract the vendor/store name, order total price, currency, and any "
            "cart or order reference number from the cart-review page",
            schema=_Quote,
        )
        return SupplierOrder(
            item=item, quantity=int(quantity), vendor=quote.vendor or "Unknown vendor",
            unit_price=(round(quote.total_price / max(int(quantity), 1), 2)
                        if quote.total_price else None),
            total_price=quote.total_price, currency=quote.currency or "USD",
            confirmation_ref=quote.confirmation_ref or f"BB-{item.replace(' ', '')[:4].upper()}-{quantity}",
            live_view_url=live_view_url, status="prepared",
        )
    finally:
        try:
            sh.close()
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass
```

- [ ] **Step 2: Verify it imports cleanly without the optional deps**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python -c "import supplier_order; print(supplier_order.browserbase_enabled())"`
Expected: prints `False` (no heavy import triggered; deps not required to load the module).

- [ ] **Step 3: Verify the seam dispatch + mock fallback end to end**

Run: `cd adyan-agent-communication-layer && BAYMAX_BROWSERBASE=1 ./.venv/bin/python -c "from interfaces import order_from_supplier; o=order_from_supplier('saline', 50, hospital='Hospital A'); print(o.status, o.vendor, o.total_price)"`
Expected: prints `prepared MedSupply Direct 160.0` — Browserbase enabled but `stagehand` absent → fail-closed to the mock (a `backend=browserbase FAILED ... -> mock fallback` warning is logged).

- [ ] **Step 4: Re-run the Task 2 check (now that `supplier_order` exists)**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python check_interfaces_order.py`
Expected: PASS — `OK check_interfaces_order`.

- [ ] **Step 5: Commit**

```bash
cd adyan-agent-communication-layer
git add supplier_order.py
git commit -m "feat(supplier_order): Browserbase order backend, fail-closed to mock"
```

---

## Task 4: Order settlement in `settlement.py`

**Files:**
- Modify: `adyan-agent-communication-layer/settlement.py` (`request_payment` ~445; `_finalize_from_payment` ~327; `_fail_from_payment` ~341; new `settle_order_via_payment_protocol`; `__all__`)

**Interfaces:**
- Consumes (lazily, from Task 5): `baymax_agents.finalize_order_after_payment(ctx, req_id, ref, *, tx_id)`, `baymax_agents.fail_order_after_payment(ctx, req_id, reason)`.
- Produces: `settle_order_via_payment_protocol(ctx, req_id, order, *, user_address, reply_to=None) -> str` (returns `order-<req_id>`), and a `recipient` keyword on `request_payment(...)`.

- [ ] **Step 1: Add an optional `recipient` override to `request_payment`**

In `settlement.py`, change the `request_payment` signature (line ~445) and the `recipient` resolution (line ~462):

```python
async def request_payment(
    ctx: Context,
    user_address: str,
    amount: Optional[str] = None,
    reference: Optional[str] = None,
    description: Optional[str] = None,
    recipient: Optional[str] = None,
) -> RequestPayment:
```

Then replace the single line `recipient = resolve_recipient_wallet(ctx)` with:

```python
    # Explicit payee (e.g. a supplier wallet for external orders) overrides our
    # own wallet; otherwise bill to OUR FET wallet (the trade facilitation case).
    recipient = recipient or resolve_recipient_wallet(ctx)
```

(The existing `metadata["provider_agent_wallet"] = recipient` and the `fetch1` guard now reflect the override automatically.)

- [ ] **Step 2: Make finalize/fail dispatch order vs trade**

In `settlement.py`, replace `_finalize_from_payment` (lines ~327-338) and `_fail_from_payment` (lines ~341-347):

```python
async def _finalize_from_payment(
    ctx: Context, reference: Optional[str], tx_id: str | None,
) -> None:
    key = _resolve_pending_key(reference)
    pending = _PAYMENT_PENDING.pop(key, None)
    if not pending:
        return
    if pending.get("kind") == "order":
        from baymax_agents import finalize_order_after_payment  # lazy import

        await finalize_order_after_payment(ctx, pending["req_id"], key, tx_id=tx_id)
    else:
        from baymax_agents import finalize_after_payment  # lazy import

        await finalize_after_payment(ctx, pending["req_id"], key, tx_id=tx_id)


async def _fail_from_payment(ctx: Context, reference: Optional[str], reason: str) -> None:
    key = _resolve_pending_key(reference)
    pending = _PAYMENT_PENDING.pop(key, None)
    if not pending:
        return
    if pending.get("kind") == "order":
        from baymax_agents import fail_order_after_payment  # lazy import

        await fail_order_after_payment(ctx, pending["req_id"], reason)
    else:
        from baymax_agents import fail_after_payment  # lazy import

        await fail_after_payment(ctx, pending["req_id"], reason)
```

- [ ] **Step 3: Add the order settlement entry point**

In `settlement.py`, after `settle_via_payment_protocol` (ends ~line 600) add:

```python
async def settle_order_via_payment_protocol(
    ctx: Context,
    req_id: str,
    order,
    user_address: str,
    reply_to: Optional[str] = None,
) -> str:
    """Settle an external-supplier ORDER via the Payment Protocol.

    Sends a RequestPayment to the chat admin for a SYMBOLIC FET amount
    representing the purchase (we cannot pay a vendor in crypto at checkout). The
    payee is BAYMAX_SUPPLIER_WALLET when set (a distinct 'supplier' fetch1...
    wallet), otherwise OUR FET wallet — and the description says so explicitly.
    The FET amount uses the same per-unit/flat pricing as trades (the vendor's
    USD total is shown in the narration, not converted). Reference is
    `order-<req_id>` so it never collides with a trade's `pay-<req_id>`.
    """
    reference = f"order-{req_id}"
    qty = int(getattr(order, "quantity", 0) or 0)
    amount = _amount_for_total(qty)
    supplier_wallet = os.getenv("BAYMAX_SUPPLIER_WALLET", "").strip() or None
    payee_note = "to the supplier wallet" if supplier_wallet else (
        "(symbolic FET settlement representing the external purchase)"
    )
    description = (
        f"Baymax external supplier order {req_id}: {qty} {getattr(order, 'item', '')} "
        f"from {getattr(order, 'vendor', 'supplier')} "
        f"(vendor quote {getattr(order, 'total_price', '?')} {getattr(order, 'currency', 'USD')}) "
        f"— {payee_note}."
    )
    await request_payment(
        ctx,
        user_address=user_address,
        amount=amount,
        reference=reference,
        description=description,
        recipient=supplier_wallet,
    )
    chat = reply_to or user_address
    if chat:
        _PAYMENT_PENDING[reference] = {
            "reply_to": chat, "req_id": req_id, "kind": "order",
        }
    ctx.logger.info(
        f"[payment] settle_order_via_payment_protocol: requested {amount} FET for "
        f"order {req_id} from {user_address}; payee="
        f"{supplier_wallet or 'OUR wallet (symbolic)'}; reference={reference}."
    )
    return reference
```

- [ ] **Step 4: Export it**

In `settlement.py` `__all__` (line ~603), add `"settle_order_via_payment_protocol",` after `"settle_via_payment_protocol",`.

- [ ] **Step 5: Verify it imports and the signature is correct**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python -c "import inspect, settlement; print('settle_order_via_payment_protocol' in settlement.__all__); print('recipient' in inspect.signature(settlement.request_payment).parameters)"`
Expected: prints `True` then `True`.

- [ ] **Step 6: Commit**

```bash
cd adyan-agent-communication-layer
git add settlement.py
git commit -m "feat(settlement): order settlement entry + recipient override + order/trade finalize dispatch"
```

---

## Task 5: Admin gate + order path in `baymax_agents.py`

**Files:**
- Modify: `adyan-agent-communication-layer/baymax_agents.py` (imports ~48-80; `_evaluate` ~184-245; offer-timeout tick ~576-590; `on_proposal` in `attach_hospital_handlers` ~629-652; add new functions + hook + `find_awaiting_approval`)

**Interfaces:**
- Consumes: `interfaces.order_from_supplier`, `interfaces.approve_release` (Task 2); `settlement._amount_for_total` (existing, lazy).
- Produces (for Tasks 6/7/8):
  - `find_awaiting_approval(reply_to:str) -> str | None`
  - `resume_after_admin_decision(ctx, req_id:str, decision:str) -> None` (`decision` in `"approve"|"order"|"reject"`)
  - `start_order(ctx, item:str, quantity:int|None, *, requester:str=REQUESTER, reply_to:str|None=None) -> str`
  - `register_order_settlement_hook(fn) -> None` (fn signature `async fn(ctx, req_id, order, user_address, reply_to) -> str`)
  - `finalize_order_after_payment(ctx, req_id, settlement_ref, *, tx_id=None) -> None`
  - `fail_order_after_payment(ctx, req_id, reason) -> None`
  - `APPROVAL_TIMEOUT_S` (module constant)

- [ ] **Step 1: Add imports + the approval-timeout constant**

In `baymax_agents.py`, add `import asyncio` at the top of the stdlib imports (after `import os`). Extend the `interfaces` import block (lines ~63-71) to include the two new seams:

```python
from interfaces import (
    OfferView,
    SupplyNeed,
    approve_release,
    distance_between,
    eta_minutes_for,
    expiry_for,
    get_inventory,
    order_from_supplier,
    rank_offers,
)
```

After the `MAX_REPLAN_ATTEMPTS` line (~99) add:

```python
# Seconds to wait for the chat admin's approve/order/reject decision before the
# watchdog auto-fails the negotiation (prevents AWAITING_APPROVAL zombies).
APPROVAL_TIMEOUT_S = float(os.getenv("BAYMAX_APPROVAL_TIMEOUT", "300"))
```

- [ ] **Step 2: Halt `_evaluate` at the admin gate (do not auto-propose)**

In `baymax_agents.py`, replace the body of `_evaluate` from the `if not plan.allocations:` block through the end of the function (lines ~211-245) with:

```python
    if not plan.allocations:
        # No inter-facility trade is possible — offer the external order instead
        # of failing outright (admin may still order or cancel).
        await _request_admin_decision(ctx, req_id)
        return

    if not plan.fully_covered:
        if len(plan.allocations) > 1:
            await _step(ctx, neg, NegotiationState.EVALUATING,
                        f"Pooled spare across {len(plan.allocations)} facilities covers only "
                        f"{plan.total_covered}/{neg['need']} {neg['item']} "
                        f"({plan.shortfall_remaining} would remain short).", narrate=True)
        else:
            await _step(ctx, neg, NegotiationState.EVALUATING,
                        f"No single facility covers {neg['need']} {neg['item']}; best split "
                        f"covers {plan.total_covered}/{neg['need']} "
                        f"({plan.shortfall_remaining} would remain short).", narrate=True)

    # Halt for the chat admin's decision (approve trade / order externally / reject)
    # instead of auto-proposing. resume_after_admin_decision() continues the flow.
    await _request_admin_decision(ctx, req_id)
```

- [ ] **Step 3: Add the gate + resume + trade + order functions**

In `baymax_agents.py`, immediately after `_evaluate` (before `_propose_leg`, ~line 247) insert:

```python
def find_awaiting_approval(reply_to: str) -> str | None:
    """Return the request_id of an active negotiation that is AWAITING_APPROVAL
    for this chat user, or None. Used by FRONT to route a decision reply."""
    for req_id, neg in NEGOTIATIONS.items():
        if (neg.get("reply_to") == reply_to and not neg.get("done")
                and neg.get("state") == NegotiationState.AWAITING_APPROVAL):
            return req_id
    return None


async def _request_admin_decision(ctx: Context, req_id: str):
    """Halt the negotiation and ask the chat admin to decide: approve the trade,
    order externally, or reject. Arms the approval watchdog (see offer_timeout)."""
    neg = NEGOTIATIONS[req_id]
    neg["approval_deadline"] = time.monotonic() + APPROVAL_TIMEOUT_S
    neg["decided"] = False
    plan = neg.get("plan")
    if plan and plan.allocations:
        from settlement import _amount_for_total  # lazy: avoids import cycle

        cost = _amount_for_total(plan.total_covered)
        legs = "; ".join(f"{a.quantity} from {a.offerer}" for a in plan.allocations)
        detail = (
            f"Decision needed. Best inter-facility trade: {legs} "
            f"(covers {plan.total_covered}/{neg['need']} {neg['item']}, ~{cost} FET). "
            f"Reply `approve` to authorize the trade, `order` to purchase "
            f"{neg['need']} {neg['item']} from an external supplier instead, or "
            f"`reject` to cancel."
        )
    else:
        detail = (
            f"No facility can spare {neg['item']} (need {neg['need']}). "
            f"Reply `order` to purchase from an external supplier, or `reject` to cancel."
        )
    await _step(ctx, neg, NegotiationState.AWAITING_APPROVAL, detail, narrate=True)


async def resume_after_admin_decision(ctx: Context, req_id: str, decision: str) -> None:
    """Continue a halted negotiation per the chat admin's decision.

    decision in {"approve", "order", "reject"}. Idempotent via neg["decided"]."""
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg.get("done") or neg.get("decided"):
        return
    if neg.get("state") != NegotiationState.AWAITING_APPROVAL:
        return
    plan = neg.get("plan")
    has_trade = bool(plan and plan.allocations)

    if decision == "order":
        neg["decided"] = True
        await _order_path(ctx, req_id)
        return
    if decision == "reject":
        neg["decided"] = True
        await _step(ctx, neg, NegotiationState.FAILED,
                    f"Admin rejected the resolution for {neg['need']} {neg['item']}. "
                    f"Shortfall unresolved — no transfer or order placed.",
                    narrate=True, final=True)
        neg["done"] = True
        _maybe_exit(ctx)
        return
    # decision == "approve"
    if not has_trade:
        # Nothing to approve; keep waiting for order/reject (do not consume).
        await _step(ctx, neg, NegotiationState.AWAITING_APPROVAL,
                    "There is no inter-facility trade to approve. Reply `order` to "
                    "purchase externally, or `reject` to cancel.", narrate=True)
        return
    neg["decided"] = True
    await _begin_trade(ctx, req_id)


async def _begin_trade(ctx: Context, req_id: str):
    """Propose the approved plan's legs (the proposing loop lifted out of the old
    _evaluate, now gated behind admin approval)."""
    neg = NEGOTIATIONS[req_id]
    plan = neg["plan"]
    neg["committed"] = {}
    neg["leg"] = {}
    neg["pending"] = set()
    n = len(plan.allocations)
    await _step(ctx, neg, NegotiationState.PROPOSING,
                f"Admin approved. Composing transfer: {n} leg(s).", narrate=True)
    for i, al in enumerate(plan.allocations):
        pid = f"{req_id}-{i}"
        await _propose_leg(ctx, neg, req_id, pid, al.offerer, al.quantity, al.eta_minutes,
                           leg_index=i, leg_count=n)


async def _order_path(ctx: Context, req_id: str):
    """Place an external-supplier order for the full need and settle it in FET."""
    neg = NEGOTIATIONS[req_id]
    await _step(ctx, neg, NegotiationState.ORDERING,
                f"Ordering {neg['need']} {neg['item']} from an external supplier…",
                narrate=True)
    try:
        # SYNC seam off the event loop (Browserbase/Playwright or mock).
        order = await asyncio.to_thread(
            order_from_supplier, neg["item"], neg["need"], hospital=neg["requester"],
        )
    except Exception as exc:  # noqa: BLE001 — never crash the handler
        await _step(ctx, neg, NegotiationState.FAILED,
                    f"External order failed ({exc}). Shortfall unresolved.",
                    narrate=True, final=True)
        neg["done"] = True
        _maybe_exit(ctx)
        return

    neg["order"] = order
    quote = (f", vendor quote {order.total_price} {order.currency}"
             if order.total_price else "")
    view = f" View: {order.live_view_url}" if order.live_view_url else ""
    prepared = (f"Order prepared with {order.vendor}: {order.quantity} {order.item}"
                f"{quote} (ref {order.confirmation_ref}).{view}")
    await _step(ctx, neg, NegotiationState.ORDERING, prepared, narrate=True)

    ref = await _settle_order(ctx, req_id, order)
    if _ORDER_SETTLEMENT_HOOK is not None and neg.get("reply_to"):
        from settlement import _amount_for_total  # lazy

        amount = _amount_for_total(order.quantity)
        neg["awaiting_payment"] = True
        await _step(ctx, neg, NegotiationState.ORDERING,
                    f"Approve **{amount} FET** on testnet to finalize the order "
                    f"(ref {ref}). Open your wallet in ASI:One if no prompt appears.",
                    narrate=True)
        return
    await _step(ctx, neg, NegotiationState.ORDERED,
                f"{prepared} Settlement: {ref}.", narrate=True, final=True)
    neg["done"] = True
    _maybe_exit(ctx)


async def start_order(ctx: Context, item: str, quantity: int | None, *,
                      requester: str = REQUESTER, reply_to: str | None = None) -> str:
    """Proactively place an external order WITHOUT a negotiation (restock / plan-
    ahead). Bypasses the shortfall guard in start_negotiation. Returns request_id."""
    if quantity is None or quantity <= 0:
        inv = get_inventory(requester, item)
        quantity = inv.shortfall if inv.shortfall > 0 else int(
            os.getenv("BAYMAX_DEFAULT_ORDER_QTY", "100"))
    req_id = uuid4().hex[:8]
    NEGOTIATIONS[req_id] = {
        "item": item, "requester": requester, "need": int(quantity),
        "offers": {}, "expected": set(), "plan": None, "pending": set(),
        "accepts": set(), "rejects": set(), "deadline": time.monotonic(),
        "done": False, "reply_to": reply_to, "state": NegotiationState.ORDERING,
        "evaluated": True, "settled": True, "decided": True, "committed": {},
        "leg": {}, "rejected_facilities": set(), "replans": 0, "covered": 0,
        "order": None,
    }
    await _order_path(ctx, req_id)
    return req_id
```

- [ ] **Step 4: Add the order settlement hook + finalize/fail**

In `baymax_agents.py`, after `register_settlement_hook` / `settle_transfer` (~line 501) add:

```python
# --- Order settlement hook (Wave 3) ----------------------------------------
# Parallel to _SETTLEMENT_HOOK: run_front registers
# settlement.settle_order_via_payment_protocol so an external order settles via
# the Payment Protocol (FET) to a supplier wallet. Stub when unregistered.
_ORDER_SETTLEMENT_HOOK = None


def register_order_settlement_hook(fn) -> None:
    """Register the real order settlement handler.
    fn: async fn(ctx, req_id, order, user_address, reply_to) -> str."""
    global _ORDER_SETTLEMENT_HOOK
    _ORDER_SETTLEMENT_HOOK = fn


async def _settle_order(ctx: Context, req_id: str, order) -> str:
    neg = NEGOTIATIONS.get(req_id, {})
    user_address = neg.get("reply_to")
    if _ORDER_SETTLEMENT_HOOK is not None and user_address:
        return await _ORDER_SETTLEMENT_HOOK(
            ctx, req_id, order, user_address=user_address, reply_to=user_address,
        )
    ref = f"stub-order-{req_id}"
    ctx.logger.info(
        f"[order-settlement-stub] Prepared order {order.confirmation_ref} on "
        f"{os.getenv('FETCH_NETWORK', 'testnet')} -> {ref} (no hook / no chat user)."
    )
    return ref


async def finalize_order_after_payment(
    ctx: Context, req_id: str, settlement_ref: str, *, tx_id: str | None = None,
) -> None:
    """Terminal ORDERED milestone after the order's FET payment succeeds."""
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg.get("done"):
        return
    order = neg.get("order")
    tx_note = f" On-chain tx: `{tx_id}`." if tx_id else ""
    quote = (f", vendor quote {order.total_price} {order.currency}"
             if order and order.total_price else "")
    detail = (
        f"External order confirmed with {getattr(order, 'vendor', 'supplier')}: "
        f"{getattr(order, 'quantity', neg['need'])} {neg['item']}{quote} "
        f"(ref {getattr(order, 'confirmation_ref', '?')}). "
        f"Settlement: {settlement_ref}.{tx_note}"
    )
    await _step(ctx, neg, NegotiationState.ORDERED, detail, narrate=True, final=True)
    neg["awaiting_payment"] = False
    neg["done"] = True
    _maybe_exit(ctx)


async def fail_order_after_payment(ctx: Context, req_id: str, reason: str) -> None:
    """Close the chat when the order's FET settlement cannot be verified."""
    neg = NEGOTIATIONS.get(req_id)
    if not neg or neg.get("done"):
        return
    await _step(ctx, neg, NegotiationState.FAILED,
                f"External order prepared but FET settlement could not be verified "
                f"({reason}).", narrate=True, final=True)
    neg["awaiting_payment"] = False
    neg["done"] = True
    _maybe_exit(ctx)
```

- [ ] **Step 5: Add the approval watchdog to the offer-timeout tick**

In `baymax_agents.py`, replace the body of the `offer_timeout` interval handler (lines ~582-590) with:

```python
        nowt = time.monotonic()
        for req_id, neg in list(NEGOTIATIONS.items()):
            if neg["done"]:
                continue
            state = neg["state"]
            if (state == NegotiationState.COLLECTING_OFFERS and not neg["evaluated"]
                    and nowt >= neg["deadline"]):
                await _step(ctx, neg, NegotiationState.COLLECTING_OFFERS,
                            f"Offer window closed: {len(neg['offers'])}/{len(neg['expected'])} "
                            f"responded. Evaluating with what arrived.", narrate=True)
                await _evaluate(ctx, req_id)
            elif (state == NegotiationState.AWAITING_APPROVAL
                  and nowt >= neg.get("approval_deadline", float("inf"))):
                await _step(ctx, neg, NegotiationState.FAILED,
                            f"No admin decision within {APPROVAL_TIMEOUT_S:.0f}s — "
                            f"timed out. No transfer or order placed.",
                            narrate=True, final=True)
                neg["done"] = True
                _maybe_exit(ctx)
```

- [ ] **Step 6: Gate B/C accept on `approve_release`**

In `baymax_agents.py`, in `on_proposal` (inside `attach_hospital_handlers`), replace the accept branch (lines ~642-646) with:

```python
        if inv.spare_capacity >= msg.quantity:
            if not approve_release(facility, msg.item, msg.quantity):
                ctx.logger.info(f"[{facility}] Facility admin withheld approval for "
                                f"leg {msg.proposal_id}.")
                await ctx.send(sender, TransferReject(
                    request_id=msg.request_id, proposal_id=msg.proposal_id,
                    rejected_by=facility, reason="facility admin withheld approval"))
                return
            ctx.logger.info(f"[{facility}] Accepting leg {msg.leg_index + 1}/{msg.leg_count}: "
                            f"{msg.quantity} {msg.item} -> {msg.to_facility}.")
            await ctx.send(sender, TransferAccept(request_id=msg.request_id,
                                                  proposal_id=msg.proposal_id, accepted_by=facility))
```

- [ ] **Step 7: Verify import + the trade path still works (regression)**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python -c "import baymax_agents as b; print(hasattr(b,'resume_after_admin_decision'), hasattr(b,'start_order'), hasattr(b,'register_order_settlement_hook'), hasattr(b,'find_awaiting_approval'))"`
Expected: prints `True True True True`.

- [ ] **Step 8: Commit**

```bash
cd adyan-agent-communication-layer
git add baymax_agents.py
git commit -m "feat(core): admin approval gate, order path, watchdog, facility-release gate"
```

---

## Task 6: Chat decision routing in `front_agent.py`

**Files:**
- Modify: `adyan-agent-communication-layer/front_agent.py` (imports ~58-62; echo regex ~111-116; add `_ORDER_CUE_RE` + `parse_decision` + help text; `parse_intent` ~291-316; `on_intent` ~388-454)

**Interfaces:**
- Consumes: `baymax_agents.find_awaiting_approval`, `resume_after_admin_decision`, `start_order` (Task 5).
- Produces: `parse_decision(text:str) -> str` (`"approve"|"order"|"reject"|"unclear"`); `parse_intent` now also returns `{"kind":"order", "item", "requester", "quantity"}`.

- [ ] **Step 1: Write the failing test**

Create `adyan-agent-communication-layer/check_parse_decision.py`:

```python
from front_agent import parse_decision, parse_intent

assert parse_decision("approve") == "approve", parse_decision("approve")
assert parse_decision("yes, go ahead") == "approve"
assert parse_decision("order instead") == "order"
assert parse_decision("let's buy it externally") == "order"
assert parse_decision("no, reject this") == "reject"
assert parse_decision("cancel") == "reject"
# Ambiguous (echo of the prompt names all three) -> unclear, never a wrong action.
assert parse_decision("reply approve to trade or order or reject") == "unclear"

# Proactive order intent.
p = parse_intent("order 500 saline")
assert p["kind"] == "order" and p["item"] == "saline" and p["quantity"] == 500, p
# Plain shortfall is still a request, not an order.
r = parse_intent("Hospital A is short on IV fluids")
assert r["kind"] == "request" and r["item"] == "IV fluids", r

print("OK check_parse_decision")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python check_parse_decision.py`
Expected: FAIL — `ImportError: cannot import name 'parse_decision' from 'front_agent'`.

- [ ] **Step 3: Extend imports + echo regex**

In `front_agent.py`, replace the `from baymax_agents import (...)` block (lines ~58-62):

```python
from baymax_agents import (
    NEGOTIATIONS,
    attach_front_handlers,
    find_awaiting_approval,
    resume_after_admin_decision,
    start_negotiation,
    start_order,
)
```

Replace `_MILESTONE_ECHO_RE` (lines ~111-116) so the new state narrations are recognised as echoes:

```python
_MILESTONE_ECHO_RE = re.compile(
    r"^\s*\*\*(?:shortfall_detected|requesting|collecting_offers|evaluating|"
    r"proposing|settling|confirmed|failed|re_planning|idle|"
    r"awaiting_approval|ordering|ordered|"
    r"payment_confirmed|payment_failed)\*\*",
    re.IGNORECASE,
)
```

- [ ] **Step 4: Add the order cue, decision parser, and decision help**

In `front_agent.py`, after `_REQUEST_CUE_RE` (ends ~line 133) add:

```python
# Explicit "place an external order" phrasing (proactive restock / plan-ahead),
# distinct from a shortfall request. Checked before the request-cue gate.
_ORDER_CUE_RE = re.compile(
    r"\b(order|buy|purchase|procure)\b", re.IGNORECASE,
)

# Admin-decision keyword groups (for parse_decision at the AWAITING_APPROVAL gate).
_DECISION_APPROVE_RE = re.compile(
    r"\b(approve|approved|yes|confirm|go ahead|do it|trade|accept|proceed|ok|okay)\b",
    re.IGNORECASE,
)
_DECISION_ORDER_RE = re.compile(
    r"\b(order|buy|purchase|procure|supplier|external|externally)\b", re.IGNORECASE,
)
_DECISION_REJECT_RE = re.compile(
    r"\b(reject|cancel|no|nope|stop|abort|deny|decline)\b", re.IGNORECASE,
)

_DECISION_HELP = (
    "I didn't catch your decision. Reply with one of:\n"
    "  • `approve` — authorize the inter-facility trade\n"
    "  • `order`  — purchase from an external supplier instead\n"
    "  • `reject` — cancel"
)


def parse_decision(text: str) -> str:
    """Classify a chat reply at the AWAITING_APPROVAL gate.

    Returns "approve" | "order" | "reject" | "unclear". Requires EXACTLY one
    signal group to fire, so an echo of our prompt (which names all three) is
    "unclear" and re-prompts rather than triggering a wrong action."""
    t = text or ""
    has_approve = bool(_DECISION_APPROVE_RE.search(t))
    has_order = bool(_DECISION_ORDER_RE.search(t))
    has_reject = bool(_DECISION_REJECT_RE.search(t))
    if (has_approve + has_order + has_reject) != 1:
        return "unclear"
    if has_order:
        return "order"
    if has_reject:
        return "reject"
    return "approve"
```

- [ ] **Step 5: Add the `order` kind to `parse_intent`**

In `front_agent.py`, in `parse_intent`, after the `if item is None: return {"kind": "unknown"}` line (~line 299) and BEFORE the `_REQUEST_CUE_RE` check, insert:

```python
    # Explicit external-order intent ("order 500 saline") — proactive restock,
    # reachable even with no shortfall. Checked before the request-cue gate
    # because "order" is not a shortfall cue.
    if _ORDER_CUE_RE.search(text):
        requester = _match_facility(text) or REQUESTER
        item_aliases = tuple(syn for syn, canon in _ITEM_SYNONYMS.items() if canon == item)
        return {
            "kind": "order",
            "item": item,
            "requester": requester,
            "quantity": _match_quantity(text, item_aliases),
        }
```

- [ ] **Step 6: Route decisions + orders in `on_intent`**

In `front_agent.py`, in `on_intent`, insert this block at the very TOP of the function body, immediately after the docstring and before `parsed = _PARSER(text)` (~line 400):

```python
    # --- AWAITING_APPROVAL pre-filter (must run BEFORE intent parsing / cooldown /
    # duplicate-block, or the decision reply would be eaten as a non-intent). ---
    pending_req_id = find_awaiting_approval(sender)
    if pending_req_id is not None:
        # Ignore our own narration echoed back by ASI:One while we wait.
        if (_MILESTONE_ECHO_RE.match(text) or _ASI1_META_RE.search(text)
                or _looks_like_echo_chatter(text)):
            ctx.logger.debug(f"awaiting-approval: ignoring echo from {sender}")
            return
        decision = parse_decision(text)
        ctx.logger.info(f"admin decision from {sender}: {text!r} -> {decision}")
        if decision == "unclear":
            await ctx.send(sender, create_text_chat(_DECISION_HELP, end_session=False))
            return
        await resume_after_admin_decision(ctx, pending_req_id, decision)
        return
```

Then, after the existing `_LAST_ACCEPTED_INTENT[sender] = time.monotonic()` line (~line 436) — which is reached only for `kind == "request"` — we must also handle `kind == "order"`. Replace the duplicate-block/greeting/request tail so order is handled. Specifically, change the `if kind != "request":` guard (line ~422) to also let `order` through, and branch on order before the request handling. Replace lines ~418-454 (`if kind == "greeting":` through the `await start_negotiation(...)` call) with:

```python
    if kind == "greeting":
        await ctx.send(sender, create_text_chat(_CAPABILITIES, end_session=False))
        return

    if kind not in ("request", "order"):
        await ctx.send(sender, create_text_chat(_UNPARSEABLE_HELP, end_session=True))
        return

    # Cooldown: suppress the post-intent ASI:One echo storm from the same sender.
    last = _LAST_ACCEPTED_INTENT.get(sender)
    if last is not None and (time.monotonic() - last) < _INTENT_COOLDOWN_S:
        ctx.logger.info(
            f"intent cooldown ({_INTENT_COOLDOWN_S:.0f}s) active for {sender} — "
            f"ignoring likely echo")
        return
    _LAST_ACCEPTED_INTENT[sender] = time.monotonic()

    item = parsed["item"]
    requester = parsed.get("requester") or REQUESTER

    if kind == "order":
        quantity = parsed.get("quantity")
        qty_note = f" {quantity}" if quantity else ""
        await ctx.send(sender, create_text_chat(
            f"Understood — placing an external supplier order for{qty_note} {item} "
            f"({requester}). I'll narrate each step.", end_session=False))
        await start_order(ctx, item, quantity, requester=requester, reply_to=sender)
        return

    quantity_needed = parsed.get("quantity_needed")
    qty_note = f" ({quantity_needed} units)" if quantity_needed else ""
    await ctx.send(sender, create_text_chat(
        f"Understood — checking the network for {item} to cover {requester}{qty_note}. "
        f"I'll narrate each step.", end_session=False))
    await start_negotiation(
        ctx, item,
        requester=requester,
        quantity_needed=quantity_needed,
        reply_to=sender,
    )
```

> Note: the duplicate-negotiation block (the `for neg in NEGOTIATIONS.values()` loop, lines ~411-416) stays where it is, between the `if kind == "ignored"` return and the `if kind == "greeting"` block. It is now reached only for non-awaiting senders, which is correct.

- [ ] **Step 7: Run the parser test to verify it passes**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python check_parse_decision.py`
Expected: PASS — `OK check_parse_decision`.

- [ ] **Step 8: Regression — the existing self-test still completes a trade**

Run: `cd adyan-agent-communication-layer && BAYMAX_SELFTEST=1 BAYMAX_EXIT_WHEN_DONE=1 BAYMAX_SELFTEST_INTENT="order 200 saline" ./.venv/bin/python front_agent.py 2>&1 | tail -20`
Expected: the log shows `[ORDERING]` then `[ORDERED]` milestones and the process self-exits (proactive order path, no payment hook in the selftest → stub settlement → ORDERED). If it instead shows the trade path, recheck Step 5/6.

- [ ] **Step 9: Commit**

```bash
cd adyan-agent-communication-layer
git add front_agent.py check_parse_decision.py
git commit -m "feat(front): admin-decision routing, parse_decision, proactive order intent"
```

---

## Task 7: Wire the order settlement hook in `run_front.py`

**Files:**
- Modify: `adyan-agent-communication-layer/run_front.py` (imports ~81-85; `build_agent` ~116)

**Interfaces:**
- Consumes: `settlement.settle_order_via_payment_protocol` (Task 4), `baymax_agents.register_order_settlement_hook` (Task 5).

- [ ] **Step 1: Import the order settlement entry**

In `run_front.py`, extend the `from settlement import (...)` block (lines ~81-85):

```python
from settlement import (
    build_payment_protocol,
    register_recipient_wallet,
    settle_order_via_payment_protocol,
    settle_via_payment_protocol,
)
```

- [ ] **Step 2: Register the order hook**

In `run_front.py`, in `build_agent`, immediately after `sp.register_settlement_hook(settle_via_payment_protocol)` (~line 116) add:

```python
    # (5) Wire ORDER settlement: an admin who chooses to order externally settles
    # the purchase via the same Payment Protocol (FET) to a supplier wallet.
    sp.register_order_settlement_hook(settle_order_via_payment_protocol)
```

- [ ] **Step 3: Verify the runner builds**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python -c "import run_front; a,w=run_front.build_agent(); print('built', bool(a), w.startswith('fetch1'))"`
Expected: prints `built True True` (no exceptions; FET wallet resolved).

- [ ] **Step 4: Commit**

```bash
cd adyan-agent-communication-layer
git add run_front.py
git commit -m "feat(run_front): register order settlement hook"
```

---

## Task 8: Offline order E2E harness `wave3_order_e2e_check.py`

**Files:**
- Create: `adyan-agent-communication-layer/wave3_order_e2e_check.py`

**Interfaces:**
- Consumes: `run_front.build_agent`, `baymax_agents.attach_hospital_handlers`, the chat + payment protocols (all existing).

- [ ] **Step 1: Create the harness (drives the reactive admin-order path)**

Create `adyan-agent-communication-layer/wave3_order_e2e_check.py`:

```python
"""wave3_order_e2e_check.py — Wave 3 end-to-end proof (offline): the admin
approval gate + external supplier order.

    buyer  --ChatMessage("Hospital A is short on IV fluids")--> FRONT
    FRONT  : negotiate B+C -> rank -> AWAITING_APPROVAL (narrates decision)
    buyer  --ChatMessage("order")------------------------------> FRONT   (admin decides)
    FRONT  : order_from_supplier (mock) -> settle_order -> RequestPayment
    buyer  --CommitPayment(tx)---------------------------------> FRONT
    FRONT  : verify (skipped) -> CompletePayment -> ORDERED      => SUCCESS

FRONT is built via run_front.build_agent() (the real deployment path), so this
also proves the order settlement hook is registered. Exits 0 on success.
Env is set BEFORE importing any agent module (import-time reads).
"""

from __future__ import annotations

import os

os.environ["PAYMENT_VERIFY_ONCHAIN"] = "false"     # no live RPC in the sandbox
os.environ["BAYMAX_REDIS"] = "0"                    # deterministic mock inventory
os.environ["BAYMAX_SPARSE_NARRATION"] = "0"        # see AWAITING_APPROVAL narration
os.environ.pop("BAYMAX_EXIT_WHEN_DONE", None)      # buyer drives exit (pay first)
os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3.0")
os.environ.setdefault("BAYMAX_APPROVAL_TIMEOUT", "60")

import agent_base  # noqa: F401,E402  (installs the Python 3.14 event loop)

from uagents import Agent, Bureau, Context, Protocol  # noqa: E402

import run_front  # noqa: E402  (real construction path; registers both hooks)
from agent_base import build_hospital_agent, create_text_chat, now  # noqa: E402
from baymax_agents import attach_hospital_handlers  # noqa: E402
from protocol import (  # noqa: E402
    ChatMessage, ChatAcknowledgement, TextContent, chat_protocol_spec,
    payment_protocol_spec, RequestPayment, CommitPayment, CompletePayment,
    CancelPayment,
)

INTENT = os.getenv("BAYMAX_E2E_INTENT", "Hospital A is short on IV fluids")

front, front_wallet = run_front.build_agent()
hospital_b = build_hospital_agent("Hospital B")
hospital_c = build_hospital_agent("Hospital C")
attach_hospital_handlers(hospital_b, "Hospital B")
attach_hospital_handlers(hospital_c, "Hospital C")

buyer = Agent(name="asi_one_user", seed="baymax-wave3-e2e-buyer-seed",
              port=8300, network="testnet")
_state = {"ordered": False, "ticks": 0}

buyer_chat = Protocol(spec=chat_protocol_spec)


@buyer_chat.on_message(ChatMessage)
async def _buyer_on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=now(),
                                               acknowledged_msg_id=msg.msg_id))
    for item in msg.content:
        if isinstance(item, TextContent):
            ctx.logger.info(f"[buyer<-chat] {item.text}")
            # At the admin gate, choose to ORDER externally (once).
            if "awaiting_approval" in item.text.lower() and not _state["ordered"]:
                _state["ordered"] = True
                ctx.logger.info("[buyer] Admin decision -> 'order' (purchase externally).")
                await ctx.send(sender, create_text_chat("order"))


@buyer_chat.on_message(ChatAcknowledgement)
async def _buyer_on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


buyer_pay = Protocol(spec=payment_protocol_spec, role="buyer")


@buyer_pay.on_message(RequestPayment)
async def _buyer_on_request(ctx: Context, sender: str, msg: RequestPayment):
    funds = msg.accepted_funds[0]
    ctx.logger.info(f"[buyer] RequestPayment: {funds.amount} {funds.currency} -> "
                    f"{msg.recipient} (ref={msg.reference}). Approving (simulated).")
    await ctx.send(sender, CommitPayment(
        funds=funds, recipient=msg.recipient,
        transaction_id="0xWAVE3ORDERTEST", reference=msg.reference))


@buyer_pay.on_message(CompletePayment)
async def _buyer_on_complete(ctx: Context, sender: str, msg: CompletePayment):
    ctx.logger.info(f"[buyer] CompletePayment tx={msg.transaction_id} — "
                    f"WAVE3 ORDER E2E SUCCESS: chat -> negotiate -> approve(order) -> "
                    f"supplier -> settle.")
    os._exit(0)


@buyer_pay.on_message(CancelPayment)
async def _buyer_on_cancel(ctx: Context, sender: str, msg: CancelPayment):
    ctx.logger.error(f"[buyer] CancelPayment: {msg.reason} — WAVE3 E2E FAIL.")
    os._exit(1)


buyer.include(buyer_chat)
buyer.include(buyer_pay)


@buyer.on_event("startup")
async def _buyer_kick(ctx: Context):
    ctx.logger.info(f"[buyer] Sending intent to FRONT {front.address}: {INTENT!r}")
    await ctx.send(front.address, create_text_chat(INTENT))


@buyer.on_interval(period=1.0)
async def _watchdog(ctx: Context):
    _state["ticks"] += 1
    if _state["ticks"] > 30:
        ctx.logger.error("[buyer] watchdog timeout — no CompletePayment within 30s.")
        os._exit(3)


bureau = Bureau()
for _a in (front, hospital_b, hospital_c, buyer):
    bureau.add(_a)


if __name__ == "__main__":
    print("=" * 70)
    print("WAVE 3 E2E: chat -> negotiate -> AWAITING_APPROVAL -> admin ORDER -> "
          "supplier -> settle")
    print(f"  FRONT  : {front.address}  (wallet {front_wallet})")
    print(f"  BUYER  : {buyer.address}")
    print(f"  intent : {INTENT!r}   PAYMENT_VERIFY_ONCHAIN=false (sandbox)")
    print("=" * 70)
    bureau.run()
```

- [ ] **Step 2: Run the order E2E to verify success**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python wave3_order_e2e_check.py 2>&1 | tail -30; echo "EXIT=${PIPESTATUS[0]}"`
Expected: logs show `[AWAITING_APPROVAL]`, the buyer replying `order`, `[ORDERING]` (order prepared with MedSupply Direct), a `RequestPayment` for the `order-...` reference, then `WAVE3 ORDER E2E SUCCESS` and `EXIT=0`.

- [ ] **Step 3: Regression — the trade path E2E still passes**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python wave2_e2e_check.py 2>&1 | tail -5; echo "EXIT=${PIPESTATUS[0]}"`
Expected: `WAVE2 E2E SUCCESS` and `EXIT=0` — but note: the buyer in wave2 does NOT reply to the AWAITING_APPROVAL prompt, so the trade now halts at the gate. **This is expected behavior change.** Update wave2 in the next step so it exercises the new `approve` decision.

- [ ] **Step 4: Update `wave2_e2e_check.py` to approve at the gate**

In `wave2_e2e_check.py`, set full narration before the imports — replace line 29 (`os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3.0")`) by adding after it:

```python
os.environ["BAYMAX_SPARSE_NARRATION"] = "0"  # surface the AWAITING_APPROVAL prompt
os.environ.setdefault("BAYMAX_APPROVAL_TIMEOUT", "60")
```

Then in `_buyer_on_chat` (the `for item in msg.content:` loop, ~line 77), add an approve reply after the existing `ctx.logger.info(...)` line:

```python
        if isinstance(item, TextContent):
            ctx.logger.info(f"[buyer<-chat] {item.text}")
            if "awaiting_approval" in item.text.lower() and not _ticks.get("approved"):
                _ticks["approved"] = True
                ctx.logger.info("[buyer] Admin decision -> 'approve' (authorize trade).")
                await ctx.send(sender, create_text_chat("approve"))
```

- [ ] **Step 5: Re-run the trade E2E to confirm it passes with approval**

Run: `cd adyan-agent-communication-layer && ./.venv/bin/python wave2_e2e_check.py 2>&1 | tail -8; echo "EXIT=${PIPESTATUS[0]}"`
Expected: logs show `[AWAITING_APPROVAL]`, buyer replying `approve`, `[PROPOSING]`/`[SETTLING]`, then `WAVE2 E2E SUCCESS` and `EXIT=0`.

- [ ] **Step 6: Commit**

```bash
cd adyan-agent-communication-layer
git add wave3_order_e2e_check.py wave2_e2e_check.py
git commit -m "test(wave3): offline admin-order E2E; wave2 approves at the new gate"
```

---

## Task 9: Optional deps + documentation

**Files:**
- Modify: `adyan-agent-communication-layer/requirements.txt`, `CLAUDE.md`, `README.md`, `DELIVERABLES.md`

**Interfaces:** none (docs/deps only).

- [ ] **Step 1: Add optional Browserbase deps (commented, not required offline)**

Append to `adyan-agent-communication-layer/requirements.txt`:

```text

# --- Wave 3 (optional): external-supplier order via Browserbase --------------
# Only needed when BAYMAX_BROWSERBASE=1. The order seam falls back to a
# deterministic mock when these are absent, so offline harnesses don't need them.
# stagehand
# playwright
# browserbase
```

- [ ] **Step 2: Document the new env vars + commands in `CLAUDE.md`**

In `CLAUDE.md`, add to the "Key environment variables" table:

```text
| `BAYMAX_BROWSERBASE` | `1`/`true` → `order_from_supplier` drives a vendor site via Stagehand/Playwright over Browserbase (`supplier_order.py`); unset = deterministic mock. Fail-closed to mock. |
| `BAYMAX_SUPPLIER_URL` | Vendor site to drive in Browserbase mode. |
| `BAYMAX_SUPPLIER_WALLET` | `fetch1…` payee for external-order FET settlement; unset → FRONT wallet, narrated as symbolic. |
| `BAYMAX_APPROVAL_TIMEOUT` | Seconds to wait for the admin's approve/order/reject decision before the watchdog auto-fails (default 300). |
| `BAYMAX_REQUIRE_FACILITY_APPROVAL` | Reserved per-facility (B/C) admin gate; default off (auto-approve + notify). When on, currently denies (fail-closed until a real channel exists). |
| `BAYMAX_DEFAULT_ORDER_QTY` | Default quantity for a proactive order when none is stated and there's no shortfall (default 100). |
| `BROWSERBASE_API_KEY` / `BROWSERBASE_PROJECT_ID` / `MODEL_API_KEY` | Browserbase/Stagehand credentials (only for `BAYMAX_BROWSERBASE=1`). |
```

Add to the commands table:

```text
| Offline admin-gate + external-order E2E (chat→negotiate→admin orders→supplier→settle) | `./.venv/bin/python wave3_order_e2e_check.py` |
```

And add a short paragraph under "The negotiation core and its seams" noting: the negotiation now halts at `AWAITING_APPROVAL` after evaluation; a chat reply (`approve`/`order`/`reject`) resumes via `resume_after_admin_decision`; the order branch uses the `order_from_supplier` seam and settles to `BAYMAX_SUPPLIER_WALLET`; B/C accept is gated by `approve_release`. A proactive `order N <item>` chat intent goes straight to the order path via `start_order`, bypassing the shortfall guard.

- [ ] **Step 3: Update `README.md` and `DELIVERABLES.md`**

Add a brief "Admin approval + external order (Wave 3)" subsection to `README.md` describing the in-chat decision (`approve`/`order`/`reject`), the proactive `order N <item>` phrasing, and the `wave3_order_e2e_check.py` proof. In `DELIVERABLES.md`, add a line marking the Wave 3 offline E2E as verifiable and noting the live Browserbase order leg (like the live signed-payment leg) is the path not verifiable offline.

- [ ] **Step 4: Verify the full offline suite is green**

Run:
```bash
cd adyan-agent-communication-layer
./.venv/bin/python check_interfaces_order.py && \
./.venv/bin/python check_parse_decision.py && \
./.venv/bin/python wave2_e2e_check.py >/dev/null 2>&1 && echo "wave2 OK" && \
./.venv/bin/python wave3_order_e2e_check.py >/dev/null 2>&1 && echo "wave3 OK"
```
Expected: `OK check_interfaces_order`, `OK check_parse_decision`, `wave2 OK`, `wave3 OK`.

- [ ] **Step 5: Remove the throwaway checks (optional) and commit docs**

The `check_*.py` scripts are lightweight unit checks; keep them (they're useful regression guards) — they match the repo's script-as-test idiom. Commit the docs:

```bash
cd adyan-agent-communication-layer
git add requirements.txt CLAUDE.md README.md DELIVERABLES.md
git commit -m "docs: Wave 3 admin approval + external supplier order (env, commands, deliverables)"
```

---

## Self-Review

**Spec coverage:**
- (A) External order branch → Tasks 2 (seam+mock), 3 (Browserbase), 4 (settlement), 5 (`_order_path`/`start_order`), 8 (E2E). ✅
- (B) Admin approval gate in ASI:One chat → Tasks 1 (states), 5 (`_evaluate` halt, `resume_after_admin_decision`, watchdog), 6 (chat routing/`parse_decision`). ✅
- "Always offer order alongside pay" → `_request_admin_decision` presents approve+order (order-only when no plan). ✅
- Proactive (no-shortfall) order → `kind:"order"` + `start_order` bypassing shortfall guard (C4). ✅
- B/C per-agent confirm (hybrid) → `approve_release` seam, Task 5 Step 6. ✅
- Review C1 (chat eaten) → Task 6 Step 6 pre-filter before parsing. ✅
- Review C2 (zombie) → Task 5 Step 5 watchdog branch keyed on `approval_deadline`, independent of `evaluated`. ✅
- Review C3 (wrong wallet) → Task 4 `recipient` override + `BAYMAX_SUPPLIER_WALLET` + symbolic description. ✅
- Review S4 (async/to_thread) → sync `supplier_order` invoked via `asyncio.to_thread` (Task 3/5). ✅
- Review S5 (key collision) → `order-<req_id>` vs `pay-<req_id>` + kind dispatch (Task 4). ✅
- Review N1 (echo regex) → Task 6 Step 3 adds new states. ✅
- Review N2 (import-time env) → Task 8 sets env before imports. ✅
- Review N4 (selftest) → dedicated `wave3_order_e2e_check.py`; proactive order regression via selftest intent (Task 6 Step 8). ✅
- Review S1 (enum contract) → additive in-process enum, documented (Task 1 + spec). ✅
- Review S2 (persistence) → watchdog-bounded; documented limitation (spec §5). ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step shows the command + expected output. ✅

**Type consistency:** `SupplierOrder` fields used identically across interfaces/supplier_order/settlement/baymax (`.vendor`, `.quantity`, `.item`, `.total_price`, `.currency`, `.confirmation_ref`, `.live_view_url`). Hook signatures match: `register_order_settlement_hook` fn `(ctx, req_id, order, user_address, reply_to)` == `settle_order_via_payment_protocol(ctx, req_id, order, *, user_address, reply_to=None)`. `resume_after_admin_decision(ctx, req_id, decision)` matches the Task 6 call. `parse_decision` returns the 4 strings consumed in Task 6. ✅
