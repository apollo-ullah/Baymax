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
