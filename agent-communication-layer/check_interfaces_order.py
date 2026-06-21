"""check_interfaces_order.py — offline verification of the Wave-3 interfaces seams
(mock path): order_from_supplier + approve_release. No Browserbase, no Redis."""
import os
os.environ.pop("BAYMAX_BROWSERBASE", None)  # force the deterministic mock path

from interfaces import order_from_supplier, approve_release, SupplierOrder

# --- order_from_supplier (SEAM 3) -----------------------------------------
o = order_from_supplier("IV fluids", 200, hospital="Hospital A")
assert isinstance(o, SupplierOrder), o
assert o.item == "IV fluids" and o.quantity == 200, o
assert o.vendor and o.total_price and o.total_price > 0, o
assert o.status == "prepared", o
assert o.confirmation_ref, o
print(f"order_from_supplier OK: {o.quantity} {o.item} from {o.vendor} "
      f"(${o.total_price} {o.currency}, ref={o.confirmation_ref!r})")

# Deterministic: same inputs -> same confirmation_ref.
o2 = order_from_supplier("IV fluids", 200, hospital="Hospital A")
assert o.confirmation_ref == o2.confirmation_ref, (o.confirmation_ref, o2.confirmation_ref)

# A second item / facility exercises the vendor table + per-hospital ref.
o3 = order_from_supplier("saline", 50, hospital="Hospital B")
assert isinstance(o3, SupplierOrder), o3
assert o3.item == "saline" and o3.quantity == 50, o3
assert o3.vendor and o3.total_price and o3.total_price > 0, o3
assert o3.status == "prepared", o3
print(f"order_from_supplier OK: {o3.quantity} {o3.item} from {o3.vendor} "
      f"(${o3.total_price} {o3.currency}, ref={o3.confirmation_ref!r})")

# --- approve_release (SEAM 4) ---------------------------------------------
# Default auto-approves; the reserved flag denies (fail-closed).
ok = approve_release("Hospital B", "IV fluids", 150)
assert ok is True, ok
print(f"approve_release OK: Hospital B -> {ok}")

ok2 = approve_release("Hospital C", "saline", 100)
assert ok2 is True, ok2
print(f"approve_release OK: Hospital C -> {ok2}")

os.environ["BAYMAX_REQUIRE_FACILITY_APPROVAL"] = "1"
assert approve_release("Hospital B", "IV fluids", 150) is False
os.environ.pop("BAYMAX_REQUIRE_FACILITY_APPROVAL", None)
print("approve_release OK: reserved gate denies (fail-closed)")

print("check_interfaces_order: all checks passed.")
