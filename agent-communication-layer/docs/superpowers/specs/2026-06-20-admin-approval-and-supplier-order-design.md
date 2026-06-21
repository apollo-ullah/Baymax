# Design: Admin Approval Gate + Supplier Order (Wave 3)

## State machine addition

```
collecting_offers -> evaluating -> AWAITING_APPROVAL
                                       |
                         +-------------+-------------+
                         v             v             v
                      approve        order         reject
                         |             |             |
                      proposing    ORDERING        FAILED
                         |             |
                      settling     ORDERED (terminal)
                         |
                      CONFIRMED (terminal)
```

## Admin gate protocol

1. FRONT evaluates offers and halts at `AWAITING_APPROVAL`.
2. A `_step()` narration is sent to `reply_to` (ASI:One) describing the plan and options.
3. `resume_after_admin_decision(ctx, req_id, decision)` is called when:
   - The chat user replies "approve/order/reject" (routed by `front_agent.on_intent`).
   - The dashboard UI posts to `/decision` (routed by `run_front._poll_decision`).
4. Watchdog in `offer_timeout` auto-fails after `BAYMAX_APPROVAL_TIMEOUT` seconds.

## Order path

When decision == "order" (or no offers exist and admin chose order):
1. `_order_path` calls `asyncio.to_thread(order_from_supplier, item, qty, hospital=...)`.
2. If `BAYMAX_BROWSERBASE=1`: delegates to `supplier_order.browserbase_order()`.
3. Otherwise: uses `_mock_order_from_supplier()` (deterministic, no external calls).
4. Returns a `SupplierOrder` dataclass.
5. `settle_order()` fires the order settlement hook (real FET via `settle_order_via_payment_protocol`, or stub).

## Seam contracts

`order_from_supplier(item, qty, *, hospital) -> SupplierOrder`
- Signature is the only contract; impl is behind `BAYMAX_BROWSERBASE`.
- Must be synchronous (called via `asyncio.to_thread`).

`approve_release(facility, item, qty) -> bool`
- Called by surplus facilities before accepting a TransferAccept.
- Default auto-approves; opt-in to human confirmation with `BAYMAX_REQUIRE_FACILITY_APPROVAL=1`.
