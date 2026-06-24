"""simulated_settlement.py — pure-simulated settlement for the Claude engine.

The Fetch.ai rewrite drops on-chain FET / Payment Protocol entirely. Settlement
is now narrated as a simulated inter-facility transfer reference per leg — no
wallet, no testnet, no cosmpy. The reference is deterministic and human-readable
so it reads convincingly in the UI ("Settlement: sim-1a2b3c-Hospital B; …").

If a future deploy wants real on-chain settlement back, restore the archived
settlement.py and register its hook instead.
"""

from __future__ import annotations

import logging

_log = logging.getLogger("baymax.settlement")


def settle(req_id: str, plan) -> str:
    """Return a settlement reference for the accepted plan (one ref per leg).

    `plan` is an interfaces.RankedPlan (or any object with `.allocations` whose
    items have `.offerer` and `.quantity`). Never raises — settlement must not
    break the negotiation.
    """
    refs: list[str] = []
    try:
        for leg in getattr(plan, "allocations", []) or []:
            ref = f"sim-{req_id}-{leg.offerer.replace(' ', '')}"
            _log.info("[settle] %s: %s %s SIMULATED -> %s",
                      leg.offerer, leg.quantity, getattr(plan, "item", ""), ref)
            refs.append(ref)
    except Exception as exc:  # noqa: BLE001 — fail-soft
        _log.warning("[settle] error building refs (%s)", exc)
    return ";".join(refs) if refs else f"sim-{req_id}"
