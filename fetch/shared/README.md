# shared — cross-track contracts

Code shared across the agent (Fetch) track.

- `protocol.py` — the uAgent negotiation + Payment Protocol messages and the
  agent state machine (PRD Section 10). The wire contract between hospital agents.

## Redis schema lives elsewhere

The Redis schema and helpers are owned by the **Redis track** in
[`../tracks/redis/`](../tracks/redis/) — see `tracks/redis/redis_contract.md`
and `tracks/redis/src/schema.py`. Do **not** redefine Redis keys here; import or
mirror the Redis track instead. (We removed a duplicate `redis_keys.py` that
drifted from the Redis owner's schema — e.g. channels are `channels:events` /
`channels:alerts`, and the seeded region is `san_francisco`.)
