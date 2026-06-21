"""Transfer recommendation trace.

Local trace simulator for the transfer output. Builds a trace event dictionary,
prints it, and emits a real OpenTelemetry span (exported to Phoenix when
configured).
"""

import json

try:
    from .trace_schema import (
        TRACE_TRANSFER_RECOMMENDATION,
        ATTR_SOURCE_HOSPITAL,
        ATTR_DESTINATION_HOSPITAL,
        ATTR_ITEM,
        ATTR_TRANSFER_QUANTITY,
        ATTR_ETA_MINUTES,
        ATTR_SEVERITY,
    )
    from .phoenix_client import print_phoenix_status, get_tracer
except ImportError:
    from trace_schema import (
        TRACE_TRANSFER_RECOMMENDATION,
        ATTR_SOURCE_HOSPITAL,
        ATTR_DESTINATION_HOSPITAL,
        ATTR_ITEM,
        ATTR_TRANSFER_QUANTITY,
        ATTR_ETA_MINUTES,
        ATTR_SEVERITY,
    )
    from phoenix_client import print_phoenix_status, get_tracer


def trace_transfer_recommendation(
    source_hospital,
    destination_hospital,
    item,
    transfer_quantity,
    eta_minutes,
    severity="critical",
):
    """Simulate a transfer_recommendation trace event, emit a span, and return it."""
    print_phoenix_status()

    tracer = get_tracer()

    with tracer.start_as_current_span(TRACE_TRANSFER_RECOMMENDATION) as span:
        trace_event = {
            "trace_name": TRACE_TRANSFER_RECOMMENDATION,
            "source_hospital": source_hospital,
            "destination_hospital": destination_hospital,
            "item": item,
            "transfer_quantity": transfer_quantity,
            "eta_minutes": eta_minutes,
            "severity": severity,
        }

        span.set_attribute(ATTR_SOURCE_HOSPITAL, source_hospital)
        span.set_attribute(ATTR_DESTINATION_HOSPITAL, destination_hospital)
        span.set_attribute(ATTR_ITEM, item)
        span.set_attribute(ATTR_TRANSFER_QUANTITY, transfer_quantity)
        span.set_attribute(ATTR_ETA_MINUTES, eta_minutes)
        span.set_attribute(ATTR_SEVERITY, severity)

        print(f"[TRACE] {TRACE_TRANSFER_RECOMMENDATION}")
        print(json.dumps(trace_event, indent=2))

    return trace_event
