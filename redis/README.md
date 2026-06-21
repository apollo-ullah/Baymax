# Redis Track

**Redis is the integration seam for the system.** Every track reads and writes through Redis, so it is the single source of truth that ties inventory, forecasting, alerts, transfers, and the live dashboard together.

## What Redis Powers

- **Real-time hospital inventory state** — current stock levels per item
- **Hospital metadata** — name, region, location, capacity
- **Surplus capacity** — what each hospital can spare for transfers
- **Forecast state** — predicted regional demand increases
- **Pub/Sub for dashboard events** — live fan-out of changes to the UI
- **Streams for transfer and alert audit logs** — durable, append-only history
- **Optional vector-backed historical memory** — recall of similar past periods

## Setup

```bash
cd redis
cp .env.example .env
docker compose -f docker-compose.redis.yml up -d
pip install -r requirements.txt
python3 src/demo_run.py
```

This starts a local Redis Stack instance (ports `6379` and `8001`), seeds demo data, and runs the full end-to-end flow: inventory, forecast, shortfall alerts, a hospital-to-hospital transfer, and vector history recall.

## Key Files

- `src/schema.py` — Redis key helpers, stream names, and channel constants
- `src/redis_client.py` — connection helper and health check (`ping_redis`)
- `src/seed_demo_data.py` — seeds hospital meta, inventory, surplus, forecast
- `src/inventory.py` — inventory + surplus read/write, publishes events
- `src/forecast.py` — forecast read/write, publishes events
- `src/alerts.py` — shortfall detection, publishes alerts + stream log
- `src/transfers.py` — transfer logging via stream, publishes events
- `src/vector_history.py` — optional embedding-based historical recall
- `src/demo_run.py` — end-to-end judge-friendly demo runner
- `redis_contract.md` — the locked Redis schema (owners, readers, purpose)
