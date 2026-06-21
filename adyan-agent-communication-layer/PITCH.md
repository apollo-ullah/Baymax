# Baymax — the AI brain for hospital supply logistics

> **A network of Fetch.ai uAgents that catch hospital supply shortfalls, then autonomously negotiate and settle an inter-facility transfer — with Claude reasoning at every decision point and a real testnet FET payment at the end.**

Built for Cal Hacks · competing for the **Anthropic** and **Fetch.ai "From Intent to Action"** prizes.

---

## The problem

Hospitals run dangerously lean on consumables — IV fluids, saline, sutures. When one site dips below its safety threshold, restocking is a manual scramble: someone phones around sister facilities, eyeballs who has spare, haggles over how much and how fast, and files paperwork. Meanwhile, surplus stock at a nearby facility quietly **expires on the shelf**. The result is the worst of both worlds: shortages in one place, waste in another, and a slow human in the loop on every transfer.

This is pure operational logistics — *what to move, how much, by when* — never clinical guidance. It's exactly the kind of multi-constraint coordination problem that should be handled by autonomous agents, not phone calls.

---

## The solution

Baymax states the need in plain language and lets a network of agents do the rest. The end-to-end loop:

```
physical shelf
   → camera (Claude Vision counts saline/IV bags)
   → Redis inventory
   → Fetch.ai uAgent network (broadcast need, collect offers)
   → Claude ranks the transfer split + writes a rationale
   → ASI:One chat narration (every milestone streams back live)
   → admin approval gate
   → Fetch testnet FET settlement (Payment Protocol, dorado-1)
   → (optional) external restock via Browserbase
```

A supply manager types *"Hospital A is short on IV fluids"* into ASI:One. The FRONT agent (Hospital A) broadcasts a `SupplyRequest`, the surplus facilities (B and C) reply with constrained `SupplyOffer`s, and — because no single facility can cover the need — Claude composes a **split transfer** (e.g. 150 units from the near facility, 50 from the far one), explains *why* in one breath, and the deal is settled as a real TestFET transaction on the Fetch Dorado testnet. Every step narrates back into the same chat conversation.

The moment that proves it's real: no single facility covers the request, so the network composes a multi-leg split and settles it on-chain — constraint handling plus a real transaction, not a scripted hand-off.

---

## Why Anthropic — Claude is the brain of the agent network

Baymax uses Claude at **three distinct cognitive layers**. Strip Claude out and you have a regex parser and a greedy sort; with Claude wired in, the agent network can *see*, *understand*, and *reason*.

### 1. Eyes — Claude Vision counts the physical shelf (`vision_count.py`)
A camera frame of a supply shelf is sent to `claude-sonnet-4-6`, which counts the visible units and returns a structured `COUNT` / `NOTES` pair. That count is written straight into the Redis inventory the agents read — so the whole negotiation is grounded in **what's physically on the shelf right now**, not a static database. (Includes a demo mode that counts water bottles as stand-in props for desk demos.)

### 2. Ears — Claude parses messy natural language into structured intent (`claude_intent.py`, gated by `BAYMAX_CLAUDE_INTENT`)
A supply manager doesn't type clean commands. *"we ran outta IV bags at site A"*, *"urgently need ~200 units of saline asap"* — Claude (`claude-haiku-4-5`, the right cost/latency tradeoff for a per-message classifier) maps synonyms, typos, urgency cues and facility names into the exact structured intent the negotiation core expects, using **forced tool-use** for strict structured output. It also distinguishes a real human request from the system's own narration echoed back by the chat layer.

### 3. Judgment — Claude reasons over the transfer split and explains it (`claude_ranking.py`, gated by `BAYMAX_CLAUDE_RANKING`)
This is the standout. When offers come in, Claude (`claude-sonnet-4-6`) reasons over **every constraint at once** — quantity available, distance/ETA, stock **expiry**, and urgency — to choose which facilities to draw from and how much from each. It returns a validated allocation plan plus a natural-language rationale.

The expiry-awareness is the genuinely intelligent move: a naive allocator just takes from the nearest facility. Claude is instructed to *prefer drawing down soonest-expiring stock first to reduce waste*, then trade that against ETA and urgency. So it might pull from a slightly farther facility precisely because that stock is about to expire — turning a logistics decision into a waste-reduction decision.

And that rationale is **streamed live into the ASI:One chat at the `EVALUATING` step** (`baymax_agents.py` narrates `plan.rationale` directly). Judges don't read about Claude's reasoning in a slide — they watch it appear in the chat as the agents decide. Claude's judgment *is* the visible product.

Every Claude layer is **fail-closed**: any API error, missing key, or malformed output falls back to a deterministic mock (the greedy allocator, the regex parser, a mock count), so the demo never hangs and the offline test suite needs no network.

---

## Why Fetch.ai — real agents, real protocols, real settlement

Baymax is a faithful implementation of the **"From Intent to Action"** challenge: a natural-language intent becomes autonomous multi-agent action that settles on-chain.

