"""check_parse_decision.py — quick offline test for parse_decision()."""

from front_agent import parse_decision

cases = [
    ("approve",             {"kind": "decision", "decision": "approve", "req_id": None}),
    ("Approve",             {"kind": "decision", "decision": "approve", "req_id": None}),
    ("approve abc123",      {"kind": "decision", "decision": "approve", "req_id": "abc123"}),
    ("APPROVE abc123",      {"kind": "decision", "decision": "approve", "req_id": "abc123"}),
    ("reject",              {"kind": "decision", "decision": "reject",  "req_id": None}),
    ("reject def456",       {"kind": "decision", "decision": "reject",  "req_id": "def456"}),
    ("order 100 IV fluids", {"kind": "order", "item": "IV fluids", "quantity": 100}),
    ("order saline",        {"kind": "order", "item": "saline"}),
    ("order 50 saline",     {"kind": "order", "item": "saline", "quantity": 50}),
    ("Hospital A is short on IV fluids", {"kind": "none"}),
    ("we're short 200 saline",           {"kind": "none"}),
    ("",                    {"kind": "none"}),
]

for text, expected in cases:
    result = parse_decision(text)
    for k, v in expected.items():
        assert result.get(k) == v, f"FAIL {text!r}: expected {k}={v!r}, got {result!r}"
    print(f"OK  {text!r} -> {result}")

print("\nparse_decision: all checks passed.")
