"""Decision outcome trace.

Compares the recommended action against the actual result. Builds a trace event
dictionary, prints it, and emits a real OpenTelemetry span (exported to Phoenix
when configured).
"""

import json

try:
    from .trace_schema import (
        TRACE_DECISION_OUTCOME,
        ATTR_HOSPITAL_ID,
        ATTR_ITEM,
        ATTR_RECOMMENDED_QUANTITY,
        ATTR_ACTUAL_QUANTITY,
        ATTR_OUTCOME,
        ATTR_IMPROVEMENT_NOTE,
    )
    from .phoenix_client import print_phoenix_status, get_tracer
    from .trace_store import save_trace
except ImportError:
    from trace_schema import (
        TRACE_DECISION_OUTCOME,
        ATTR_HOSPITAL_ID,
        ATTR_ITEM,
        ATTR_RECOMMENDED_QUANTITY,
        ATTR_ACTUAL_QUANTITY,
        ATTR_OUTCOME,
        ATTR_IMPROVEMENT_NOTE,
    )
    from phoenix_client import print_phoenix_status, get_tracer
    from trace_store import save_trace


def trace_decision_outcome(
    hospital_id,
    item,
    recommended_quantity,
    actual_quantity,
    outcome,
    improvement_note,
):
    """Simulate a decision_outcome trace event, emit a span, and return the event."""
    print_phoenix_status()

    tracer = get_tracer()

    with tracer.start_as_current_span(TRACE_DECISION_OUTCOME) as span:
        trace_event = {
            "trace_name": TRACE_DECISION_OUTCOME,
            "hospital_id": hospital_id,
            "item": item,
            "recommended_quantity": recommended_quantity,
            "actual_quantity": actual_quantity,
            "outcome": outcome,
            "improvement_note": improvement_note,
        }

        span.set_attribute(ATTR_HOSPITAL_ID, hospital_id)
        span.set_attribute(ATTR_ITEM, item)
        span.set_attribute(ATTR_RECOMMENDED_QUANTITY, recommended_quantity)
        span.set_attribute(ATTR_ACTUAL_QUANTITY, actual_quantity)
        span.set_attribute(ATTR_OUTCOME, outcome)
        span.set_attribute(ATTR_IMPROVEMENT_NOTE, improvement_note)

        print(f"[TRACE] {TRACE_DECISION_OUTCOME}")
        print(json.dumps(trace_event, indent=2))

    save_trace(trace_event)

    return trace_event