| Challenge requirement | How Baymax meets it |
| :-- | :-- |
| **Built on Fetch.ai uAgents** | Three real `uagents.Agent`s (Hospital A/B/C) running the full PRD negotiation state machine in `baymax_agents.py`. |
| **ASI:One Chat Protocol** | The official `chat_protocol_spec` (`AgentChatProtocol` v0.3.0), re-exported unchanged from `protocol.py` and matched by schema digest — never hand-redefined. |
| **Natural-language intent → action** | `front_agent.on_intent` turns a chat utterance into a live negotiation; every milestone streams back as a `ChatMessage`. |
| **Discoverable via Agentverse Mailbox** | Each agent runs in its own process (`run_front.py`, `run_hospital_b.py`, `run_hospital_c.py`) with `mailbox=True` — reachable from ASI:One with no public inbound endpoint. |
| **Payment Protocol settlement on testnet** | The official `payment_protocol_spec` (seller role) in `settlement.py`: `RequestPayment → CommitPayment → CompletePayment`, with cosmpy **on-chain tx verification** on `fetchai_stable_testnet` (dorado-1). |
| **Autonomous agent-to-agent negotiation** | B and C respond to broadcasts, accept/reject individual transfer legs, and the FRONT agent re-plans around rejections — no human in the inner loop. |
| **testnet-only / innovationlab + hackathon tags** | `FETCH_NETWORK=testnet` is force-pinned in `agent_base.py`; mainnet is fail-closed. README carries the required badges. |

This has run live end-to-end on ASI:One + Dorado: intent → 3-hospital negotiation → split transfer → in-chat TestFET payment card → signed → on-chain settlement → confirmed (verified tx on dorado-1, recorded in `DELIVERABLES.md`).

---

## What's live vs. mocked (honest engineering)

Every external dependency sits behind a **fail-closed seam**: the real backend runs when its flag is on, and a deterministic mock takes over on any failure. This is a deliberate design choice — the offline test suite (`wave2_e2e_check.py`, `wave3_order_e2e_check.py`, `front_agent.py` self-test) runs the full logic path with zero network, so the demo is reliable on conference WiFi while the real integrations are one env var away.

| Capability | Live path | Mock fallback |
| :-- | :-- | :-- |
| **Testnet FET settlement** | **Live** — real `RequestPayment`/`CommitPayment`/`CompletePayment` + cosmpy on-chain verification on dorado-1 (confirmed on ASI:One). | `wave2_e2e_check.py` exercises the wired path offline; `PAYMENT_VERIFY_ONCHAIN=false` for dev. |
| **Claude Vision inventory** | **Live** with `ANTHROPIC_API_KEY` — counts a real shelf. | `--counts`/`--mock-count` for keyless runs. |
| **Claude intent parsing** | **Live** with `BAYMAX_CLAUDE_INTENT=1` + key. | Deterministic regex/keyword parser (`front_agent.parse_intent`). |
| **Claude offer ranking** | **Live** with `BAYMAX_CLAUDE_RANKING=1` + key. | Deterministic nearest-first greedy allocator. |
| **Redis inventory** | **Live** with `BAYMAX_REDIS=1` — reads the teammates' Redis. | Hardcoded scenario inventory in `interfaces.py`. |
| **Browserbase external order** | **Live** with `BAYMAX_BROWSERBASE=1` — Stagehand/Playwright drives a vendor site. | Deterministic mock purchase order. |
| **Agentverse Mailbox / ASI:One** | **Live** — confirmed connected; agents found + chatted. | n/a (browser-gated, manual step). |

The two paths not verifiable offline — the live signed-payment leg and the live Browserbase order leg — are exactly the two that require a browser login or a funded wallet, and both are documented as such.

---

## Tech stack

- **Agents / protocols:** Fetch.ai `uagents` 0.25.2 + `uagents-core` 0.4.7 (Chat + Payment protocols), Agentverse Mailbox, ASI:One.
- **On-chain:** `cosmpy` against `NetworkConfig.fetchai_stable_testnet()` (Dorado, `atestfet`).
- **AI:** Anthropic SDK — Claude Sonnet 4.6 (vision + ranking) and Claude Haiku 4.5 (intent), all via forced tool-use for structured output.
- **Inventory:** Redis (redis-stack), seeded by the `tracks/redis` workstream.
- **Vision / web:** OpenCV capture, FastAPI/uvicorn camera workers + scan dashboard, Tailscale for two-laptop demos.
- **Ordering:** Stagehand 0.5.x / Playwright over Browserbase.
- **Language:** Python 3.12+ (developed on 3.14).

---

## Try it in 60 seconds

```bash
cd adyan-agent-communication-layer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# The full 3-agent negotiation in one process — split transfer, self-exits when done.
BAYMAX_EXIT_WHEN_DONE=1 ./.venv/bin/python baymax_agents.py
```

Pick the scenario with `BAYMAX_ITEM`: `"IV fluids"` (split across B+C, default), `"saline"` (full cover by B), `"sutures"` (no offer → graceful escalation).

```bash
# Watch the chat → negotiate → narrate loop with no network at all:
BAYMAX_SELFTEST=1 ./.venv/bin/python front_agent.py

# Full wired path incl. settlement, offline (chat → negotiate → settle → pay):
./.venv/bin/python wave2_e2e_check.py

# Admin gate + external supplier order, offline:
./.venv/bin/python wave3_order_e2e_check.py
```

**See Claude's three layers live** (needs `ANTHROPIC_API_KEY` in `.env`):

```bash
# Ranking reasoning streamed into the negotiation:
BAYMAX_CLAUDE_RANKING=1 BAYMAX_EXIT_WHEN_DONE=1 ./.venv/bin/python baymax_agents.py
# Natural-language intent parsing:
BAYMAX_CLAUDE_INTENT=1 BAYMAX_SELFTEST=1 ./.venv/bin/python front_agent.py
# Vision count of a shelf image:
./.venv/bin/python vision_count.py --image capture.jpg --item Saline
```

For the live ASI:One demo, run `run_hospital_b.py`, `run_hospital_c.py`, and `run_front.py` in separate terminals, connect each via Mailbox, and chat the FRONT agent. See `README.md` and `ARCHITECTURE.md` for the full picture.
