# shared — cross-track contracts

Code shared across the agent (Fetch) track.

- `protocol.py` — the uAgent negotiation + Payment Protocol messages and the
  agent state machine (PRD Section 10). The wire contract between hospital agents.
- `redis_io.py` — the bridge to the Redis track (`redis/`). Adds `redis/src` to
  the path and exposes `get_forecast` / `write_forecast` / `upsert_forecast_items`.

## Redis schema lives elsewhere

The Redis schema and helpers are owned by the **Redis track** in
[`../redis/`](../redis/) — see `redis/redis_contract.md` and `redis/src/schema.py`.
Do **not** redefine Redis keys here; import or mirror the Redis track instead.
Channels are `channels:events` / `channels:alerts`; the seeded region is
`san_francisco`.
