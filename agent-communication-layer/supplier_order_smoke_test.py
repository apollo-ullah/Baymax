"""supplier_order_smoke_test.py — drive the LIVE Browserbase supplier-order leg.

Calls the same seam the negotiation uses (interfaces.order_from_supplier) so we
exercise the real Stagehand-over-Browserbase flow: open the vendor site, search
for saline, add the first result to the cart, reach the cart-review page, and
extract the order total + reference. Prints the resulting SupplierOrder.

Run:
    BAYMAX_BROWSERBASE=1 BAYMAX_SUPPLIER_URL="https://www.saveritemedical.com" \
      ./.venv/bin/python supplier_order_smoke_test.py [item] [quantity]

Requires BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID, MODEL_API_KEY (or
ANTHROPIC_API_KEY) in the environment / .env. On any failure the seam falls back
to the deterministic mock — this harness flags which backend actually ran so a
silent mock-fallback can't masquerade as a live run.
"""

from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

import interfaces
import supplier_order


def main() -> int:
    item = sys.argv[1] if len(sys.argv) > 1 else "saline"
    quantity = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    hospital = os.getenv("BAYMAX_TEST_HOSPITAL", "Hospital A")

    live = supplier_order.browserbase_enabled()
    url = os.getenv("BAYMAX_SUPPLIER_URL", "(default placeholder)")
    print(f"[smoke] backend={'BROWSERBASE (live)' if live else 'MOCK'}  url={url}")
    print(f"[smoke] ordering {quantity} x {item!r} for {hospital} ...")
    if live:
        for k in ("BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"):
            if not os.getenv(k):
                print(f"[smoke] WARNING: {k} not set — live run will fail to mock")

    t0 = time.monotonic()
    order = interfaces.order_from_supplier(item, quantity, hospital=hospital)
    dt = time.monotonic() - t0

    # A live order carries a Browserbase live_view_url; the mock never does.
    ran_live = bool(order.live_view_url)
    print(f"\n[smoke] completed in {dt:.1f}s  (backend actually used: "
          f"{'BROWSERBASE' if ran_live else 'MOCK fallback'})")
    print("-" * 60)
    print(f"  vendor         : {order.vendor}")
    print(f"  item           : {order.item}")
    print(f"  quantity       : {order.quantity}")
    print(f"  unit_price     : {order.unit_price}")
    print(f"  total_price    : {order.total_price} {order.currency}")
    print(f"  confirmation   : {order.confirmation_ref}")
    print(f"  status         : {order.status}")
    print(f"  live_view_url  : {order.live_view_url}")
    print("-" * 60)

    if live and not ran_live:
        print("[smoke] FAIL: requested live Browserbase but fell back to the mock.")
        return 1
    print("[smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
