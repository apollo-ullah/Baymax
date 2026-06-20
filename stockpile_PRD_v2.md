# Stockpile — Product Requirements Document

| | |
| :-- | :-- |
| **Product** | Stockpile |
| **One-liner** | A network of hospital agents that detect supply shortfalls and autonomously negotiate and settle transfers across facilities, before anyone runs out. |
| **Version** | 0.2 (Cal Hacks build, supersedes 0.1) |
| **Status** | In active build |
| **Main track** | Ddoski's Lab (science, engineering, health tech, hardware) |
| **Qualifying surface** | Fetch.ai ASI:One chat (Chat Protocol + Payment Protocol) |

---

## 1. Overview

Hospitals run lean on consumable supplies and absorb uneven demand shocks: one facility runs critically short on an item while another nearby sits on surplus of the same item. Today that imbalance is reconciled slowly and manually, by phone, if it is caught at all.

Stockpile is an autonomous coordination layer across a network of facilities. Each hospital runs an agent that knows its real-time stock (from edge vision and sensors) and its projected demand (from weather and illness signals). When a facility is detected heading into a shortfall, its agent is reachable through ASI:One, negotiates with the other facilities' agents, and settles a transfer as an on-chain transaction. The output is operational and routes into existing transfer and procurement workflows. It never makes clinical decisions.

The novel work is the coordination between institutions, not the perception inside one. Vision-based inventory monitoring is a commodity. The defensible system is the agent network that detects, negotiates, and settles cross-facility imbalance autonomously.

---

## 2. Goals and non-goals

### Goals
- Detect a projected shortfall from the convergence of falling stock and rising forecast demand.
- Resolve it by autonomous agent-to-agent negotiation, reachable and demonstrable through ASI:One.
- Settle the resolved transfer as a real transaction (Fetch Payment Protocol).
- Make the loop legible: ASI:One for the qualifying interaction, a live dashboard for the visual story.

### Non-goals (explicit, and load-bearing for the Q&A)
- **No clinical decision-making.** Operational outputs only: what to move, how much, by when.
- **No autonomous movement of controlled substances.** Scope is non-scheduled consumables; recommendations route to existing custody and procurement protocols rather than executing them.
- **No ambulance or patient routing.** Future direction only.
- **No doctor-to-agent interface.** Future direction only.
- **Not an ERP or procurement replacement.** Stockpile sits on top of existing systems and hands off.
- **No real hospital integration.** The network is simulated for the demo; the agent logic, negotiation, and transaction are real.

---

## 3. Background and premise

The premise is scoped to survive a healthcare-literate judge.

**Real-world grounding.** Within a single health system (multi-facility operators) and within regional hospital mutual-aid compacts, facilities already load-balance supplies across sites, manually. Stockpile automates the detection, the match, and the settlement, then hands the result to the existing transfer process.

**In scope.** High-churn, non-scheduled consumables: IV fluids, saline, PPE, sutures, certain blood products under existing agreements. Inter-facility transfer of these is routine and legally uncomplicated.

**Why now.** Fetch.ai makes independent agents discoverable, conversational, and able to transact (Chat Protocol + Payment Protocol), and Claude can do the multi-constraint reasoning of a human supply manager, at network scale, in seconds.

---

## 4. Target users

| Persona | Need | What Stockpile gives them |
| :-- | :-- | :-- |
| **Supply manager, multi-facility system** (primary) | Never get caught short when the network has slack | Automatic detection and a one-tap transfer |
| **Regional supply-chain lead** | Visibility into imbalance across sites | Live network view and an audit trail of settled transfers |
| **Biomedical / ops staff** | Trust the readings are real | Edge vision plus sensor ground-truth on the dashboard |

---

## 5. The product (MVP spine)

The single chain that must work end to end:

> A facility is detected heading short on an item → its agent (reachable in ASI:One) broadcasts a request to the network → other facilities' agents respond with constrained offers → the agent ranks them with Claude and selects a resolution → the transfer is settled as a Payment Protocol transaction → it is logged and shown live.

Everything beyond P0 in Section 9 layers on top of this and degrades gracefully.

---

## 6. Competitive differentiation

Vision-based inventory monitoring and demand forecasting are mature and commoditized (Chooch and others provide real-time inventory audits, anomaly detection, and ERP-integrated forecasting). Those platforms are single-tenant by design: each customer's data is walled off.

