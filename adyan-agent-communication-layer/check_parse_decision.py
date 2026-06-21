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
