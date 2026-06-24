# Archived — Fetch.ai uAgents negotiation engine

These files are the original **Fetch.ai uAgents + cosmpy** implementation of the
Baymax supply-negotiation network. They were **archived (not deleted)** when the
engine was replaced by a **Claude multi-agent** orchestrator. History is
preserved — every file was moved with `git mv`.

## Why

The negotiation engine was rebuilt to be Claude-native: the FRONT/coordinator and
each surplus hospital are now Claude agents reasoning directly, with **simulated**
settlement instead of on-chain FET. The rest of the stack (Next.js web, Flask
bridge `ui/app.py`, camera → Claude Vision, Redis state bus, `start_demo.sh`) is
unchanged — the engine only ever talked to it through the frozen Redis seam
(`baymax:crisis` / `baymax:trigger` / `baymax:decision` in; `baymax:narration`
out), and the replacement honours that seam and the same lowercase `state`
vocabulary verbatim.

See `docs/superpowers/specs/2026-06-21-claude-multiagent-negotiation-design.md`.

## What replaced what

| Archived (uAgents)              | Replacement (Claude, no uAgents)             |
| :------------------------------ | :------------------------------------------- |
| `baymax_agents.py` (core + FSM) | `claude_negotiation.py`                      |
| `front_agent.py` (FRONT + chat) | `claude_negotiation.py` (coordinator)        |
| Hospital B/C `on_request`       | `hospital_agent.py` (`decide_offer`, Claude) |
| `settlement.py` (FET / cosmpy)  | `simulated_settlement.py`                    |
| `run_dashboard_demo.py` (Bureau)| `run_claude_demo.py` (asyncio loop)          |
| `run_front.py`, `run_hospital_*`| — (no separate processes / Mailbox)          |
| `protocol.py`, `agent_base.py`  | — (no wire models / event-loop shim needed)  |
| `wave2`–`wave7` checks, spikes  | — (offline harnesses for the old engine)     |

The seams that **stayed** (`interfaces.py`, `redis_inventory.py`,
`claude_ranking.py`, `claude_research.py`, `vision_inventory.py`,
`supplier_order.py`, `dashboard_bus.py`) are shared by both engines and were not
moved.

## Restoring on-chain settlement

If a future deploy wants real Fetch testnet settlement back, restore
`settlement.py` and register its hook (`settle_via_payment_protocol` /
`settle_via_direct_transfer`) on the negotiation core, as `run_front.py` /
`run_dashboard_demo.py` did. The Claude engine deliberately keeps settlement
behind a single function (`simulated_settlement.settle`) so the swap is local.
