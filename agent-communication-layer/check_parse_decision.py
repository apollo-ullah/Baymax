"""check_parse_decision.py — offline test for the AWAITING_APPROVAL admin-gate
classifier parse_decision() and the proactive-order intent parse_intent()."""

from front_agent import parse_decision, parse_intent

# --- parse_decision(text) -> "approve" | "order" | "reject" | "unclear" ----
assert parse_decision("approve") == "approve", parse_decision("approve")
assert parse_decision("Approve") == "approve", parse_decision("Approve")   # case-insensitive
assert parse_decision("yes, go ahead") == "approve"
assert parse_decision("order instead") == "order"
assert parse_decision("let's buy it externally") == "order"
assert parse_decision("no, reject this") == "reject"
assert parse_decision("cancel") == "reject"
assert parse_decision("ok") == "approve", parse_decision("ok")
assert parse_decision("okay") == "approve", parse_decision("okay")
# Ambiguous (echo of the prompt names all three) -> unclear, never a wrong action.
assert parse_decision("reply approve to trade or order or reject") == "unclear"

# --- parse_intent(text) -> dict --------------------------------------------
# Proactive external-order intent ("order N <item>").
p = parse_intent("order 500 saline")
assert p["kind"] == "order" and p["item"] == "saline" and p["quantity"] == 500, p
p2 = parse_intent("order 100 IV fluids")
assert p2["kind"] == "order" and p2["item"] == "IV fluids" and p2["quantity"] == 100, p2
# Order with no quantity stated -> quantity is None (caller falls back to a default).
p3 = parse_intent("order saline")
assert p3["kind"] == "order" and p3["item"] == "saline" and p3["quantity"] is None, p3

# Plain shortfall is still a request, not an order (quantity lives under quantity_needed).
r = parse_intent("Hospital A is short on IV fluids")
assert r["kind"] == "request" and r["item"] == "IV fluids", r
r2 = parse_intent("we're short 200 saline")
assert r2["kind"] == "request" and r2["item"] == "saline" and r2["quantity_needed"] == 200, r2

print("OK check_parse_decision")
