# Stockpile (Network) — Cal Hacks Build Roadmap

## The MVP (the only thing that must work)

One chain, end to end:

> A hospital is detected heading short on a supply → its agent queries the network → another hospital's agent negotiates and offers a transfer under real constraints → the transfer resolves and is announced on the dashboard.

Everything else is a bonus layered on top of this spine. If only this works, you have a finalist-grade demo and a strong Fetch + Anthropic submission.

---

## Optimal tech stack

The optimization: **Python everywhere on the backend, Redis as the single integration seam.** Fetch, Claude, and Redis all have first-class Python, so the four of you build in parallel against one shared state store instead of wiring into each other's code.

| Layer | Tool | Why |
| :-- | :-- | :-- |
| Agent mesh + negotiation | **Fetch.ai uAgents** (Python), registered on Agentverse | Co-host track, this is your moat |
| Reasoning brain | **Claude (Sonnet 4.6)** via Anthropic API, built with Claude Code | Anthropic track, the decision logic |
| Perception | **Claude Vision** (same SDK) + laptop webcam | Reuses one SDK, Pi is a stretch goal only |
| State + vector search + memory | **Redis Stack** (redis-py): inventory hashes, vector index over historical usage, pub/sub for live UI | Redis track, and your integration seam |
| Embeddings | **sentence-transformers** (local, no key) for MVP; Voyage AI if you want quality | Anthropic has no embeddings endpoint, keep it frictionless |
| Observability | **Arize Phoenix** (OTel tracing) | Honorable-mention track, light touch |
| Dashboard | **Next.js or Vite + React + Tailwind**, live via WebSocket/SSE off Redis pub/sub | The wow surface |
| Glue | **FastAPI** | Thin backend to bridge agents and UI |
| Forecast inputs | **Open-Meteo** (no key) + mocked CDC/WHO | Live scrape (Browserbase) is bonus, not MVP |
| Optional wow | Deepgram TTS, Poke iMessage | Layer only if M3 is done |

---

## Build this first: the Redis contract (hour 1)

This schema is what lets four people work without blocking. Lock it before anyone writes feature code.

```
hospital:{id}:inventory   (hash)   item -> {qty, pct, status, updated_at}
hospital:{id}:meta        (hash)   name, lat, lng, capacity
forecast:{region}         (json)   predicted demand per category
history:usage             (vector) embeddings of past usage periods (for similarity search)
transfers                 (stream) negotiated transfers log
channels: events, alerts  (pub/sub) drive the live dashboard
```

Producers: C writes inventory + forecast, B writes recommendations + history index, A writes transfers, D reads everything and renders. Nobody needs another person's code, only this schema.

---

## Workstreams (one owner each)

**A — Agent + Negotiation (Fetch).** The spine and the co-host prize. Build uAgents for 3 hospitals, a negotiation protocol (request / offer / accept with constraints: quantity, distance, expiry, criticality), register on Agentverse, write resolved transfers to Redis. Make the negotiation genuinely reason, including a case where the offering hospital can only spare part of the amount and the requester re-plans. Give this to your strongest agent dev.

**B — Intelligence (Claude + Redis vector).** The brain. A Claude reasoning agent that ingests inventory + forecast and outputs ranked restock/transfer recommendations. Build the Redis vector index over historical usage and the similarity query that grounds the forecast ("periods like this consumed X"). Owns the Anthropic and Redis narrative.

**C — Perception + Data.** The reliable trigger. Claude Vision estimating fill level from a webcam, plus a deterministic fallback you can fire on command so the demo never hangs on a flaky frame. Wire Open-Meteo and a mocked CDC/WHO feed into the forecast keys. The perception is plumbing, engineer it to never fail.

**D — Frontend, Demo + Observability.** The wow surface. Next.js dashboard showing per-hospital inventory, the convergence alert, and the negotiation/transfer resolving live (driven by Redis pub/sub). Wire Arize Phoenix traces. Own the demo choreography and record the backup video. Add Deepgram/Poke only if there is time.

---

## Milestones (24h, with exit criteria)

**M0 — Setup + contract.** First 60 to 90 min. Repo, API keys (Anthropic, Fetch, Redis Cloud), schema locked, hardware claimed at 11:15am. *Exit: every owner can read and write Redis from a stub.*

**M1 — Ugly vertical slice.** By ~hour 5 (Sat ~5pm). Hardcoded low trigger → Claude rec → one agent offers → transfer written → dashboard shows it. Zero polish. *Exit: the chain runs end to end once, even if half of it is faked.* This is the most important milestone, get the skeleton breathing early.

**M2 — Make each piece real.** By ~hour 11 (Sat ~11pm). Real Claude Vision trigger, real Fetch negotiation on Agentverse with constraints, real Redis vector search returning analogs, dashboard live off pub/sub. *Exit: every piece is genuine and integrated.* Draft the Devpost with all teammates now, the guide says this is the only way judging is guaranteed and the deadline is midnight Saturday.

**M3 — Convergence + harden.** Overnight, by ~hour 18 (Sun ~5am). Convergence detection (stock falling AND demand rising in the same category) becomes the trigger, negotiation shows disagreement/re-plan, Arize traces flowing, dashboard polished, optional voice/Poke. *Exit: the demo path runs clean three times in a row.*

**M4 — Freeze + rehearse.** Sun morning, by ~hour 23. Feature freeze, lock the demo choreography, record the backup video, rehearse the 4-min table pitch and the 2-min Q&A with the Chooch, regulatory, and "is the negotiation real" answers pre-loaded. *Exit: you can run the demo half-asleep.* Submit by 11am to noon.

---

## Cut lines (drop in this order if behind)

Browserbase live scrape → mocked feed. Deepgram voice → on-screen alert. Poke → skip. Pi camera → laptop webcam. Three hospitals → two. Arize → minimal traces.

**Never cut:** the real Fetch negotiation, one reliable trigger, and the live dashboard moment. Those three are the demo.
