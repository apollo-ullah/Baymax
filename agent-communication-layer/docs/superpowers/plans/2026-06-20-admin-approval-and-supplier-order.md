# Plan: Admin Approval Gate + External Supplier Order (Wave 3)

**Date:** 2026-06-20  
**Status:** Implemented

## Goal

Add a human-in-the-loop approval gate before the negotiation core executes a transfer, and a fallback external-supplier order path when no facility can spare the needed supply.

## Changes

### protocol.py
- Added `AWAITING_APPROVAL`, `ORDERING`, `ORDERED` to `NegotiationState`.

### baymax_agents.py (renamed from stockpile_agents.py)
- `_evaluate()` now halts at `AWAITING_APPROVAL` instead of immediately proposing.
- `resume_after_admin_decision(ctx, req_id, decision)` resumes from the gate.
- `_order_path(ctx, req_id)` executes the external-supplier branch.
- `start_order(ctx, item, quantity)` proactive order without a shortfall.
- Watchdog in `offer_timeout` fires `FAILED` if admin never replies within `BAYMAX_APPROVAL_TIMEOUT`.
- `register_narration_sink(fn)` / `register_order_settlement_hook(fn)` for wiring.

### interfaces.py
- `SupplierOrder` dataclass.
- `order_from_supplier(item, qty, *, hospital)` seam (mock + Browserbase delegation).
- `approve_release(facility, item, qty)` seam (auto-approve default).

### front_agent.py
- `parse_decision(text)` parses "approve/reject/order" replies.
- `on_intent` pre-filter routes admin decisions before normal intent parsing.

### settlement.py
- `settle_order_via_payment_protocol()` for FET settlement of external orders.
- `_finalize_from_payment` routes to `finalize_after_order_payment` for order payments.

### supplier_order.py (new)
- `browserbase_order()` via Stagehand (opt-in with `BAYMAX_BROWSERBASE=1`).

## Test harnesses
- `wave3_order_e2e_check.py` — full Bureau E2E for both the order and approve paths.
- `check_interfaces_order.py` — offline verification of both new seams.
- `check_parse_decision.py` — unit tests for `parse_decision()`.
