"""Trace persistence helper.

Saves trace events as pretty-printed JSON files under tracks/arize/traces/.
Independent from Phoenix and OpenTelemetry.
"""

import json
from datetime import datetime
from pathlib import Path

TRACES_DIR = Path(__file__).resolve().parent.parent / "traces"


def save_trace(trace_event):
    """Persist a trace event as a JSON file and return its filepath."""
    TRACES_DIR.mkdir(parents=True, exist_ok=True)

    trace_name = trace_event.get("trace_name", "trace")
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    filepath = TRACES_DIR / f"{trace_name}_{timestamp}.json"

    with filepath.open("w") as f:
        json.dump(trace_event, f, indent=2)

    print(f"[TRACE SAVED] {filepath}")

    return filepath
