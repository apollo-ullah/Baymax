"""arize_hook.py — deploy-time observability emitter for the negotiation core.

The negotiation core (baymax_agents.py) emits milestones through a one-way hook
(baymax_agents._trace -> the registered emitter). This module is that emitter for
the Arize/Phoenix track. The core NEVER imports arize — run_front.py /
run_dashboard_demo.py register emit_trace via baymax_agents.register_trace_hook(),
exactly mirroring register_settlement_hook (a clean one-way dependency: the
deployment wires observability INTO the core, never the reverse). This keeps
protocol.py frozen and the offline harnesses arize-free.

Opt-in + fail-open:
  * Registered only when BAYMAX_ARIZE is truthy (arize_enabled()).
  * Every emit is wrapped so a missing dep (opentelemetry/arize-phoenix), an
    unreachable Phoenix collector, or any error is swallowed — tracing must never
    break or slow a negotiation.

Each span carries `req_id` so Phoenix can group one negotiation's lifecycle
(crisis_research -> inventory_low -> reasoning_decision -> transfer_recommendation
/ supplier_order -> decision_outcome). Set PHOENIX_COLLECTOR_ENDPOINT to export to
a running Phoenix; without it, spans are created locally and JSON trace artifacts
are still written under arize/traces/ (gitignored).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# The Arize track owns the trace schema + store. Add its src/ to the path.
# agent-communication-layer/arize_hook.py -> parents[1] == repo root.
_ARIZE_SRC = Path(__file__).resolve().parents[1] / "arize" / "src"
if str(_ARIZE_SRC) not in sys.path:
    sys.path.insert(0, str(_ARIZE_SRC))


def arize_enabled() -> bool:
    """True when the Arize observability hook should be registered (opt-in)."""
    return os.getenv("BAYMAX_ARIZE", "").strip().lower() in ("1", "true", "yes", "on")


def _span_safe(value):
    """OTel span attributes must be primitives (or sequences of them). Stringify
    anything else (lists/dicts) to JSON so set_attribute never raises."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)) and all(
        isinstance(x, (str, int, float, bool)) for x in value
    ):
        return list(value)
    try:
        return json.dumps(value, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


def emit_trace(event_type: str, attrs: dict) -> None:
    """Persist a JSON trace artifact and (if Phoenix/OTel is available) open an
    OTel span for `event_type`, tagged with every attr + the shared req_id.
    Fail-open: any error is swallowed."""
    attrs = attrs or {}

    # 1) JSON artifact (no OTel dependency — always works, gitignored output).
    try:
        import trace_store  # arize/src/trace_store.py
        trace_store.save_trace({
            "trace_name": event_type,
            "req_id": attrs.get("req_id"),
            "attributes": attrs,
        })
    except Exception:  # noqa: BLE001 — never break the negotiation
        pass

    # 2) OTel span to Phoenix (optional — needs opentelemetry + a collector).
    try:
        import phoenix_client  # arize/src/phoenix_client.py
        tracer = phoenix_client.get_tracer()
        with tracer.start_as_current_span(event_type) as span:
            for key, value in attrs.items():
                try:
                    span.set_attribute(str(key), _span_safe(value))
                except Exception:  # noqa: BLE001 — one bad attr must not abort the span
                    pass
    except Exception:  # noqa: BLE001 — Phoenix/OTel unavailable -> JSON artifact still written
        pass
