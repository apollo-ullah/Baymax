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
| `front_agent.py` | ASI:One-facing FRONT agent: NL intent → negotiation, with chat narration + a Bureau self-test. | front |
| `settlement.py` | Testnet FET Payment Protocol (seller role): `RequestPayment → CommitPayment → CompletePayment`. | pay |
| `run_front.py` | Per-agent **Mailbox** runner for Hospital A: chat **+** payment protocols + the settlement bridge. | ship |
| `run_hospital_b.py` / `run_hospital_c.py` | Per-agent **Mailbox** runners for the surplus facilities. | ship |
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

---

## Run as separate Agentverse agents (per-agent Mailbox mode)

For Agentverse registration / live ASI:One, each agent runs in **its own process**
and connects via its **own Mailbox** (no public inbound endpoint needed). Three
runners — one per facility — are the entrypoints. Each imports `agent_base`
first (the Python-3.14 event-loop rule), pins `FETCH_NETWORK=testnet`, prints its
address, and runs.

| Runner | Agent | Protocols carried |
| :-- | :-- | :-- |
| `run_front.py` | `stockpile_front` (Hospital A) | **Chat Protocol** + **Payment Protocol** (both `publish_manifest=True`) |
| `run_hospital_b.py` | `stockpile_hospital_b` (Hospital B) | negotiation Models only |
| `run_hospital_c.py` | `stockpile_hospital_c` (Hospital C) | negotiation Models only |

Run each in a **separate terminal** (order does not matter):

```bash
./.venv/bin/python run_hospital_b.py     # surplus facility B
./.venv/bin/python run_hospital_c.py     # surplus facility C
./.venv/bin/python run_front.py          # Hospital A — ASI:One + payment entrypoint
```

Each prints a banner with its **address** and the Agent **Inspector URL**. For
each agent, do the one-time Agentverse Mailbox connect:

1. Open the **Agent Inspector** URL printed in that agent's logs.
2. **Connect → Mailbox → Finish** (one-time per agent; needs a browser login).
3. Open the agent's **Agentverse profile** and copy the URL into `DELIVERABLES.md`.

`run_front.py` additionally wires settlement into the front agent: it attaches the
PAY stream's **Payment Protocol** (seller role), registers the agent's FET wallet,
and registers the settlement handler on the negotiation core so a **settled**
transfer triggers a real testnet `RequestPayment` in the same ASI:One conversation
(see “Settlement wiring” below).

### Use it from ASI:One (live)

Once `run_front.py`'s Mailbox is connected:

1. Go to **[ASI:One](https://asi1.ai)** and find the `stockpile_front` agent (by
   its address, below).
2. Send a natural-language intent, e.g.:
   - `Hospital A is short on IV fluids`  *(split across B + C)*
   - `we're short 100 saline`            *(full cover by B)*
   - `need sutures at Hospital A`        *(no offer → graceful escalation)*
3. Watch each negotiation milestone (`shortfall_detected → requesting →
   collecting_offers → evaluating → proposing → settling → confirmed`) stream
   back into the chat.
4. On `confirmed`, approve + sign the **FET payment** request in your ASI:One
   wallet to settle the transfer on testnet.

> Phase-0 sanity check: `hello_world_agent.py` is a minimal ASI:One-compatible
> Chat Protocol agent (`./.venv/bin/python hello_world_agent.py`) if you only want
> to verify Mailbox + chat plumbing.

### Settlement wiring (`run_front.py`)

The negotiation core ends a successful deal at `stockpile_agents.settle_transfer()`.
`run_front.py` registers the real handler on the core via
`stockpile_agents.register_settlement_hook(settlement.settle_via_payment_protocol)`,
so the instant a deal settles, `settle_transfer()` delegates to the Payment Protocol —
sending a `RequestPayment` to the ASI:One chat user for the **final** settled plan
(post re-plan). The user's `CommitPayment` is verified on-chain (in a worker thread)
and answered with `CompletePayment` / `CancelPayment`. No polling, no double-fire.
Per-transfer pricing is honored via `STOCKPILE_PAYMENT_PER_UNIT_FET` (falls back to the
flat `STOCKPILE_PAYMENT_AMOUNT_FET`). The whole wired path (chat → negotiate → settle →
pay) is proven offline by `wave2_e2e_check.py`.

---

## Guardrails

- **Testnet only.** `FETCH_NETWORK=testnet` is forced in `agent_base.py`; payment
  verification uses `NetworkConfig.fetchai_stable_testnet()`. Never mainnet, never real funds.
- **No committed secrets.** Agent seeds and keys live in `.env` (see `.env.example`)
  and are gitignored along with `private_keys.json` and `.venv/`.

---

## Agent addresses

Deterministic from each agent's seed (`agent_base.address_for(...)`); stable across
restarts as long as the `*_SEED` env vars are unchanged. These are the addresses the
runners above print and that you use to find the agents on ASI:One / Agentverse.

| Agent | Facility | Address | Agentverse profile |
| :-- | :-- | :-- | :-- |
| `stockpile_front` | Hospital A | `agent1qtmgxmgr6l8jzegay8wketwm576g58ndarpjzrvrd70jxtfg84wmujwcvau` | _fill after Mailbox connect_ |
| `stockpile_hospital_b` | Hospital B | `agent1qf6xup6ayvegczq0nq829wcf8smvlxharjkj7fa67ezymq2ye4dcujrheke` | _fill after Mailbox connect_ |
| `stockpile_hospital_c` | Hospital C | `agent1qd0jd7w0t6t5xzx5myupdajyk2z65zm9xsvrag2cn3909z6qmyg76kdwftt` | _fill after Mailbox connect_ |

> The Agentverse profile links are filled in by hand once each agent's one-time
> Mailbox connect is done — see `DELIVERABLES.md` for the checklist. Addresses
> shown are for the default dev seeds; set the `STOCKPILE_*_SEED` env vars for a
> real deployment (the addresses will then change accordingly).

Re-derive them at any time:

```bash
./.venv/bin/python -c "import agent_base; print(*(f'{f}: {agent_base.address_for(f)}' for f in ('Hospital A','Hospital B','Hospital C')), sep=chr(10))"
```

## Status

Wave 0 (frozen contract) + the 3-agent negotiation, the ASI:One FRONT agent
(`front_agent.py`), the testnet Payment Protocol (`settlement.py`), and the
per-agent Mailbox runners (`run_*.py`, SHIP) are all in place. Remaining work is
the **manual, browser-gated** Agentverse Mailbox connect for each agent + the live
ASI:One demo (tracked in `DELIVERABLES.md`). The Wave-2 direct settlement seam is
wired and verified end to end (`wave2_e2e_check.py`, clean 3×). See `DELIVERABLES.md`
for the submission checklist + requirement matrix.
