# Baymax — Anthropic Sponsor Track

> "Projects built with Claude Code that tackle meaningful issues in health, education, economic opportunity, or any domain where AI could genuinely shift what's possible for people."

Baymax is a health logistics system built entirely with Claude Code and the Claude API. The problem it addresses is real: hospitals run out of supplies because the coordination layer between facilities is slow, manual, and dependent on someone noticing in time. Baymax replaces that with an autonomous agent network that detects, negotiates, and settles inter-facility transfers before the shortage becomes a crisis.

---

## Built with Claude Code

The entire Baymax codebase was written in Claude Code. This includes:

- The Fetch.ai uAgent negotiation core (`agent-communication-layer/stockpile_agents.py`, `front_agent.py`, `protocol.py`)
- The Payment Protocol settlement layer (`settlement.py`)
- The Redis integration seam (`redis/src/`)
- The camera vision pipeline (`hardware/camera connection/`)
- The live dashboard (`ui/app.py`)
- The observability layer (`arize/`)
- The forecast input agents (`fetch/agents/`)
- The human-approval iMessage pipeline (`fetch/approval/`)

Claude Code handled the full development loop: exploring the Fetch.ai uAgents and Payment Protocol APIs (neither was well-known to the team), designing the frozen wire contract that lets workstreams build in parallel, debugging async event-loop issues specific to Python 3.14, and wiring six loosely-coupled subsystems together under a single Redis state bus.

---

## How Claude (the model) powers the application

### 1. Offer ranking and transfer composition

When surplus offers arrive from multiple hospitals, Claude evaluates them against real constraints — quantity available, distance/ETA, supply expiry date, and urgency level — and composes a resolution. When no single facility covers the need, Claude selects a split (e.g. 150 units from Hospital B + 50 from Hospital C), reasoning about which combination minimizes delivery time while meeting the total requirement.

This is the reasoning that replaces a human supply manager's judgment. A deterministic greedy allocator (nearest-first) sits behind the same interface as a mock for offline testing; the live path calls Claude Sonnet 4.6.

**Seam in code:** `interfaces.py → rank_offers(need, offers) -> RankedPlan`

### 2. Shelf inventory via Claude Vision

A camera captures a frame of a physical supply shelf divided into two hospital sections by a green straw. That frame goes to Claude Vision (Sonnet 4.6), which:

- Locates the green straw divider
- Counts saline units on each side
- Returns structured JSON (`hospital_a.saline`, `hospital_b.saline`, totals, notes)

The result writes directly to Redis as `qty` and `surplus` — the same fields the negotiating agents read. The vision output is the ground truth inventory signal for Hospital A and Hospital B.

**Why Claude Vision over a trained CV model:** Claude understands "saline bag" and "IV fluid bottle" out of the box. No labeled training dataset, no retraining loop when the supply type changes. The prompt changes in plain English.

**Code:** `hardware/camera connection/bottle_counter.py`

### 3. Intent parsing (with LLM seam)

`front_agent.parse_intent` currently uses a deterministic keyword/regex parser to turn "Hospital A is short on IV fluids" into a structured `SupplyRequest`. A commented seam in the same function accepts a Claude completion behind the same signature — the switch from mock to model is a one-line change.

---

## The health impact case

**The problem is real.** Within multi-facility health systems and regional hospital mutual-aid compacts, inter-facility supply transfers already happen — but slowly and manually. A nurse notices the IV fluid cabinet is running low. A phone call goes to the network coordinator. Someone checks a spreadsheet. Hours pass. Sometimes the shortage is caught; sometimes it isn't until a procedure is delayed.

**The scope is carefully bounded.** Baymax handles non-scheduled consumables (IV fluids, saline, PPE, sutures) where inter-facility transfer is routine and legally uncomplicated. It never makes clinical decisions, never touches controlled substances, and never replaces existing procurement protocols — it detects the imbalance, negotiates the transfer, and hands the settled result to the existing process.

**The novel layer is coordination.** Vision-based inventory monitoring is a commodity. What's genuinely new is the agent network that detects the imbalance across organizational boundaries, matches surplus to shortfall under real constraints, and settles the transfer as an on-chain transaction — autonomously, in seconds, before the shortage becomes a crisis.

---

## What we proved works

A full live run on ASI:One + Dorado testnet: natural-language intent → 3-hospital negotiation → split transfer → in-chat TestFET payment → on-chain settlement → confirmed. On-chain transaction `912BB2030A5F14467F434D4CDB0F1DDE23EE6E72E19B9F778998D17A5046557F`.

The entire path is also reproducible offline with deterministic harnesses:

```bash
# Full offline e2e: chat → negotiate → settle → pay (no ASI:One/wallet needed)
cd agent-communication-layer
./.venv/bin/python wave2_e2e_check.py

# Camera vision pipeline (no camera needed — uses --counts flag)
python "hardware/camera connection/sync_to_redis.py" --counts a=0,b=6
```

---

## Models used

| Use | Model |
| :-- | :-- |
| Offer ranking + transfer composition | `claude-sonnet-4-6` |
| Shelf inventory via camera | `claude-sonnet-4-6` (vision) |
| Full codebase development | Claude Code (Sonnet 4.6) |
