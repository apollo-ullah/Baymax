# Stockpile — Cross-Hospital Supply Negotiation Agents

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)
![tag:healthcare](https://img.shields.io/badge/healthcare-00B894)

A network of hospital agents that detect supply shortfalls and **autonomously
negotiate and settle inter-facility transfers** — before anyone runs out. A
supply manager states an intent in natural language through ASI:One
("Hospital A is short on IV fluids"); the network finds surplus, composes a
transfer (splitting across facilities when no single one covers the need), and
settles it as a real testnet transaction — narrating each step back in the chat.

This is operational logistics only — *what to move, how much, by when*. Never
clinical guidance. Scope is non-controlled consumables within a single health
system / regional mutual-aid network, where inter-facility transfers are routine.

> Built for the Fetch.ai **"From Intent to Action"** challenge.

---

## Architecture

Three Fetch.ai uAgents run the negotiation:

| Agent | Facility | Role |
| :-- | :-- | :-- |
| `stockpile_front` | Hospital A | **Requester + ASI:One surface.** Carries the Chat Protocol; detects the shortfall, broadcasts the request, ranks offers, composes the transfer, settles it, narrates the result. |
| `stockpile_hospital_b` | Hospital B | **Surplus facility.** Responds with constrained offers; accepts/rejects proposed transfer legs. |
| `stockpile_hospital_c` | Hospital C | **Surplus facility.** Same. |

**The chain (PRD §10):**

```
shortfall_detected → requesting → collecting_offers → evaluating
   → (re_planning if no single offer covers the need) → proposing
   → settling → confirmed
```

**The moment that proves it is real:** no single facility covers the request, so
the front agent composes a **split** (e.g. 150 from the near facility + 50 from
the far one) and settles it — constraint handling plus a real transaction, not a
scripted hand-off.

### Module map

| File | What it is | Owner |
| :-- | :-- | :-- |
| `protocol.py` | **Frozen contract.** All message models (negotiation + re-exported Chat/Payment Protocol) and the negotiation state machine. | shared |
| `interfaces.py` | The two stubbed seams with working mocks: `get_inventory` (Redis seam) and `rank_offers` (Claude seam). | shared |
| `agent_base.py` | Shared building blocks: agent factory, facility registry, address derivation, the Chat Protocol shell. | shared |
| `stockpile_agents.py` | The 3-agent negotiation core + local Bureau runner. | negotiation |
| `hello_world_agent.py` | Phase-0 Chat Protocol proof (live in ASI:One). | — |

### Stubbed seams (deliberately not implemented here)

Both are owned by other workstreams and ship as deterministic mocks so this
layer is never blocked. The function **signatures are the contract**:

- `get_inventory(hospital, item) -> InventoryState` — live stock (Redis). *Mock:* hardcoded scenarios.
- `rank_offers(need, offers) -> RankedPlan` — multi-constraint offer ranking (Claude). *Mock:* nearest-first greedy allocator that produces the canonical split.

---

## Run the local demo

```bash
cd adyan-agent-communication-layer
python -m venv .venv && source .venv/bin/activate     # Python 3.12+ (verified on 3.14)
pip install -r requirements.txt

# Run the 3-agent negotiation in one process (Bureau). Self-exits when done.
STOCKPILE_EXIT_WHEN_DONE=1 python stockpile_agents.py
```

Pick the scenario with `STOCKPILE_ITEM`:

| `STOCKPILE_ITEM` | Demonstrates |
| :-- | :-- |
| `"IV fluids"` (default) | **Split** across two facilities (150 + 50) |
| `"saline"` | **Full cover** by a single facility |
| `"sutures"` | **No offer** — graceful escalation to manual procurement |

```bash
STOCKPILE_EXIT_WHEN_DONE=1 STOCKPILE_ITEM="saline" python stockpile_agents.py
```

### Reach it through ASI:One (Mailbox)

The Phase-0 agent is already ASI:One-compatible. Run it and connect via Mailbox:

```bash
python hello_world_agent.py
```

Open the **Agent Inspector** URL printed in the logs → **Connect** → **Mailbox** →
**Finish**, then chat with it in ASI:One. (The full front-agent + ASI:One
intent flow and on-chain settlement land in the next waves.)

---

## Guardrails

- **Testnet only.** `FETCH_NETWORK=testnet` is forced in `agent_base.py`; payment
  verification uses `NetworkConfig.fetchai_stable_testnet()`. Never mainnet, never real funds.
- **No committed secrets.** Agent seeds and keys live in `.env` (see `.env.example`)
  and are gitignored along with `private_keys.json` and `.venv/`.

---

## Agent addresses

> _Filled in once the agents are registered on Agentverse (SHIP stream)._

| Agent | Address | Agentverse profile |
| :-- | :-- | :-- |
| `stockpile_front` | `agent1q…` | _link_ |
| `stockpile_hospital_b` | `agent1q…` | _link_ |
| `stockpile_hospital_c` | `agent1q…` | _link_ |

## Status

Wave 0 (frozen contract) + working 3-agent negotiation complete. Next: ASI:One
intent agent (FRONT), negotiation hardening (NEGOTIATE), testnet Payment Protocol
settlement (PAY), and Agentverse registration + deliverables (SHIP).
