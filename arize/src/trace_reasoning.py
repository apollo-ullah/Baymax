"""Reasoning decision trace.

Local trace simulator for the agent's operational decision. Builds a trace event
dictionary, prints it, and emits a real OpenTelemetry span (exported to Phoenix
when configured).
"""

import json

try:
    from .trace_schema import (
        TRACE_REASONING_DECISION,
        ATTR_HOSPITAL_ID,
        ATTR_ITEM,
        ATTR_SEVERITY,
        ATTR_RECOMMENDED_ACTION,
        ATTR_SOURCE_HOSPITAL,
        ATTR_TRANSFER_QUANTITY,
    )
    from .phoenix_client import print_phoenix_status, get_tracer
    from .trace_store import save_trace
except ImportError:
    from arize.src.trace_schema import (
        TRACE_REASONING_DECISION,
        ATTR_HOSPITAL_ID,
        ATTR_ITEM,
        ATTR_SEVERITY,
        ATTR_RECOMMENDED_ACTION,
        ATTR_SOURCE_HOSPITAL,
        ATTR_TRANSFER_QUANTITY,
    )
    from arize.src.phoenix_client import print_phoenix_status, get_tracer
    from arize.src.trace_store import save_trace


def trace_reasoning_decision(
    hospital_id,
    item,
    severity,
    recommended_action,
    reasoning,
    source_hospital=None,
    transfer_quantity=None,
):
    """Simulate a reasoning_decision trace event, emit a span, and return the event."""
    print_phoenix_status()

    tracer = get_tracer()

    with tracer.start_as_current_span(TRACE_REASONING_DECISION) as span:
        trace_event = {
            "trace_name": TRACE_REASONING_DECISION,
            "hospital_id": hospital_id,
            "item": item,
            "severity": severity,
            "recommended_action": recommended_action,
            "reasoning": reasoning,
            "source_hospital": source_hospital,
            "transfer_quantity": transfer_quantity,
        }

        span.set_attribute(ATTR_HOSPITAL_ID, hospital_id)
        span.set_attribute(ATTR_ITEM, item)
        span.set_attribute(ATTR_SEVERITY, severity)
        span.set_attribute(ATTR_RECOMMENDED_ACTION, recommended_action)
        if source_hospital is not None:
            span.set_attribute(ATTR_SOURCE_HOSPITAL, source_hospital)
        if transfer_quantity is not None:
            span.set_attribute(ATTR_TRANSFER_QUANTITY, transfer_quantity)
        span.set_attribute("reasoning", reasoning)

        print(f"[TRACE] {TRACE_REASONING_DECISION}")
        print(json.dumps(trace_event, indent=2))

    save_trace(trace_event)

    return trace_event
