"""supplier_order.py — the REAL implementation of the external-supplier order
seam (Wave 3), behind interfaces.order_from_supplier().

interfaces.order_from_supplier() ships a deterministic mock so the network is
never blocked on Browserbase. This module is the live backend: it drives a vendor
website with Stagehand/Playwright over a Browserbase cloud browser, navigates to
the product, adds it to the cart, reaches the cart-review step, and extracts the
order total + a confirmation/cart reference — returning the SAME SupplierOrder
shape. interfaces.order_from_supplier() delegates here when BAYMAX_BROWSERBASE=1
and falls back to the mock on ANY failure (the seam must never hang).

This module exposes a SYNC entrypoint on purpose: the async negotiation core
calls it via asyncio.to_thread() (the cosmpy gotcha — you must not block the
event loop). Stagehand 0.5.x is an async library, so browserbase_order() runs the
async flow with asyncio.run() *inside* the worker thread (to_thread gives us a
thread with no running loop, so asyncio.run is safe there). Heavy imports
(stagehand) are done lazily INSIDE the async flow so this module imports cleanly
even when the optional package is absent.

Targets Stagehand 0.5.x (the Playwright-backed agentic page API: page.act /
page.extract). The 3.x line is a different, REST-only SDK — pin `stagehand==0.5.x`.

Env:
    BAYMAX_BROWSERBASE        1/true -> use this backend (default off = mock)
    BAYMAX_SUPPLIER_URL       vendor site to drive (default a demo placeholder)
    BROWSERBASE_API_KEY       Browserbase credentials
    BROWSERBASE_PROJECT_ID
    MODEL_API_KEY             model key for Stagehand act/extract (falls back to
                              ANTHROPIC_API_KEY)
    BAYMAX_SUPPLIER_MODEL     LLM for act/extract (default anthropic/claude-sonnet-4-6)
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

    SYNC entrypoint + raises on any failure (the caller falls back to the mock).
    Runs the async Stagehand flow via asyncio.run(); safe because the negotiation
    core calls this through asyncio.to_thread() (a thread with no running loop),
    and the standalone harnesses call it from the main thread before any agent
    loop is running. Heavy imports are lazy so the module loads without the
    optional deps installed.
    """
    import asyncio

    return asyncio.run(_browserbase_order_async(item, quantity, hospital=hospital))


def _parse_cart_summary(text: str) -> tuple[str, str, float | None, str]:
    """Parse the fixed-format cart summary into (vendor, product, total, currency).

    Stagehand's typed-schema extract returns nulls for numbers/line-item fields on
    many cart pages where prose extraction succeeds, so we ask for a fixed KEY:
    value block and parse it here (regex, no LLM). Robust to missing lines and
    stray '$'/commas.
    """
    import re

    def _field(key: str) -> str:
        m = re.search(rf"^{key}\s*:\s*(.+)$", text, re.I | re.M)
        return m.group(1).strip() if m else ""

    vendor = _field("VENDOR")
    product = _field("ITEM")
    currency = (_field("CURRENCY") or "USD").upper()[:8] or "USD"

    sub = _field("SUBTOTAL")
    total: float | None = None
    m = re.search(r"([\d,]+\.\d{2}|\d[\d,]*)", sub)
    if m:
        try:
            total = float(m.group(1).replace(",", ""))
        except ValueError:
            total = None
    if total is None:  # fallback: largest $-amount anywhere in the summary
        amts = [float(a.replace(",", "")) for a in re.findall(r"\$\s*([\d,]+\.\d{2})", text)]
        total = max(amts) if amts else None
    if product.upper() in ("NONE", "EMPTY", ""):
        product = ""
    return vendor, product, total, currency


async def _browserbase_order_async(item: str, quantity: int, *, hospital: str) -> SupplierOrder:
    from stagehand import Stagehand  # 0.5.x async, Playwright-backed agentic API

    url = os.getenv("BAYMAX_SUPPLIER_URL", "https://www.example-medical-supply.com")
    model_name = os.getenv("BAYMAX_SUPPLIER_MODEL", "anthropic/claude-sonnet-4-6")
    model_api_key = os.getenv("MODEL_API_KEY") or os.environ["ANTHROPIC_API_KEY"]
    timeout_ms = int(os.getenv("BAYMAX_ORDER_TIMEOUT_S", "90")) * 1000

    sh = Stagehand(
        env="BROWSERBASE",
        api_key=os.environ["BROWSERBASE_API_KEY"],
        project_id=os.environ["BROWSERBASE_PROJECT_ID"],
        model_name=model_name,
        model_api_key=model_api_key,
        act_timeout_ms=timeout_ms,
        verbose=1,
    )
    await sh.init()
    # session_id is populated by init(); the dashboard URL is a replayable view.
    session_id = getattr(sh, "session_id", None)
    live_view_url = (
        f"https://www.browserbase.com/sessions/{session_id}" if session_id else None
    )
    try:
        page = sh.page
        await page.goto(url)
        # Decompose the flow into discrete, unambiguous acts — each is one
        # model-driven step, which is far more reliable than one mega-instruction.
        # NOTE: on Shopify-style stores you must OPEN the product page before
        # Add-to-Cart works; a combined "open or add" act only navigates and
        # leaves the cart empty (no order total), so keep these steps separate.
        await page.act(f"type '{item}' into the site search box and submit the search")
        await page.act(f"click the first {item} product in the search results to open its product page")
        await page.act(f"set the quantity field to {int(quantity)} if a quantity input is present")
        await page.act("click the Add to Cart (or Add to Basket) button on this product page")
        # Add-to-Cart on Shopify is an async POST; give it a moment to persist
        # server-side before we navigate, or /cart loads empty (a race we hit).
        import asyncio
        await asyncio.sleep(3)
        # Go straight to the canonical cart page rather than clicking a slide-out
        # drawer (which the extractor's a11y tree often can't read prices from).
        # Shopify always serves the full cart at <origin>/cart.
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        cart_url = f"{parts.scheme}://{parts.netloc}/cart"
        try:
            await page.goto(cart_url)
        except Exception:  # noqa: BLE001 — fall back to clicking the cart UI
            await page.act("open the shopping cart / go to the cart page")
        # Prose extract in a fixed KEY:value format, parsed in Python — far more
        # reliable than Stagehand's typed-schema extract, which returns nulls for
        # cart line-item prices on this and many other stores.
        summary = await page.extract(
            "Look at this shopping cart page and respond with EXACTLY these four "
            "lines and nothing else:\n"
            "VENDOR: <the store/website name>\n"
            "ITEM: <the name of the saline product in the cart, or NONE if the "
            "cart is empty>\n"
            "SUBTOTAL: <the cart subtotal/order total as digits with two decimals, "
            "e.g. 125.00, or NONE if empty>\n"
            "CURRENCY: <the 3-letter currency code, e.g. USD>"
        )
        text = getattr(summary, "extraction", None) or (
            summary if isinstance(summary, str) else str(summary)
        )
        vendor, product, total_price, currency = _parse_cart_summary(text)
        vendor = vendor or "Unknown vendor"
        ref = f"BB-{item.replace(' ', '')[:4].upper()}-{quantity}"
        return SupplierOrder(
            item=item, quantity=int(quantity), vendor=vendor,
            unit_price=(round(total_price / max(int(quantity), 1), 2)
                        if total_price else None),
            total_price=total_price, currency=currency or "USD",
            confirmation_ref=ref, live_view_url=live_view_url, status="prepared",
        )
    finally:
        try:
            await sh.close()
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass
