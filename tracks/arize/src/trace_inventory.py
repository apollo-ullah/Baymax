"""Inventory trace.

Local trace simulator for low-stock events. Builds a trace event dictionary,
prints it, and emits a real OpenTelemetry span (exported to Phoenix when
configured).
"""

import json

try:
    from .trace_schema import (
        TRACE_INVENTORY_LOW,
        ATTR_HOSPITAL_ID,
        ATTR_ITEM,
        ATTR_CURRENT_PCT,
        ATTR_STATUS,
    )
    from .phoenix_client import print_phoenix_status, get_tracer
except ImportError:
    from trace_schema import (
        TRACE_INVENTORY_LOW,
        ATTR_HOSPITAL_ID,
        ATTR_ITEM,
        ATTR_CURRENT_PCT,
        ATTR_STATUS,
    )
    from phoenix_client import print_phoenix_status, get_tracer


def trace_inventory_low(hospital_id, item, current_pct, status):
    """Simulate an inventory_low trace event, emit a span, and return the event."""
    print_phoenix_status()

    tracer = get_tracer()

    with tracer.start_as_current_span(TRACE_INVENTORY_LOW) as span:
        trace_event = {
            "trace_name": TRACE_INVENTORY_LOW,
            "hospital_id": hospital_id,
            "item": item,
            "current_pct": current_pct,
            "status": status,
        }

        span.set_attribute(ATTR_HOSPITAL_ID, hospital_id)
        span.set_attribute(ATTR_ITEM, item)
        span.set_attribute(ATTR_CURRENT_PCT, current_pct)
        span.set_attribute(ATTR_STATUS, status)

        print(f"[TRACE] {TRACE_INVENTORY_LOW}")
        print(json.dumps(trace_event, indent=2))

    return trace_event
