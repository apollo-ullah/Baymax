# Replace Fetch.ai uAgents with a Claude multi-agent negotiation engine

**Date:** 2026-06-21
**Status:** Approved (design)

## Goal

Replace the entire Fetch.ai uAgents negotiation engine with a Claude-driven
**multi-agent** one, behind the existing Redis seam, so the web UI / Flask
bridge / camera→Vision / `start_demo.sh` are otherwise untouched. Fetch code is
**archived, not deleted**.

## The frozen seam (what does NOT change)

The agent engine only ever talks to the rest of the stack through Redis:

- **In:** `baymax:crisis`, `baymax:trigger`, `baymax:decision` (lists; `dashboard_bus.pop_*`).
- **Out:** `baymax:narration` pubsub — dicts `{req_id, state, detail, final}` with the
  frozen lowercase `state` vocabulary: `researching` → `researched` → `scanned`
  → `shortfall_detected` → `requesting` → `collecting_offers` → `evaluating` →
  `awaiting_approval` → `proposing` → `settling` → `confirmed` (and `failed`,
  `ordering`/`ordered` for the external-order branch).
- Inventory/counts in Redis (`hospital:{id}:inventory/surplus`, `vision:latest`)
  written by the camera scan and read via `interfaces.get_inventory`.

`dashboard_bus.publish_narration` stays the narration sink. Web/Flask code is unchanged.

## Architecture — true multi-agent on `claude-sonnet-4-6`

New Claude-native modules (no uAgents, no cosmpy):

- **`hospital_agent.py`** — `decide_offer(hospital, item, qty_requested, requester) → HospitalOffer`.
  A Hospital B / C **Claude agent** acting as that facility's supply manager:
  reads its own inventory (`get_inventory`), computes `spare_capacity`, and asks
  Claude (forced tool-use `submit_offer`, mirroring `claude_ranking.py`) how many
  units to offer (0..spare), with a rationale. Fail-closed: on any Claude error,
  offer full spare (cooperative default). B and C run concurrently (`asyncio.gather`).
- **`simulated_settlement.py`** — `settle(req_id, plan) → ref`. Pure simulation,
  no FET/cosmpy/testnet: returns `sim-<reqid>-<hospital>;…`.
- **`claude_negotiation.py`** — the FRONT/coordinator agent + orchestration +
  per-negotiation state (`NEGOTIATIONS` dict) + narration. Public async entry
  points mirror the old ones but take no uAgents `ctx`:
  `start_crisis(crisis_text, requester, region)`,
  `start_negotiation(req_id, item, requester, need)`,
  `resume_after_admin_decision(req_id, decision)`.
  Reuses `interfaces.research_crisis` (Claude), `interfaces.get_inventory`,
  `interfaces.rank_offers` (Claude), `interfaces.distance_between/eta_minutes_for/expiry_for`.
- **`run_claude_demo.py`** — entrypoint replacing `run_dashboard_demo.py`: a plain
  `asyncio` loop that polls `dashboard_bus.pop_crisis/pop_trigger/pop_decision`,
  drives one negotiation at a time, and runs the 45s auto-approve watchdog.

## Flow (the pitch)

crisis → FRONT researches (`research_crisis`, Claude) → derive shortfall from
Redis/vision inventory → **ask Hospital B agent + Hospital C agent (each Claude,
concurrent)** → each reasons about how much surplus to spare → FRONT ranks +
composes the split (`rank_offers`, Claude) → `awaiting_approval` → approve (or
45s auto-approve) → **simulated** settlement → `confirmed`. Every milestone
streams to `baymax:narration` → web UI.

## Archived (not deleted) → `agent-communication-layer/archive/fetchai/`

The 18 uAgents/cosmpy-coupled files: `agent_base.py`, `baymax_agents.py`,
`protocol.py`, `settlement.py`, `front_agent.py`, `run_front.py`,
`run_dashboard_demo.py`, `run_hospital_b.py`, `run_hospital_c.py`,
`two_agent_payment_spike.py`, `check_arize_trace.py`, `check_wallets.py`,
`wave2`–`wave7` checks. A README in the archive explains the migration. Moved
with `git mv` to preserve history.

## `start_demo.sh` change

Bureau step launches `run_claude_demo.py` (not `run_dashboard_demo.py`); export
`BAYMAX_CLAUDE_RESEARCH=1`, `BAYMAX_CLAUDE_RANKING=1`, `BAYMAX_HOSPITAL_LLM=1`
(plus existing `ANTHROPIC_API_KEY`, `REDIS_URL`, `BAYMAX_REDIS=1`, scenario seed)
so the real Claude paths fire.

## Trade-offs (accepted)

1. Settlement is purely simulated — no real Fetch testnet tx (Fetch removed by request).
2. ~4 Sonnet calls per negotiation (research + B + C + rank); B/C concurrent →
   ~15–25s end-to-end. 45s auto-approve failsafe retained.

## Verification

End-to-end through the live stack: web crisis prompt → real Claude research →
Hospital B & C Claude offers in the narration → split plan → approve → confirmed.
Confirm `baymax:narration` payloads carry the frozen `state` strings so the UI
pipeline renders unchanged.
