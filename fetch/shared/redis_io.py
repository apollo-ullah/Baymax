"""
Bridge to the Redis track (tracks/redis).

The Redis track owns the schema, the connection client, and the read/write
helpers. Its modules use flat imports (e.g. `from redis_client import get_redis`),
so we add its `src/` dir to sys.path and re-export the helpers our agents need.
The single source of truth for Redis stays in tracks/redis — we do not redefine
keys or connections here.
"""

import sys
from pathlib import Path

# tracks/fetch/shared/redis_io.py  ->  parents[3] == repo root
_REDIS_SRC = Path(__file__).resolve().parents[3] / "tracks" / "redis" / "src"
if str(_REDIS_SRC) not in sys.path:
    sys.path.insert(0, str(_REDIS_SRC))

from redis_client import get_redis, ping_redis  # noqa: E402
from forecast import get_forecast, write_forecast  # noqa: E402

__all__ = ["get_redis", "ping_redis", "get_forecast", "write_forecast",
           "upsert_forecast_items"]


def upsert_forecast_items(region: str, new_items: dict) -> dict:
    """Merge demand signals into forecast:{region} without clobbering what
    another agent already wrote.

    The Redis track's write_forecast() overwrites the whole key, but the weather
    and illness agents each only own part of the forecast. So we read the current
    forecast, merge our items in, and write the union back.
    """
    existing = get_forecast(region) or {}
    items = dict(existing.get("items", {}))
    items.update(new_items)
    return write_forecast(region, {"items": items})