Stockpile is the opposite premise and a different layer: coordination across the wall. A perception platform could be the camera layer underneath Stockpile; it is a component, not a competitor. The hard problem is agent-to-agent negotiation and settlement across organizational boundaries, a multi-agent systems problem, not a computer-vision one. That is why the demo hero is the negotiation and the transaction, not the shelf.

---

## 7. Functional requirements

Priority key: **P0** = qualifying spine, must work. **P1** = real and valuable, cuttable. **P2** = bonus, only if ahead.

| ID | Requirement | Priority |
| :-- | :-- | :-- |
| FR1 | **Edge inventory capture.** Pi + camera captures frames, Claude Vision estimates fill level, writes to Redis. Deterministic fallback so the trigger never hangs. | P0 |
| FR2 | **Per-facility state.** Each facility's live stock in Redis under the agreed schema. | P0 |
| FR3 | **Shortfall detection.** Flag an item below a safety threshold, escalated on convergence with rising forecast demand. | P0 |
| FR4 | **ASI:One-compatible agents.** Hospital agents built as uAgents, implementing the Chat Protocol, registered on Agentverse, reachable in ASI:One. | P0 |
| FR5 | **Inter-facility negotiation.** uAgents messaging across 2 to 3 facilities: request → offer → select → confirm, with real constraints (quantity, distance/ETA, expiry, urgency) and a re-plan on partial offers. | P0 |
| FR6 | **Reasoning agent.** Claude ranks offers and produces the recommendation and rationale, inside the agent. | P0 |
| FR7 | **Payment Protocol settlement.** The resolved transfer settles as a transaction (RequestPayment → CommitPayment → CompletePayment) on testnet FET, the CommitPayment surfaced as the action in ASI:One. | P0 |
| FR8 | **ASI:One demonstration path.** A judge talks to the network agent in ASI:One, triggers the flow, and sees the transaction complete. | P0 |
| FR9 | **Live dashboard.** Per-facility inventory, the shortfall alert, and the negotiation and transfer resolving in real time (Redis pub/sub). The visual hero for the table judges. | P0 |
| FR10 | **Forecast inputs.** Open-Meteo plus a mocked CDC/WHO illness feed in the forecast keys. | P1 |
| FR11 | **Decision observability.** Arize traces each agent decision and shows behavior improving. | P1 |
| FR12 | **Sensor fusion.** Arduino ultrasonic or load cell gives a deterministic ground-truth reading fused with vision. | P1 |
| FR13 | **Historical grounding.** Redis vector search over past usage returns analogs for the forecast. Optional, Redis-prize only. | P2 |
| FR14 | **Voice alert.** Deepgram announces a critical shortfall aloud. | P2 |
| FR15 | **Messaging.** Poke sends the transfer summary over iMessage. | P2 |
| FR16 | **Live web signal.** Browserbase scrapes a live CDC/WHO page instead of the mock. | P2 |
| FR17 | **Intra-facility agents.** Band coordinates forecast / monitor / reasoning agents inside one facility. | P2 |

---

## 8. System architecture

**Two surfaces, one engine.** ASI:One is the Fetch-qualifying surface (a judge interacts there); the Next.js dashboard is the visual surface for the table and Lab judges. Both are driven by the same agent engine and the same Redis state.

**Integration seam: Redis.** Every component reads and writes Redis against one schema, so the four workstreams build in parallel without coupling.

**Data flow.**
1. Edge node (Pi + camera, optional Arduino sensor) writes live inventory for its facility into Redis.
2. The intelligence layer writes forecast demand (and optional historical analogs) into Redis.
3. A judge or user reaches the network agent through **ASI:One** (Chat Protocol, Agentverse-registered).
4. The agent reads inventory and forecast, detects a shortfall, and broadcasts a request across the network over uAgents messaging.
5. Other facilities' agents respond with constrained offers; the requesting agent calls **Claude** to rank and select.
6. The resolved transfer settles via **Payment Protocol** on testnet FET, confirmed by the action in ASI:One.
7. The transfer writes to the Redis transfers stream and publishes on a channel; the dashboard renders it live; optional P2 layers fire voice / iMessage.
8. Arize traces the agent decisions throughout.

**Build tool.** All code is built with Claude Code (Anthropic track).

---

## 9. Fetch integration spec (the qualifying core)

The single rule: **judging is on the chat feature, not the API.** Do not consume the ASI:One API from the backend. Build agents that live in ASI:One.

