"""Trace persistence helper.

Saves trace events as pretty-printed JSON files under tracks/arize/traces/.
Independent from Phoenix and OpenTelemetry.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

# arize/traces (gitignored via arize/.gitignore). Regenerable output, never committed.
TRACES_DIR = Path(__file__).resolve().parent.parent / "traces"


def save_trace(trace_event):
    """Persist a trace event as a JSON file and return its filepath."""
    TRACES_DIR.mkdir(parents=True, exist_ok=True)

    trace_name = trace_event.get("trace_name", "trace")
    # Microsecond timestamp + short uuid so concurrent spans of the same trace
    # name within one second never overwrite each other (live push-based loop).
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    filepath = TRACES_DIR / f"{trace_name}_{timestamp}_{uuid.uuid4().hex[:6]}.json"

    with filepath.open("w") as f:
        json.dump(trace_event, f, indent=2)

    print(f"[TRACE SAVED] {filepath}")

    return filepath
