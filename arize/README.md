# Baymax — Arize Sponsor Track

Arize Phoenix provides end-to-end observability over Baymax's decision chain. The system doesn't just log the final recommendation — it traces every signal that contributed to it, compares predicted versus actual outcomes, and surfaces where the chain can improve. That's the requirement: evidence that Arize was used **and actually improved the application**.

---

## What Arize traces

Every autonomous decision in Baymax flows through a chain of signals:

```
inventory signal  →  forecast signal  →  reasoning decision  →  transfer recommendation  →  outcome
```

Each link in this chain is a separate span. Together they form a single trace per negotiation, so a judge (or a future engineer) can see exactly what inventory reading triggered what forecast evaluation, what Claude reasoned over them, what transfer it recommended, and whether the actual fulfilled quantity matched.

### Span types

| Span | What it captures |
| :-- | :-- |
| `inventory_low` | Item, location, current quantity, threshold, signal strength |
| `forecast_signal` | Region, item, predicted demand increase (%), source (weather / illness) |
| `reasoning_decision` | The agent's decision and rationale; decision confidence (0–1) |
| `transfer_recommendation` | Recommended quantity, source hospital, ETA, expiry margin |
| `decision_chain` | Parent span that links all of the above into one trace |
| `decision_outcome` | Recommended quantity vs. actual fulfilled quantity; improvement note |

### The improvement loop

`decision_outcome` is the span that makes Arize more than a logger. It records:
- `recommended_qty` — what the agent proposed
- `fulfilled_qty` — what actually transferred
- `improvement_note` — a free-text explanation of any gap (partial offer, rejection, re-plan)

This makes it possible to audit why a negotiation produced a 150+50 split instead of a full 200-unit transfer, and to build a dataset of decisions and outcomes to improve future reasoning.

---

## How it connects to the live system

The Arize integration is in `arize/src/`. It imports from the Redis track (`redis/src/`) to read live inventory and forecast state, so the traces reflect real system state rather than synthetic data. When Phoenix is running, every negotiation produces a full decision-chain trace visible in the Phoenix UI. When Phoenix is not running, the spans are printed locally and the system exits cleanly — Arize is observability, not a dependency of the critical path.

The agent code emits trace events via the `scan_dashboard.py` bridge in `agent-communication-layer/`, which reads Redis state and pushes spans to Phoenix after each negotiation milestone.

---

## Setup

```bash
cd arize

# Install dependencies
pip install -r requirements.txt     # arize-phoenix, opentelemetry-sdk

# Run the demo trace (works with or without Phoenix running)
python src/demo_trace.py
```

To view traces in the Phoenix UI:

```bash
python -m phoenix.server.main serve
open http://localhost:6006
```

To run against live Redis state (requires Redis Stack running and seeded):

```bash
REDIS_URL=redis://localhost:6379 python src/demo_trace.py
```

---

## What you can see in Phoenix

After a negotiation run:

1. **Trace timeline** — the full decision chain as a waterfall: inventory check → forecast evaluation → Claude reasoning → transfer proposal → outcome
2. **Signal strengths** — each span carries a numeric signal strength (0–1), so you can see how strongly the inventory reading drove the forecast evaluation
3. **Decision confidence** — the reasoning span exposes Claude's confidence in the chosen resolution
4. **Outcome gap** — where recommended ≠ fulfilled, the outcome span explains why (partial offer, rejection, re-plan into a split)

This is the data needed to tune the urgency thresholds, the offer-ranking weights, and the re-planning logic — things you can't improve without observability over the full chain.

---

## Why this matters for Baymax

Baymax makes autonomous decisions about hospital supply transfers. In a real deployment, those decisions need to be auditable — a supply manager needs to know why the system chose Hospital B over C, why it split the transfer, and whether the split actually delivered what was needed. Arize Phoenix makes that possible without instrumenting the agent code with ad-hoc logging.
