"""check_wallets.py — one-shot wallet balance probe for all Baymax facilities.

Derives each hospital's on-chain fetch1 address (from seed_for / address_for),
queries atestfet balance via cosmpy, and prints the result.  Also probes the
address derived from AGENT_SEED_PHRASE if that env var is set.

Run:
    cd agent-communication-layer
    ../adyan-agent-communication-layer/.venv/bin/python check_wallets.py
"""

# CRITICAL: import agent_base FIRST — it installs the asyncio event loop
# and loads .env before any Agent/Protocol is constructed (Py3.14 requirement).
import agent_base  # noqa: F401  (side-effect: loop + load_dotenv)

import os

from cosmpy.aerial.client import LedgerClient, NetworkConfig
from cosmpy.aerial.wallet import LocalWallet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ATESTFET_DENOM = "atestfet"
ATESTFET_PER_FET = 1_000_000_000_000_000_000  # 1e18


def _fmt(raw_balance: int) -> str:
    fet = raw_balance / ATESTFET_PER_FET
    return f"{raw_balance} atestfet  ({fet:.6f} FET)"


def query_balance(client: LedgerClient, addr: str) -> str:
    """Return a human-readable balance string, or an 'unreachable' message."""
    try:
        bal = client.query_bank_balance(addr, ATESTFET_DENOM)
        return _fmt(int(bal))
    except Exception as exc:
        return f"unreachable ({type(exc).__name__}: {exc})"


def seed_source(facility: str) -> str:
    """Which seed is actually in effect — confirms the .env symlink + STOCKPILE
    alias worked. 'DEV FALLBACK' here means real seeds did NOT load (unfunded)."""
    env_name = agent_base.FACILITIES[facility]["seed_env"]   # e.g. BAYMAX_FRONT_SEED
    if os.getenv(env_name):
        return env_name
    alias = env_name.replace("BAYMAX_", "STOCKPILE_")
    if os.getenv(alias):
        return f"{alias} (alias)"
    return "DEV FALLBACK (public seed — unfunded)"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Baymax wallet balance probe  (TESTNET / dorado-1)")
    print("=" * 60)

    client = LedgerClient(NetworkConfig.fetchai_stable_testnet())

    facilities = ["Hospital A", "Hospital B", "Hospital C"]
    for facility in facilities:
        # address_for returns the uagents agent1q… identity address (not a fetch1… wallet addr).
        agent_identity_addr = agent_base.address_for(facility)
        print(f"\n{facility}")
        print(f"  seed source   : {seed_source(facility)}")
        print(f"  agent identity: {agent_identity_addr}")

        # The cosmpy-queryable wallet address (fetch1…) comes from agent.wallet.address().
        try:
            agent = agent_base.build_hospital_agent(facility)
            wallet_addr = str(agent.wallet.address())
            print(f"  wallet address: {wallet_addr}")
            balance = query_balance(client, wallet_addr)
            print(f"  balance       : {balance}")
        except Exception as exc:
            print(f"  wallet.address: (build failed: {type(exc).__name__}: {exc})")

    # Optional: AGENT_SEED_PHRASE
    phrase = os.getenv("AGENT_SEED_PHRASE", "").strip()
    if phrase:
        print("\n--- AGENT_SEED_PHRASE ---")
        try:
            wallet = LocalWallet.from_mnemonic(phrase)
            addr = str(wallet.address())
            balance = query_balance(client, addr)
            print(f"  address : {addr}")
            print(f"  balance : {balance}")
        except Exception as exc:
            print(f"  (failed: {type(exc).__name__}: {exc})")
    else:
        print("\n(AGENT_SEED_PHRASE not set — skipping mnemonic wallet probe)")

    print("\n" + "=" * 60)
    print("Done.")


if __name__ == "__main__":
    main()
