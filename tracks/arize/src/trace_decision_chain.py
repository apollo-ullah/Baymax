"""Decision chain trace.

Links inventory, forecast, reasoning, and transfer signals into one trace.
Builds a trace event dictionary, prints it, and emits a real OpenTelemetry span
(exported to Phoenix when configured).
"""

import json

try:
    from .trace_schema import (
        TRACE_DECISION_CHAIN,
        ATTR_HOSPITAL_ID,
        ATTR_ITEM,
        ATTR_INVENTORY_SIGNAL_STRENGTH,
        ATTR_FORECAST_SIGNAL_STRENGTH,
        ATTR_DECISION_CONFIDENCE,
        ATTR_RECOMMENDED_ACTION,
        ATTR_SOURCE_HOSPITAL,
        ATTR_TRANSFER_QUANTITY,
    )
    from .phoenix_client import print_phoenix_status, get_tracer
except ImportError:
    from trace_schema import (
        TRACE_DECISION_CHAIN,
        ATTR_HOSPITAL_ID,
        ATTR_ITEM,
        ATTR_INVENTORY_SIGNAL_STRENGTH,
        ATTR_FORECAST_SIGNAL_STRENGTH,
        ATTR_DECISION_CONFIDENCE,
        ATTR_RECOMMENDED_ACTION,
        ATTR_SOURCE_HOSPITAL,
        ATTR_TRANSFER_QUANTITY,
    )
    from phoenix_client import print_phoenix_status, get_tracer


def trace_decision_chain(
    hospital_id,
    item,
    inventory_signal_strength,
    forecast_signal_strength,
    decision_confidence,
    recommended_action,
    source_hospital,
    transfer_quantity,
):
    """Simulate a decision_chain trace event, emit a span, and return the event."""
    print_phoenix_status()

    tracer = get_tracer()

    with tracer.start_as_current_span(TRACE_DECISION_CHAIN) as span:
        trace_event = {
            "trace_name": TRACE_DECISION_CHAIN,
            "hospital_id": hospital_id,
            "item": item,
            "inventory_signal_strength": inventory_signal_strength,
            "forecast_signal_strength": forecast_signal_strength,
            "decision_confidence": decision_confidence,
            "recommended_action": recommended_action,
            "source_hospital": source_hospital,
            "transfer_quantity": transfer_quantity,
        }

        span.set_attribute(ATTR_HOSPITAL_ID, hospital_id)
        span.set_attribute(ATTR_ITEM, item)
        span.set_attribute(ATTR_INVENTORY_SIGNAL_STRENGTH, inventory_signal_strength)
        span.set_attribute(ATTR_FORECAST_SIGNAL_STRENGTH, forecast_signal_strength)
        span.set_attribute(ATTR_DECISION_CONFIDENCE, decision_confidence)
        span.set_attribute(ATTR_RECOMMENDED_ACTION, recommended_action)
        span.set_attribute(ATTR_SOURCE_HOSPITAL, source_hospital)
        span.set_attribute(ATTR_TRANSFER_QUANTITY, transfer_quantity)

        print(f"[TRACE] {TRACE_DECISION_CHAIN}")
        print(json.dumps(trace_event, indent=2))

    return trace_event
