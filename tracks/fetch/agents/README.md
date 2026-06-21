# Agents

Fetch.ai uAgents for the Stockpile network. One folder per agent.

Per the PRD (v0.2), the qualifying core is hospital uAgents that implement the
**Chat Protocol** (reachable in ASI:One), negotiate transfers, and settle with
the **Payment Protocol** on testnet FET. They share the wire contract in
[`../shared/protocol.py`](../shared/protocol.py). Redis state goes through the
Redis track via [`../shared/redis_io.py`](../shared/redis_io.py) (schema is owned
by `tracks/redis/`).

## Layout

```
agents/
├── requirements.txt          # shared deps for all agents
├── weather_agent/            # FR10 forecast input — weather (workstream B)
│   ├── agent.py              # the uAgent
│   └── models.py            # WeatherUpdate message schema
└── illness_agent/            # FR10 forecast input — mocked CDC/WHO (workstream B)
    ├── agent.py              # the uAgent
    ├── models.py            # IllnessUpdate message schema
    └── mock_cdc_feed.json   # mocked illness activity (live scrape = FR16, P2)
```

## What's built vs. planned

- ✅ `weather_agent/` — pulls Open-Meteo every 30s and writes a weather signal to
  `forecast:{region}` in Redis.
- ✅ `illness_agent/` — serves a mocked CDC/WHO illness feed every 60s and merges
  an illness signal into `forecast:{region}`.
- ⬜ Hospital agent(s) — Chat Protocol + negotiation (`SupplyRequest` →
  `SupplyOffer` → `TransferProposal` → accept) + Payment Protocol settlement.
  This is the P0 spine (FR4–FR8).

## Run (from the repo root)

```bash
pip install -r tracks/fetch/agents/requirements.txt
python -m tracks.fetch.agents.weather_agent.agent     # weather → forecast:{region}
python -m tracks.fetch.agents.illness_agent.agent     # illness → forecast:{region}

# verify Redis connectivity end to end:
python -m tracks.fetch.scripts.redis_smoke_test
```

> Requires Redis running (`REDIS_URL`, default `redis://localhost:6379`). The
> Redis schema/helpers live in `tracks/redis/`; our agents reach them through
> `tracks/fetch/shared/redis_io.py`.

> Note: at startup uAgents tries to register on the Fetch on-chain Almanac
> contract; without testnet tokens you'll see `_InactiveRpcError` errors. They
> are harmless for local agent-to-agent work — the agent still runs.
