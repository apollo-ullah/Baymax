# Redis Track — Teammate Handoff

Quick reference for frontend/backend teammates integrating with Redis.

## 1. Redis URL

```env
REDIS_URL=redis://localhost:6379
```

Copy `.env.example` to `.env` if you do not have one yet. The default already points here.

## 2. Main Redis Keys

| Key | Type | Holds |
| --- | --- | --- |
| `hospital:{id}:inventory` | Hash | Current stock per item as JSON with `qty`, `pct`, `status`, `updated_at` |
| `hospital:{id}:meta` | Hash | Hospital metadata: `name`, `region`, `lat`, `lng`, `capacity` |
| `hospital:{id}:surplus` | Hash | Spare quantity per item |
| `forecast:{region}` | String JSON | Regional demand forecast |
| `transfers` | Stream | Append-only log of supply transfers |
| `alerts:log` | Stream | Append-only log of raised alerts |
| `channels:events` | Pub/Sub | Live inventory, forecast, and transfer events |
| `channels:alerts` | Pub/Sub | Live alert notifications |
| `history:usage:{id}` | String JSON | Embedded past usage periods for vector-style recall |
| `scenario:{id}` | String JSON | Optional demo scenario profile (e.g. `scenario:heatstroke`) |
| `vision:latest` | String JSON | Latest raw camera-derived inventory counts (no percentages) |

## 3. How to Run

```bash
cd tracks/redis
python3 src/ping.py
python3 src/demo_run.py
```

`ping.py` should print `Redis connected successfully`. `demo_run.py` seeds data and runs the full end-to-end Redis flow.

## 4. What to Consume

- Inventory: read from `hospital:{id}:inventory`
- Forecast: read from `forecast:san_francisco`
- Alerts: read history from `alerts:log`, or subscribe live to `channels:alerts`
- Transfers: read from the `transfers` stream
- Optional scenario profile: read from `scenario:heatstroke` (demo scenario)
- Raw camera-derived inventory counts: read from `vision:latest` (counts only, no percentages)

## 5. Optional Scenario Profile

`scenario:heatstroke` — optional demo scenario profile linking Heatstroke, required supplies, facility placeholders, forecast status, and recommendation status. Read it with `scenario.get_scenario("heatstroke")`.

## 6. Important

Redis core is tested and working. Do not change Redis key names without telling the Redis owner because other tracks depend on the exact names above.
