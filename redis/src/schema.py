"""Redis key helpers and constants for the Redis track.

This module only defines naming conventions for keys, streams, and channels.
It must not import or talk to Redis directly.
"""

# --- Key helpers (per-entity Redis keys) ---


def inventory_key(hospital_id: str) -> str:
    """Hash holding current stock counts per item for a hospital."""
    return f"hospital:{hospital_id}:inventory"


def meta_key(hospital_id: str) -> str:
    """Hash holding hospital metadata (name, region, location, etc.)."""
    return f"hospital:{hospital_id}:meta"


def surplus_key(hospital_id: str) -> str:
    """Set/hash tracking items a hospital currently has in surplus."""
    return f"hospital:{hospital_id}:surplus"


def forecast_key(region: str) -> str:
    """Hash holding demand forecast values for a region."""
    return f"forecast:{region}"


def scenario_key(scenario_id: str) -> str:
    """JSON string holding a high-level operational scenario profile."""
    return f"scenario:{scenario_id}"


# --- Streams (append-only event logs) ---

# Stream of transfer requests/records between hospitals.
TRANSFERS_STREAM = "transfers"

# Stream logging every alert that has been raised.
ALERTS_STREAM = "alerts:log"


# --- Pub/Sub channels (real-time fan-out) ---

# Channel broadcasting general system events.
EVENTS_CHANNEL = "channels:events"

# Channel broadcasting alerts as they happen.
ALERTS_CHANNEL = "channels:alerts"


# --- Usage history (vector search) ---

# Prefix for per-record usage history keys (e.g. history:usage:<id>).
HISTORY_PREFIX = "history:usage"

# Name of the vector index built over usage history records.
HISTORY_INDEX = "history:usage:index"


# --- Vision pipeline ---

# Latest raw camera/vision inventory detection output (JSON string).
VISION_LATEST_KEY = "vision:latest"


# --- Reasoning + crisis (Claude outputs) ---

# Latest Claude supply-risk reasoning (JSON string):
# {risk_level, priority_items, reasoning, recommended_action, updated_at}.
# Written by the WHO ingest fetcher (run_who_update); read by the dashboard.
REASONING_KEY = "reasoning:latest"

# The active crisis brief (JSON string): the user's crisis prompt + the inferred
# crisis type + ranked at-risk supplies + rationale + status. Written by the
# crisis flow (dashboard POST /api/crisis seeds it; the agent layer's
# start_crisis fills in the research result); read by the dashboard.
CRISIS_ACTIVE_KEY = "crisis:active"


def crisis_active_key() -> str:
    """JSON string holding the active crisis brief."""
    return CRISIS_ACTIVE_KEY


# --- Live narration (FRONT agent -> dashboard SSE) ---

# Channel carrying negotiation/crisis/ingest milestones the dashboard streams
# over SSE. This is the channel the dashboard ACTUALLY subscribes to — distinct
# from the legacy channels:events / channels:alerts above (which currently have
# no live subscriber).
NARRATION_CHANNEL = "baymax:narration"