- **Framework.** Hospital agents are uAgents (Python). They message each other directly; uAgents is the transport for the negotiation.
- **Chat Protocol (mandatory).** The front agent implements the Chat Protocol so it is conversational and reachable through ASI:One.
- **Agentverse.** Register each agent (use the inspector link printed at startup) and add a README in the Overview tab with the agent name and address, so it is discoverable and testable by judges.
- **ASI:One role.** ASI:One is the discovery-and-routing layer; it connects to agents acting as domain experts. The hospital agent is the domain expert and uses Claude internally for reasoning. No conflict between ASI:One and Claude.
- **Payment Protocol (the transaction).** Enable Payment Protocol (FET) on testnet. The settlement handshake is RequestPayment → CommitPayment → CompletePayment; the CommitPayment is the button the judge taps in ASI:One. This is what qualifies the project for the Fetch transaction prize.
- **What not to build.** No REST bridge from FastAPI to Fetch. The qualifying interaction is a judge talking to your agent in ASI:One, not your code calling a Fetch endpoint.

---

## 10. Agent protocol

Each facility is a uAgent on Agentverse with local state: inventory, forecast demand, spare capacity per item, needs. Fetch provides transport, discovery, and settlement; Claude provides the decision logic.

**Negotiation messages.**
- `SupplyRequest` — broadcast on shortfall: `{item, quantity_needed, urgency, needed_by, requester}`
- `SupplyOffer` — from a surplus facility: `{item, quantity_available, distance, eta, expiry, offerer}`
- `TransferProposal` — requester selects and proposes: `{item, quantity, from, to, eta}`
- `TransferAccept` / `TransferReject`

**Settlement messages (Payment Protocol).**
- `RequestPayment` → `CommitPayment` (or `RejectPayment`) → `CompletePayment` (or `CancelPayment`)

**States.** `idle → shortfall_detected → requesting → collecting_offers → evaluating → proposing → settling → confirmed`, with `re_planning` when no single offer satisfies the need.

**The moment that proves it is real.** No single facility fully covers the request: B offers 150 of 200, C offers 80 but is farther with nearer-expiry stock. Claude composes a resolution (split, or ETA-vs-expiry tradeoff) and the agent re-plans. Showing constraint handling, then a real settled transaction, is what separates this from scripted message-passing.

---

## 11. Data model (Redis)

```
hospital:{id}:inventory   (hash)   item -> {qty, pct, status, updated_at}
hospital:{id}:meta        (hash)   name, lat, lng, capacity
hospital:{id}:surplus     (hash)   item -> spare_qty
forecast:{region}         (json)   predicted demand per item
transfers                 (stream) confirmed transfers, full audit trail
history:usage             (vector) OPTIONAL, Redis-prize only
channels: events, alerts  (pub/sub) drive the live dashboard
```

Redis role: real-time state bus plus pub/sub (load-bearing). Vector search is optional and only pursued if chasing the Redis prize. Lock this schema before any feature code.

---

## 12. Non-functional requirements

- **Demo robustness (highest priority).** The spine runs clean three times in a row. Every live dependency (camera, web calls, chain) has a deterministic fallback.
- **Latency.** Removal to settled transfer under ~10 seconds, so it reads as real time.
- **Resilience.** Pi connects to Redis Cloud over outbound TLS (no inbound ports); laptop webcam is the fallback. Payment Protocol runs on auto-funded testnet, no real money.
- **Legibility.** An uncoached judge understands the save in one viewing, in ASI:One and on the dashboard.

---

## 13. Tech stack

Python-first backend, Redis as the seam, JavaScript only on the dashboard.

| Layer | Tool |
| :-- | :-- |
| Agent mesh, chat, settlement | Fetch.ai uAgents + Chat Protocol + Payment Protocol, on Agentverse, reachable in ASI:One |
| Reasoning + vision | Claude (Sonnet 4.6) via Anthropic API; built with Claude Code |
| Edge node | Raspberry Pi 4 + camera; optional Arduino HC-SR04 / HX711 sensor |
| State + pub/sub | Redis Stack (redis-py); vector index optional |
| Embeddings (optional) | sentence-transformers (local) or Voyage AI |
| Observability | Arize Phoenix |
| Dashboard | Next.js / Vite + React + Tailwind, live over WebSocket/SSE |
| Glue | FastAPI |
| Forecast inputs | Open-Meteo + mocked CDC/WHO |
| Optional wow | Deepgram TTS, Poke iMessage, Browserbase |

---

## 14. Workstreams (one owner each)

**A — Agents: chat, negotiation, settlement (Fetch).** The heaviest workstream and the qualifying core. Build the hospital uAgents, Chat Protocol for ASI:One, Agentverse registration, the negotiation with constraints and re-plan, and Payment Protocol settlement on testnet. Give this to the strongest builder. De-risk with a Payment-Protocol-first go/no-go at M1.

