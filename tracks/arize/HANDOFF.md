# Arize Track — Teammate Handoff

## What this track does

Uses Arize Phoenix + OpenTelemetry to make autonomous operational decisions
observable and measurable.

## Traced Events

- inventory_low
- forecast_signal
- reasoning_decision
- transfer_recommendation
- decision_chain
- decision_outcome

## Why it matters

The system does not only log recommendations.

It traces:

inventory signal
→ forecast signal
→ reasoning decision
→ transfer recommendation
→ decision confidence
→ outcome feedback

This makes recommendations explainable and helps improve future decisions.

## How to Run

```bash
cd tracks/arize
cp .env.example .env
pip install -r requirements.txt
python3 src/demo_trace.py
```

Optional Phoenix UI:

```bash
python3 -m phoenix.server.main serve
```

Open:
http://localhost:6006

## Current Status

- OpenTelemetry spans implemented
- Phoenix exporter implemented
- demo_trace.py working
- decision_chain implemented
- decision_outcome implemented

## Important

Do not rename trace names without updating all trace modules.
