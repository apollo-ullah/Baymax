"""check_interfaces_order.py — verifies the order_from_supplier and approve_release
seams work correctly in mock mode (no Browserbase, no Redis needed)."""

from interfaces import approve_release, order_from_supplier

order = order_from_supplier("IV fluids", 50, hospital="Hospital A")
assert order.item == "IV fluids", f"Expected 'IV fluids', got {order.item!r}"
assert order.quantity == 50, f"Expected 50, got {order.quantity}"
assert order.vendor, "vendor should be non-empty"
assert order.status == "prepared", f"Expected 'prepared', got {order.status!r}"
print(f"order_from_supplier OK: {order.quantity} {order.item} from {order.vendor} "
      f"(${order.total_price} {order.currency}, ref={order.confirmation_ref!r})")

order2 = order_from_supplier("saline", 200, hospital="Hospital B")
assert order2.item == "saline"
assert order2.quantity == 200
print(f"order_from_supplier OK: {order2.quantity} {order2.item} from {order2.vendor}")

ok = approve_release("Hospital B", "IV fluids", 50)
assert isinstance(ok, bool)
print(f"approve_release OK: Hospital B -> {ok}")

ok2 = approve_release("Hospital C", "saline", 100)
assert isinstance(ok2, bool)
print(f"approve_release OK: Hospital C -> {ok2}")

print("check_interfaces_order: all checks passed.")
