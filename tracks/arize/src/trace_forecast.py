"""Forecast trace.

Local trace simulator for demand-increase forecast signals. Builds a trace event
dictionary, prints it, and emits a real OpenTelemetry span (exported to Phoenix
when configured).
"""

import json

try:
    from .trace_schema import (
        TRACE_FORECAST_SIGNAL,
        ATTR_REGION,
        ATTR_ITEM,
        ATTR_PREDICTED_DEMAND_INCREASE_PCT,
        ATTR_REASON,
    )
    from .phoenix_client import print_phoenix_status, get_tracer
except ImportError:
    from trace_schema import (
        TRACE_FORECAST_SIGNAL,
        ATTR_REGION,
        ATTR_ITEM,
        ATTR_PREDICTED_DEMAND_INCREASE_PCT,
        ATTR_REASON,
    )
    from phoenix_client import print_phoenix_status, get_tracer


def trace_forecast_signal(region, item, predicted_demand_increase_pct, reason):
    """Simulate a forecast_signal trace event, emit a span, and return the event."""
    print_phoenix_status()

    tracer = get_tracer()

    with tracer.start_as_current_span(TRACE_FORECAST_SIGNAL) as span:
        trace_event = {
            "trace_name": TRACE_FORECAST_SIGNAL,
            "region": region,
            "item": item,
            "predicted_demand_increase_pct": predicted_demand_increase_pct,
            "reason": reason,
        }

        span.set_attribute(ATTR_REGION, region)
        span.set_attribute(ATTR_ITEM, item)
        span.set_attribute(ATTR_PREDICTED_DEMAND_INCREASE_PCT, predicted_demand_increase_pct)
        span.set_attribute(ATTR_REASON, reason)

        print(f"[TRACE] {TRACE_FORECAST_SIGNAL}")
        print(json.dumps(trace_event, indent=2))

    return trace_event
