"""
Bridge to the Redis track (redis/).

The Redis track owns the schema, the connection client, and the read/write
helpers. Its modules use flat imports (e.g. `from redis_client import get_redis`),
so we add its `src/` dir to sys.path and re-export the helpers our agents need.
The single source of truth for Redis stays in redis/ — we do not redefine keys
or connections here.
"""

import sys
from pathlib import Path

# fetch/shared/redis_io.py  ->  parents[2] == repo root
_REDIS_SRC = Path(__file__).resolve().parents[2] / "redis" / "src"
if str(_REDIS_SRC) not in sys.path:
    sys.path.insert(0, str(_REDIS_SRC))

from redis_client import get_redis, ping_redis  # noqa: E402
from forecast import get_forecast, write_forecast  # noqa: E402
from inventory import (  # noqa: E402
    get_inventory, get_item_inventory, get_surplus, write_inventory, write_surplus,
)
from transfers import get_recent_transfers, log_transfer  # noqa: E402
from schema import EVENTS_CHANNEL  # noqa: E402

__all__ = ["get_redis", "ping_redis", "get_forecast", "write_forecast",
           "upsert_forecast_items", "get_inventory", "get_item_inventory",
           "get_surplus", "write_inventory", "write_surplus", "log_transfer",
           "get_recent_transfers", "EVENTS_CHANNEL"]


def upsert_forecast_items(region: str, new_items: dict) -> dict:
    """Merge signals into forecast:{region} without clobbering what another
    agent already wrote: read current items, merge ours in, write the union back.

    (write_forecast overwrites the whole key, but the weather and illness agents
    each only own part of the forecast.)
    """
    existing = get_forecast(region) or {}
    items = dict(existing.get("items", {}))
    items.update(new_items)
    return write_forecast(region, {"items": items})
