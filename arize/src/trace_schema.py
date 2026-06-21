"""Trace data models / schema.

Defines the canonical trace names and span attribute keys used across the
observability track. Plain string constants only - no Arize or OpenTelemetry
imports here.
"""

# Trace names
TRACE_INVENTORY_LOW = "inventory_low"  # tracks low stock
TRACE_FORECAST_SIGNAL = "forecast_signal"  # tracks demand increase
TRACE_REASONING_DECISION = "reasoning_decision"  # tracks the agent's decision
TRACE_TRANSFER_RECOMMENDATION = "transfer_recommendation"  # tracks the transfer output
TRACE_DECISION_CHAIN = "decision_chain"  # links inventory, forecast, reasoning, and transfer into one trace
TRACE_DECISION_OUTCOME = "decision_outcome"  # compares recommendation vs actual result

# Span attributes
ATTR_HOSPITAL_ID = "hospital_id"
ATTR_ITEM = "item"
ATTR_CURRENT_PCT = "current_pct"
ATTR_STATUS = "status"
ATTR_REGION = "region"
ATTR_PREDICTED_DEMAND_INCREASE_PCT = "predicted_demand_increase_pct"
ATTR_REASON = "reason"
ATTR_SEVERITY = "severity"
ATTR_RECOMMENDED_ACTION = "recommended_action"
ATTR_SOURCE_HOSPITAL = "source_hospital"
ATTR_DESTINATION_HOSPITAL = "destination_hospital"
ATTR_TRANSFER_QUANTITY = "transfer_quantity"
ATTR_ETA_MINUTES = "eta_minutes"
ATTR_INVENTORY_SIGNAL_STRENGTH = "inventory_signal_strength"
ATTR_FORECAST_SIGNAL_STRENGTH = "forecast_signal_strength"
ATTR_DECISION_CONFIDENCE = "decision_confidence"
ATTR_RECOMMENDED_QUANTITY = "recommended_quantity"
ATTR_ACTUAL_QUANTITY = "actual_quantity"
ATTR_OUTCOME = "outcome"
ATTR_IMPROVEMENT_NOTE = "improvement_note"
