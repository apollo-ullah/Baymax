# Baymax — Fetch.ai Sponsor Track

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)
![tag:healthcare](https://img.shields.io/badge/healthcare-00B894)

A network of hospital uAgents that detect supply shortfalls and **autonomously negotiate and settle inter-facility transfers** — before anyone runs out. A supply manager states an intent in natural language through ASI:One ("Hospital A is short on IV fluids"); the network finds surplus, composes a transfer (splitting across facilities when no single one covers the need), and settles it as a real testnet transaction — narrating each step back in the chat.

> Operational logistics only — *what to move, how much, by when.* Never clinical guidance.

---

## How we use Fetch.ai

### uAgents

Three independent `uagents.Agent`s run the negotiation — one per hospital. Each agent has a deterministic identity (derived from a seed), discovers the others via the Almanac, and communicates over the uAgents message bus. All agent construction goes through `agent_base.build_hospital_agent(...)`, which enforces testnet-only networking and installs a pre-3.14 event loop fix so the framework initializes cleanly on Python 3.14.

| Agent | Address | Role |
| :-- | :-- | :-- |
| `baymax_front` | `agent1qtmgxmgr6l8jzegay8wketwm576g58ndarpjzrvrd70jxtfg84wmujwcvau` | Requester + ASI:One surface |
| `baymax_hospital_b` | `agent1qf6xup6ayvegczq0nq829wcf8smvlxharjkj7fa67ezymq2ye4dcujrheke` | Surplus facility B |
| `baymax_hospital_c` | `agent1qd0jd7w0t6t5xzx5myupdajyk2z65zm9xsvrag2cn3909z6qmyg76kdwftt` | Surplus facility C |

### Chat Protocol (v0.3.0)

The front agent carries the **official** `AgentChatProtocol` from `uagents_core.contrib.protocols.chat`. It is re-exported unchanged from `protocol.py` — ASI:One matches by schema digest, so a local redefinition would be a different, incompatible protocol. Every negotiation milestone (shortfall detected, offers collected, transfer composed, settled) streams back to the chat user as a `ChatMessage` in real time.

The intent parser (`front_agent.parse_intent`) turns "Hospital A is short on IV fluids" into a structured `SupplyRequest`. It has substantial hardening against ASI:One's echo loop: milestone/meta regexes, an echo-chatter heuristic, and a per-sender cooldown prevent the chat surface's own narration from being re-ingested as a new user intent.

### Payment Protocol (v0.1.0)

The front agent also carries `AgentPaymentProtocol` (seller role). When a transfer is settled, `settlement.py` sends `RequestPayment` to the chat user — the in-chat FET payment card renders in ASI:One because `metadata["provider_agent_wallet"]` and `metadata["fet_network"]` are populated. The user signs; we receive `CommitPayment`; we verify the transaction on-chain via cosmpy (in a worker thread, to avoid blocking the event loop); we reply `CompletePayment` and emit the terminal narration.

Verified live: on-chain tx `912BB2030A5F14467F434D4CDB0F1DDE23EE6E72E19B9F778998D17A5046557F` on Dorado testnet.

### Agentverse / Mailbox

Each agent runs with `mailbox=True` and `publish_agent_details=True`, making it reachable from ASI:One without a public endpoint. The one-time browser Mailbox connect is the only manual step.

---

## The negotiation

```
shortfall_detected → requesting → collecting_offers → evaluating
   → (re_planning if no single offer covers the need) → proposing → settling → confirmed
```

The moment that proves it is real: no single facility covers the request, so the agent re-plans into a **split transfer** (150 units from Hospital B + 50 from Hospital C), proposes each leg independently, collects accepts, and then triggers on-chain settlement. This is real constraint-handling, not scripted message-passing.

---

## The wire contract

`protocol.py` is the single source of truth for every cross-agent message and the negotiation state machine. Stockpile negotiation models (`SupplyRequest`, `SupplyOffer`, `TransferProposal`, `TransferAccept`, `TransferReject`, `NegotiationState`, `Urgency`) are defined here and imported everywhere. No module redefines them.

---

## Run

```bash
cd agent-communication-layer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# One-process Bureau demo (self-exits when done)
STOCKPILE_EXIT_WHEN_DONE=1 ./.venv/bin/python stockpile_agents.py

# Full offline end-to-end including settlement
./.venv/bin/python wave2_e2e_check.py

# Live Mailbox mode (three terminals)
./.venv/bin/python run_hospital_b.py
./.venv/bin/python run_hospital_c.py
./.venv/bin/python run_front.py
```

Scenarios via `STOCKPILE_ITEM`: `"IV fluids"` (split, default) / `"saline"` (full cover) / `"sutures"` (no offer → escalation).

---

## Key files

| File | What it does |
| :-- | :-- |
| `protocol.py` | Frozen wire contract — all message models + state machine |
| `agent_base.py` | Agent factory, testnet guardrail, event-loop fix |
| `stockpile_agents.py` | 3-agent negotiation Bureau (one-process demo) |
| `front_agent.py` | Chat intent parser, narration, negotiation kickoff |
| `settlement.py` | Payment Protocol handler — RequestPayment → CommitPayment → CompletePayment |
| `interfaces.py` | Redis + Claude ranking seams (fail-closed mocks when deps unavailable) |
| `run_front.py` | Live entrypoint — wires Payment Protocol + settlement hook onto front agent |
| `run_hospital_b.py` / `run_hospital_c.py` | Surplus facility Mailbox runners |
| `wave2_e2e_check.py` | Offline end-to-end harness (chat → negotiate → settle → pay) |