**B — Intelligence (Claude).** The reasoning inside the agent: rank offers, produce the recommendation and rationale. Owns the forecast inputs (Open-Meteo + mocked CDC/WHO). Owns the optional Redis vector search if the team chases that prize.

**C — Hardware edge node (Pi + camera).** The live inventory loop and physical wow. Pi captures frames, runs Claude Vision, writes to Redis Cloud. Optional Arduino sensor fused with vision so the trigger never lies. Hard go/no-go at M1; laptop webcam is the fallback.

**D — Frontend, demo, observability.** The dashboard (Redis pub/sub), the ASI:One demo choreography, Arize tracing, and the backup video. Owns how the story reads to judges in both surfaces.

---

## 15. Milestones

- **M0 — Setup + contract** (first 90 min): repo, keys (Anthropic, Fetch/Agentverse, Redis Cloud), schema locked, hardware claimed at 11:15am.
- **M1 — Ugly vertical slice** (by ~hr 5): the chain runs end to end once, even if faked. **Two go/no-go checks:** Pi writes a reading to Redis Cloud, and a bare Payment Protocol transaction fires between two throwaway agents. If either fails, fall back (webcam / defer settlement) and keep moving.
- **M2 — Make it real** (by ~hr 11): real vision trigger, real negotiation on Agentverse via Chat Protocol, agent reachable in ASI:One, real Payment Protocol settlement, dashboard live. Draft the Devpost (deadline midnight Sat).
- **M3 — Convergence + harden** (overnight): convergence trigger, constraint handling and re-plan, Arize traces, polish. Demo path clean three times.
- **M4 — Freeze + rehearse** (Sun AM): feature freeze, backup video, rehearse the 4-min table pitch and 2-min Q&A (Chooch, regulatory, "is it real" answers pre-loaded). Submit by noon.

---

## 16. Success metrics

**Demo success.**
- The full spine runs end to end, live, three times consecutively.
- A judge triggers the flow in ASI:One and a real transaction completes.
- The negotiation visibly handles a constraint (partial offer + re-plan), not a scripted hand-off.
- The trigger fires reliably from a real physical action (sensor + vision agreement).
- An uncoached judge understands the save in one viewing.

**Prize coverage.**

| Prize | What we show |
| :-- | :-- |
| Fetch (co-host) | Agents on Agentverse, reachable in ASI:One via Chat Protocol, with a settled Payment Protocol transaction |
| Anthropic | Built with Claude Code; Claude doing vision + reasoning; a real health swing |
| Redis | Real-time state bus + pub/sub; vector search if pursued |
| Arize (honorable mention) | Traces that demonstrably sharpen behavior |
| Ddoski's Lab (main track) | Health tech + hardware + technical depth |
| Most Technical Hack | Multi-agent negotiation + settlement + edge hardware |

---

## 17. Risks and mitigations

| Risk | Mitigation |
| :-- | :-- |
| Workstream A overloaded (chat + negotiation + payment) | Payment-Protocol-first go/no-go at M1; lean on Fetch's step-by-step guides; consider B pairing on settlement |
| Scope: too many integrations | Three deep (Fetch, Anthropic, Redis-as-plumbing); everything else degrades gracefully |
| Vision flakiness | Arduino sensor ground-truth; controlled lighting; deterministic fallback |
| Negotiation looks scripted | Force a partial offer + re-plan into the demo path |
| Premise credibility in Q&A | Non-scheduled consumables within a system / mutual-aid network; recommendations route to existing protocols |
| Pi networking at venue | Pi to Redis Cloud over outbound TLS; webcam fallback; go/no-go at M1 |
| Payment Protocol setup eats time | Testnet auto-funds; isolate a toy transaction early; defer if it blocks the spine |
| Demo-god failure | Record a backup demo video |

---

## 18. Open questions

- Two facilities or three? (Three enriches the negotiation, adds setup. Decide at M1.)
- Hero item for the demo? (Pick one with a clean physical removal moment.)
- Chase the Redis prize (vector search) or keep Redis as plumbing only?
- Band intra-facility layer: stretch only, decide after M2.

---

## 19. Future direction (out of scope now)

Ambulance and patient-acuity routing; doctor-to-agent interface; live federation across separate operators; Payment Protocol settlement at production scale; real ERP / procurement integration. This is the expansion story for the stage pitch, not the build.
