# Agents

Fetch.ai uAgents for the Stockpile network. One folder per agent.

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
└── illness_agent/            # FR10 forecast input — mocked illness (workstream B)
    ├── agent.py              # the uAgent
    ├── models.py            # IllnessUpdate message schema
    └── mock_illness_feed.json  # mocked illness levels per region
```

## What's built vs. planned

- ✅ `weather_agent/` — pulls Open-Meteo every 30s and writes a weather signal to
  `forecast:{region}` in Redis.
- ✅ `illness_agent/` — posts a mocked `{illness -> level}` map into
  `forecast:{region}` every 60s. No weather/temperature; an intelligence agent
  reads both weather + illness and reasons over trends.
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
