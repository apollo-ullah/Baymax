"""run_hospital_b.py — per-agent Mailbox runner for Hospital B (surplus facility).

Registers Hospital B on Agentverse INDEPENDENTLY (its own process + Mailbox), so
the three Baymax agents can be deployed and discovered separately rather than
only as the in-process Bureau demo. Hospital B speaks ONLY the negotiation Models
(SupplyRequest -> SupplyOffer, TransferProposal -> TransferAccept/Reject); it does
NOT carry the Chat Protocol (only the FRONT agent / Hospital A does).

Contract notes:
  * agent_base is imported FIRST (its import installs the Python 3.14 event-loop
    workaround) BEFORE any Agent is constructed — the hard event-loop rule.
  * build_hospital_agent("Hospital B", mailbox=True) pins network=testnet and
    publishes agent details + README for Agentverse discoverability.
  * attach_hospital_handlers(agent, "Hospital B") wires the surplus-facility
    negotiation handlers (the same ones the Bureau demo uses).

Run:  ./.venv/bin/python run_hospital_b.py
Then complete the one-time Agentverse Mailbox connect (see the printed banner).
"""

from __future__ import annotations

import os

os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "30")
# Read this facility's spare from the live Redis backend (tracks/redis); falls
# back to the mock if Redis is unreachable. Override with BAYMAX_REDIS=0.
os.environ.setdefault("BAYMAX_REDIS", "1")

# Import agent_base FIRST so the event-loop workaround is installed before ANY
# Agent is constructed. baymax_agents also imports agent_base, but we name it
# explicitly here to make the ordering contract obvious.
import agent_base  # noqa: F401  (side-effect: installs current event loop)
from agent_base import build_hospital_agent

from baymax_agents import attach_hospital_handlers

FACILITY = "Hospital B"


def build_agent():
    """Build Hospital B in Mailbox mode with its negotiation handlers attached."""
    agent = build_hospital_agent(FACILITY, mailbox=True)
    attach_hospital_handlers(agent, FACILITY)
    return agent


if __name__ == "__main__":
    agent = build_agent()
    print("=" * 70)
    print(f"Baymax {FACILITY} agent (surplus facility) — Mailbox runner")
    print(f"  name    : {agent.name}")
    print(f"  address : {agent.address}")
    print(f"  network : {os.getenv('FETCH_NETWORK', 'testnet')} (TESTNET ONLY)")
    print("-" * 70)
    print("Manual Agentverse registration (one-time, needs a browser login):")
    print("  1. Run this file:            ./.venv/bin/python run_hospital_b.py")
    print("  2. Open the Agent Inspector URL printed below by uAgents.")
    print("  3. In Agentverse: Connect -> Mailbox -> Finish (one-time).")
    print("  4. Open the agent's Agentverse profile and copy its URL into")
    print("     DELIVERABLES.md (Agentverse profile URLs).")
    print("=" * 70)
    agent.run()
