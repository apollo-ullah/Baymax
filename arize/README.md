# Arize Observability Track

This track uses Arize Phoenix and OpenTelemetry to make autonomous operational
decisions observable and improvable.

## Traced events

- `inventory_low` — a low-stock signal for an item at a location.
- `forecast_signal` — a predicted demand increase for a region and item.
- `reasoning_decision` — the agent's operational decision and rationale.
- `transfer_recommendation` — the recommended transfer output.
- `decision_chain` — links inventory, forecast, reasoning, and transfer signals
  into one trace, with signal strengths and decision confidence.
- `decision_outcome` — compares the recommended quantity against the actual
  fulfilled quantity and records an improvement note.

## The improvement loop

The system does not only log the final recommendation. It traces the signal
chain and compares recommended quantity against actual fulfilled quantity. This
makes it possible to debug, audit, and improve future recommendations.

## Setup

```bash
cd tracks/arize
cp .env.example .env
pip install -r requirements.txt
python3 src/demo_trace.py
```

## Optional Phoenix UI

```bash
python3 -m phoenix.server.main serve
```

Then open:

```
http://localhost:6006
```

If Phoenix is not running, the demo still prints local trace events and exits
cleanly.

## Prize Fit

Arize improves the application by making the decision chain inspectable:

inventory signal → forecast signal → reasoning decision → transfer
recommendation → decision confidence → outcome feedback.
