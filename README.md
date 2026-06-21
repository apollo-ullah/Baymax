# Stockpile

**A network of hospital AI agents that detect supply shortfalls and autonomously negotiate and settle inter-facility transfers — before anyone runs out.**

A supply manager states an intent in plain English through ASI:One — *"Hospital A is short on IV fluids"* — and the network takes it from there: it broadcasts the need, collects constrained offers from facilities with surplus, ranks them with Claude, composes a (possibly split) transfer, and settles it as a real on-chain testnet FET transaction — narrating every step back into the chat.

> Built at Cal Hacks for the Fetch.ai **"From Intent to Action"** challenge.
> Operational logistics only — *what to move, how much, by when.* Never clinical guidance.

---

## Inspiration

Hospitals run lean on consumable supplies and absorb uneven demand shocks. One facility runs critically short on an item while another a few miles away sits on a surplus of the exact same thing. Inside multi-facility health systems and regional mutual-aid compacts, this imbalance already gets reconciled — but slowly, by phone, and only if someone catches it in time.

The perception problem (counting what's on the shelf) is a solved commodity. The hard, unsolved problem is **coordination across organizational boundaries**: detecting the imbalance, matching surplus to shortfall under real constraints, and settling the transfer — autonomously, before the shortage becomes a crisis. That's a multi-agent systems problem, and it's exactly what Fetch.ai's agent mesh + Claude's reasoning make possible for the first time.

## What it does

Stockpile runs one agent per hospital. Each agent knows its live stock (from edge vision) and its forecast demand (from weather + illness signals). The end-to-end spine:

1. **A shortfall is detected** — falling stock converges with rising forecast demand.
2. **The front agent broadcasts a request** to the network over uAgents messaging.
3. **Surplus facilities answer with constrained offers** — quantity available, distance/ETA, expiry, urgency.
4. **Claude ranks the offers and composes a resolution.** When no single facility covers the need, it splits the order (e.g. **150 units from the near hospital + 50 from the far one**) or trades ETA against expiry, and re-plans on partial offers.
5. **The transfer settles as a real transaction** via the Fetch Payment Protocol on Dorado testnet — `RequestPayment → CommitPayment → CompletePayment` — with the payment card surfacing right in the ASI:One chat for the user to sign.
6. **Everything is logged and shown live** — a Redis-backed dashboard renders inventory, the shortfall alert, and the negotiation resolving in real time.

**The moment that proves it's real:** the negotiation visibly handles a constraint — a partial offer plus a re-plan into a split transfer — and then settles an actual on-chain payment. That's what separates this from scripted message-passing.

## How we built it

Two surfaces, one engine. **ASI:One** is the Fetch-qualifying conversational surface; a **live dashboard** is the visual story. Both are driven by the same agents and the same **Redis** state bus — the integration seam that let four workstreams build in parallel without coupling.

| Layer | What we used |
| :-- | :-- |
| **Agent mesh, chat, settlement** | Fetch.ai **uAgents** + **Chat Protocol** + **Payment Protocol**, registered on Agentverse, reachable in **ASI:One** |
| **Reasoning + vision** | **Claude (Sonnet 4.6)** via the Anthropic API for offer ranking + shelf vision; entire codebase built with **Claude Code** |
| **State + pub/sub** | **Redis Stack** — inventory, surplus, forecast, transfer audit stream, and live event channels |
| **Edge inventory** | MacBook / Pi camera → **Claude Vision** counts saline across a green-straw divider → writes straight to Redis |
| **Forecast inputs** | Open-Meteo weather agent + a mocked CDC/WHO illness agent, both writing to `forecast:{region}` |
| **Observability** | **Arize Phoenix** traces the full decision chain (inventory → forecast → reasoning → transfer → outcome) |
| **Human-in-the-loop** | An iMessage approval pipeline (Hospital A ↔ B) with tappable Accept/Reject links via Messages.app |

### The agent network

Three Fetch.ai uAgents run the negotiation:

| Agent | Facility | Role |
| :-- | :-- | :-- |
| `stockpile_front` | Hospital A | **Requester + ASI:One surface.** Carries the Chat + Payment protocols; detects the shortfall, broadcasts the request, ranks offers, composes the transfer, settles it, narrates back. |
| `stockpile_hospital_b` | Hospital B | **Surplus facility** — responds with constrained offers, accepts/rejects transfer legs. |
| `stockpile_hospital_c` | Hospital C | Same. |

A **frozen wire contract** (`protocol.py`) is the single source of truth for every cross-agent message and the negotiation state machine, so no module ever drifts. The official Fetch Chat/Payment protocol classes are re-exported *unchanged* — ASI:One matches by schema digest, so a local copy would be a different, incompatible protocol.

The negotiation core runs the PRD state machine:

```
shortfall_detected → requesting → collecting_offers → evaluating
   → (re_planning if no single offer covers the need) → proposing → settling → confirmed
```

The Redis seam and the Claude ranking seam sit behind deterministic mocks so the spine never hangs — every live dependency (camera, web calls, chain) has a fail-closed fallback. The system is **testnet-only by construction**: mainnet is refused at startup and payment verification is pinned to Fetch's stable testnet.

## Challenges we ran into

- **ASI:One's LLM echo loop** — the chat surface parrots our own narration back as if it were a new user intent. We hardened the intent parser with milestone/meta regexes, an echo-chatter heuristic, and a per-sender cooldown.
- **The payment card wouldn't render** until we learned ASI:One builds the in-chat FET card from `RequestPayment.metadata` (`provider_agent_wallet`, `fet_network`) — send `metadata=None` and it rejects the message before any approval.
- **Python 3.14 removed the implicit event loop** that uAgents 0.25 still relies on, so import order became load-bearing.
- **Never block the event loop** — cosmpy's on-chain tx verification can stall ~20s on a slow RPC, so we push it to a worker thread.
- **Poke's iMessage API silently stopped delivering** (returned success, sent nothing), so we pivoted the approval pipeline to drive Messages.app directly via AppleScript.

## Accomplishments we're proud of

- **A full, live run on ASI:One + Dorado testnet:** natural-language intent → 3-hospital negotiation → split transfer → in-chat TestFET payment → on-chain settlement → confirmed. Real on-chain transactions, verified by hash.
- The negotiation **genuinely handles constraints** — partial offers, re-planning, and a real split — instead of a canned hand-off.
- A clean **Redis integration seam** that let agents, vision, forecasting, dashboard, and observability all build in parallel.
- The whole wired path (chat → negotiate → settle → pay) is **reproducible offline** with deterministic harnesses — the demo runs clean three times in a row.

## What we learned

The defensible system isn't the camera — it's the agent network that detects, negotiates, and settles across the wall between institutions. Getting agents to *transact*, not just message, is where the real value (and the real engineering) lives. We also learned how much disciplined contract-freezing and fail-closed fallbacks matter when a demo depends on a live chain, a camera, and a third-party chat surface all at once.

## What's next

Ambulance and patient-acuity routing, a doctor-to-agent interface, live federation across separate operators, Payment Protocol settlement at production scale, and real ERP / procurement integration. Stockpile sits *on top of* existing transfer workflows and hands off — it never replaces them, and it never makes a clinical call.

---

## Repository layout

All commands run from inside the relevant track directory.

| Path | What it is |
| :-- | :-- |
| `agent-communication-layer/` | The Fetch agent mesh — negotiation core, FRONT/ASI:One agent, Payment Protocol settlement, Mailbox runners |
| `redis/` | The Redis integration seam — schema, inventory/forecast/alerts/transfers, pub/sub, optional vector history |
| `fetch/` | Forecast input agents (weather, illness) + the iMessage human-approval pipeline |
| `hardware/camera connection/` | Claude Vision shelf-counting → Redis |
| `arize/` | Arize Phoenix decision-chain tracing |
| `stockpile_PRD_v2.md` | The full product spec |

## Quickstart — run the negotiation locally

```bash
cd agent-communication-layer
python -m venv .venv && source .venv/bin/activate     # Python 3.12+
pip install -r requirements.txt

# 3-agent negotiation in one process; self-exits when done.
STOCKPILE_EXIT_WHEN_DONE=1 python stockpile_agents.py
```

Pick the scenario with `STOCKPILE_ITEM`:

| `STOCKPILE_ITEM` | Demonstrates |
| :-- | :-- |
| `"IV fluids"` (default) | **Split** across two facilities (150 + 50) |
| `"saline"` | **Full cover** by a single facility |
| `"sutures"` | **No offer** — graceful escalation to manual procurement |

For the live ASI:One + Agentverse path (per-agent Mailbox mode), Redis bring-up, the camera→Redis loop, and the full requirement matrix, see [`agent-communication-layer/README.md`](agent-communication-layer/README.md) and [`agent-communication-layer/DELIVERABLES.md`](agent-communication-layer/DELIVERABLES.md).

## Built with

`fetch.ai` · `uagents` · `agentverse` · `asi:one` · `chat-protocol` · `payment-protocol` · `claude` · `claude-code` · `anthropic` · `redis` · `arize-phoenix` · `opencv` · `open-meteo` · `fastapi` · `python` · `next.js`
</content>
</invoke>
