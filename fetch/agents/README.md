# Agents

Fetch.ai uAgents for the Baymax network. One folder per agent.

Per the PRD (v0.2), the qualifying core is hospital uAgents that implement the
**Chat Protocol** (reachable in ASI:One), negotiate transfers, and settle with
the **Payment Protocol** on testnet FET. They share the wire contract in
[`../shared/protocol.py`](../shared/protocol.py). Redis state goes through the
Redis track via [`../shared/redis_io.py`](../shared/redis_io.py) (schema is owned
by `redis/`).

## Layout

```
agents/
├── requirements.txt          # shared deps for all agents
├── weather_agent/            # FR10 forecast input — weather (workstream B)
│   ├── agent.py              # the uAgent
│   └── models.py            # WeatherUpdate message schema
├── illness_agent/            # FR10 forecast input — mocked illness (workstream B)
│   ├── agent.py              # the uAgent
│   ├── models.py            # IllnessUpdate message schema
│   ├── mock_illness_feed.json  # mocked illness levels per region
│   └── mock_cdc_feed.json      # mocked CDC/WHO ILINet levels (stub source)
└── who_agent/                # de-facto unified ingest orchestrator
    └── fetcher.py            # run_who_update / run_ingest
```

## The unified ingest loop (`who_agent/fetcher.py`)

`run_who_update(region)` (aliased `run_ingest`) is the **de-facto unified ingest
orchestrator** — it gathers every forecast signal in one pass and produces a
proactive supply recommendation:

1. **WHO disease burden** — live COVID-19 30-day history via
   [disease.sh](https://disease.sh) (WHO/JHU CSSE), no key.
2. **Weather** — live current conditions via Open-Meteo, no key.
3. **Illness** — mocked `{disease -> level}` map from `illness_agent/mock_illness_feed.json`.
4. **CDC** — mocked ILINet `{disease -> level}` stub from
   `illness_agent/mock_cdc_feed.json` (labeled as a stub; CDC not yet integrated).

All four are merge-written into `forecast:{region}` via
`redis_io.upsert_forecast_items` (so concurrent weather/illness writers aren't
clobbered), then Claude reasons over the combined context (plus inventory,
camera vision, and any active scenario) and writes its `{risk_level,
priority_items, reasoning, recommended_action}` to `reasoning:latest`. The agent
layer reaches this through `agent-communication-layer/ingest_orchestrator.py`.

## What's built vs. planned

- ✅ `weather_agent/` — pulls Open-Meteo every 30s and writes a weather signal to
  `forecast:{region}` in Redis.
- ✅ `illness_agent/` — posts a mocked `{illness -> level}` map into
  `forecast:{region}` every 60s.
- ✅ `who_agent/` — the unified ingest orchestrator above (WHO + weather +
  illness + CDC stub → `forecast:{region}` → Claude reasoning → `reasoning:latest`).
- ⬜ Hospital agent(s) — Chat Protocol + negotiation (`SupplyRequest` →
  `SupplyOffer` → `TransferProposal` → accept) + Payment Protocol settlement.
  This is the P0 spine (FR4–FR8).

## Run (from the repo root)

```bash
pip install -r fetch/agents/requirements.txt
python -m fetch.agents.weather_agent.agent     # weather → forecast:{region}
python -m fetch.agents.illness_agent.agent     # illness → forecast:{region}

# verify Redis connectivity end to end:
python -m fetch.scripts.redis_smoke_test
```

> Requires Redis running (`REDIS_URL`, default `redis://localhost:6379`). The
> Redis schema/helpers live in `redis/`; our agents reach them through
> `fetch/shared/redis_io.py`.

> Note: at startup uAgents tries to register on the Fetch on-chain Almanac
> contract; without testnet tokens you'll see `_InactiveRpcError` errors. They
> are harmless for local agent-to-agent work — the agent still runs.
