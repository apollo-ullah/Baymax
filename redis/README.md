# Baymax — Redis Sponsor Track

**Redis is the integration seam for the entire system.** Every component — agents, camera vision, weather forecasting, illness signals, the dashboard, and the human-approval pipeline — reads and writes through Redis. It is the single source of truth that lets six independent workstreams build in parallel without coupling to each other's code.

---

## How we use Redis (beyond caching)

### 1. Real-time agent memory

Each hospital's live inventory state lives in Redis hash keys (`inventory:{hospital_id}:{item}`). When an agent evaluates whether it has surplus to offer, it reads directly from Redis via `redis_inventory.py`. This gives agents **persistent, shared memory across processes** — the negotiation state survives agent restarts, and a camera update is immediately visible to a running agent without any restart.

```
hospital_a : IV Fluids  →  qty=45, pct=22, status=low, surplus=0
hospital_b : IV Fluids  →  qty=180, pct=90, status=ok, surplus=80
hospital_c : IV Fluids  →  qty=120, pct=60, status=ok, surplus=50
```

The `spare_capacity` an agent offers in negotiation equals the `surplus` field — whatever the camera or seeder last wrote. The agents never hard-code inventory; they read it.

### 2. Vector search — historical context retrieval

`vector_history.py` stores embeddings of past shortage events and their resolutions. When a new shortfall is detected, the agent retrieves semantically similar historical periods (by item, region, and demand pattern) to inform urgency scoring and transfer sizing. This is Redis as a **long-term memory store** for the AI reasoning layer.

### 3. Pub/Sub — live event fan-out

`channels:events` and `channels:alerts` fan out every state change — inventory updates, forecast changes, shortfall alerts, negotiation milestones, transfer settlements — to all subscribers simultaneously. The live dashboard subscribes to these channels; the SSE stream it serves to the browser is a direct relay of Redis pub/sub events. Adding a new consumer (Arize tracing, a mobile notification service) requires zero changes to the publishers.

### 4. Streams — durable audit log

Transfers and alerts are appended to Redis Streams (`stream:transfers`, `stream:alerts`). Streams give us an append-only, consumer-group-readable audit trail: every shortfall and every settled transfer is durably recorded with timestamps, quantities, hospital IDs, and transfer IDs. This is not a cache — it is the authoritative ledger.

### 5. Forecast state

Weather and illness agents write their signals to `forecast:{region}` hashes. The front agent reads these when evaluating urgency and when reasoning about whether a shortfall is demand-driven (temporary) or structural. Two independent agents can write different forecast dimensions (temperature, illness prevalence) to the same hash without stepping on each other.

### 6. Dashboard state bus

The Flask dashboard (`ui/app.py`) has no direct connection to the agent processes. It reads all pipeline state from Redis — inventory, forecasts, alerts, narration events, transfer log — and serves a live view of the negotiation as it runs. The SSE endpoint (`/api/narration`) proxies the Redis pub/sub channel directly to the browser. Adding the dashboard required zero changes to the agent code.

---

## Architecture: how the seam works

```
Camera (Claude Vision)  ──► redis_inventory.py ──► inventory:{hospital}:{item}
                                                          │
Weather agent           ──► forecast:{region}             │
Illness agent           ──►                               │
                                                          ▼
                                              Agents read inventory + forecast
                                                          │
                                              Negotiation milestones published
                                                          │
                                              channels:events / stream:transfers
                                                          │
Dashboard ◄──────────────────────────────────────────────┘
```

Every arrow is a Redis read or write. The agents, the camera, the forecast agents, and the dashboard are fully decoupled — they share only the Redis schema, documented in `redis_contract.md`.

---

## Key files

| File | What it does |
| :-- | :-- |
| `src/schema.py` | Redis key helpers, stream names, channel constants — the locked contract |
| `src/redis_client.py` | Connection helper, health check, `ping_redis` |
| `src/seed_demo_data.py` | Seeds hospital meta, inventory, surplus, and forecast for the demo |
| `src/inventory.py` | Inventory + surplus read/write; publishes `channels:events` on every update |
| `src/forecast.py` | Forecast read/write; publishes `channels:events` |
| `src/alerts.py` | Shortfall detection → `channels:alerts` + `stream:alerts` |
| `src/transfers.py` | Transfer settlement logging → `stream:transfers` + `channels:events` |
| `src/vector_history.py` | Embedding-based historical shortage recall |
| `src/demo_run.py` | End-to-end judge-friendly demo: seed → shortfall → negotiation → transfer → history recall |
| `redis_contract.md` | The locked Redis schema (key owners, readers, purpose) |

---

## Setup

```bash
# Start Redis Stack (includes RedisJSON, RediSearch for vector queries)
docker compose -f redis/docker-compose.redis.yml up -d

# Seed inventory, surplus, forecast, hospital metadata
cd redis/src && REDIS_URL=redis://localhost:6379 python seed_demo_data.py

# Run the full demo
python demo_run.py
```

Redis Stack UI available at `http://localhost:8001`.

To run the negotiation against live Redis:

```bash
STOCKPILE_REDIS=1 STOCKPILE_ITEM="IV fluids" STOCKPILE_NEED=200 \
  STOCKPILE_EXIT_WHEN_DONE=1 python agent-communication-layer/stockpile_agents.py
```

---

## Why Redis here

The hospital supply coordination problem is inherently multi-process and multi-source: camera, forecast agents, hospital agents, and dashboard all run concurrently and need to share state. A shared-memory approach (Python dicts, SQLite) would couple processes together. Redis gives us a fast, durable, pub/sub-capable bus with vector search and stream primitives built in — all of which Baymax actually uses.
