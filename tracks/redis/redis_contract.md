# Redis Contract

The locked Redis schema for the stockpile track. Owner = who writes the key,
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

### `history:usage:{id}`
- **Type:** String (JSON `{text, item, usage_increase_pct, embedding}`)
- **Owner:** `vector_history.seed_history`
- **Reader:** `vector_history.find_similar_periods`
- **Why it matters:** Stores embedded past usage periods so we can recall similar historical events (local cosine similarity, no RediSearch needed).
