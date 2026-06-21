# Redis Contract

The locked Redis schema for the Redis track. Owner = who writes the key,
Reader = who consumes it. All values are kept simple (hashes, strings, streams)
so the demo runs without RediSearch.

---

### `hospital:{id}:inventory`
- **Type:** Hash (field = item name, value = JSON `{qty, pct, status, updated_at}`)
- **Owner:** `inventory.write_inventory`, `seed_demo_data.seed_inventory`
- **Reader:** `alerts`, `demo_run`, dashboard
- **Why it matters:** Source of truth for current stock levels per hospital; drives every shortfall decision.

### `hospital:{id}:meta`
- **Type:** Hash (`name`, `region`, `lat`, `lng`, `capacity`)
- **Owner:** `seed_demo_data.seed_hospital_meta`
- **Reader:** dashboard, mapping/routing logic
- **Why it matters:** Identifies and locates each hospital so transfers and maps make sense.

### `hospital:{id}:surplus`
- **Type:** Hash (field = item name, value = spare quantity)
- **Owner:** `inventory.write_surplus`, `seed_demo_data.seed_surplus`
- **Reader:** transfer matching logic
- **Why it matters:** Shows what each hospital can spare, enabling hospital-to-hospital transfers.

### `forecast:{region}`
- **Type:** String (JSON `{region, updated_at, items{...}}`)
- **Owner:** `forecast.write_forecast`, `seed_demo_data.seed_forecast`
- **Reader:** `alerts`, dashboard
- **Why it matters:** Predicts demand increases so we can act before a shortage happens.

### `transfers`
- **Type:** Stream (each entry has a `data` field of transfer JSON)
- **Owner:** `transfers.log_transfer`
- **Reader:** `transfers.get_recent_transfers`, dashboard
- **Why it matters:** Append-only audit log of every supply transfer between hospitals.

### `alerts:log`
- **Type:** Stream (each entry has an `alert` field of alert JSON)
- **Owner:** `alerts.detect_shortfall_and_publish`
- **Reader:** dashboard, audit/history views
- **Why it matters:** Durable history of every alert raised, even after live subscribers disconnect.

### `channels:events`
- **Type:** Pub/Sub channel
- **Owner:** `inventory`, `forecast`, `transfers`
- **Reader:** live dashboard subscribers
- **Why it matters:** Real-time fan-out of inventory/forecast/transfer changes for a live UI.

### `channels:alerts`
- **Type:** Pub/Sub channel
- **Owner:** `alerts.detect_shortfall_and_publish`
- **Reader:** live dashboard / notification subscribers
- **Why it matters:** Instantly pushes critical and warning alerts to anyone watching.

### `scenario:{id}`
- **Type:** JSON String
- **Owner:** `scenario.write_scenario`, `seed_demo_data.seed_scenarios`
- **Reader:** `scenario.get_scenario`, dashboard / reasoning layer
- **Why it matters:** Stores a high-level operational scenario linking disease, required supplies, facilities, forecast status, and recommendation status.

### `vision:latest`
- **Type:** JSON String
- **Owner:** `vision_sync.write_latest_vision_result`, `vision_sync.sync_vision_counts_to_redis`
- **Reader:** `vision_sync.get_latest_vision_result`, dashboard
- **Why it matters:** Stores the latest raw Claude Vision inventory detection output (counts only, no percentages).

### `history:usage:{id}`
- **Type:** String (JSON `{text, item, usage_increase_pct, embedding}`)
- **Owner:** `vector_history.seed_history`
- **Reader:** `vector_history.find_similar_periods`
- **Why it matters:** Stores embedded past usage periods so we can recall similar historical events (local cosine similarity, no RediSearch needed). _Status: P2 / not on the live demo path — only `demo_run` uses it._

### `reasoning:latest`
- **Type:** String (JSON `{risk_level, priority_items, reasoning, recommended_action, updated_at}`)
- **Owner:** `who_agent/fetcher.run_who_update` (the "Ingest Data" loop); the crisis-research seam may also write it
- **Reader:** dashboard (`ui/app.py`)
- **Why it matters:** Claude's proactive supply-risk recommendation — the output of the ingest/forecast loop. _(Previously undocumented; added in the realignment.)_

### `crisis:active`
- **Type:** String (JSON `{crisis_text, crisis_type, region, at_risk:[{item, risk, rationale}], rationale, status, created_at, updated_at}`)
- **Owner:** dashboard `POST /api/crisis` seeds it (`status="researching"`); the agent layer's `start_crisis` fills the research result (`status="researched"`). Helpers: `redis/src/crisis.py`, agent-side `redis_inventory.write_crisis_active`.
- **Reader:** dashboard
- **Why it matters:** The front-of-funnel for both demo surfaces — the user's crisis prompt + the inferred crisis type + ranked at-risk supplies. _(New in the realignment.)_

### `baymax:narration`
- **Type:** Pub/Sub channel
- **Owner:** FRONT agent narration sink (`agent-communication-layer/dashboard_bus.py`)
- **Reader:** dashboard SSE (`ui/app.py`, `scan_dashboard.py`)
- **Why it matters:** The channel the dashboard ACTUALLY subscribes to for live negotiation/crisis/ingest milestones. _(Previously undocumented. NOTE: the legacy `channels:events` / `channels:alerts` above currently have no live subscriber — see SCOPE_AUDIT.md; retire or wire in D4.)_
