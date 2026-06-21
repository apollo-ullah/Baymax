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
        _timeout_ms = int(os.getenv("BAYMAX_ORDER_TIMEOUT_S", "90")) * 1000
        page = sh.page
        try:
            page.set_default_timeout(_timeout_ms)
            page.set_default_navigation_timeout(_timeout_ms)
        except Exception:
            pass  # best-effort; not all Stagehand page wrappers expose these

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
