"""settlement.py — STREAM PAY: production SELLER-side Payment Protocol logic.

The negotiation core (stockpile_agents.py) ends a successful deal at its
`settle_transfer()` hook. PAY replaces the Wave 0 stub with the real Fetch
**Payment Protocol** handshake on the FET testnet:

    SERVICE (us, our agent)              USER / BUYER (ASI:One wallet)
    ───────────────────────              ─────────────────────────────
    RequestPayment  ───────────────────▶
                    ◀───────────────────  CommitPayment(transaction_id)   (user approved + signed)
    [verify tx on-chain via cosmpy]
    CompletePayment ───────────────────▶  (on success)
       or
    CancelPayment   ───────────────────▶  (on verification failure)

    (the user may instead reply RejectPayment — we just log and stop)

──────────────────────────────────────────────────────────────────────────────
THE PAYMENT-ROLE DECISION (verified empirically, NOT from the docs prose)
──────────────────────────────────────────────────────────────────────────────
The installed uagents_core 0.4.7 `payment_protocol_spec.roles` maps each role to
the set of messages that role is allowed to *RECEIVE* (i.e. register an
`on_message` handler for). Empirically:

    roles['seller'] = {CommitPayment, RejectPayment}
    roles['buyer']  = {RequestPayment, CompletePayment, CancelPayment}

Our SERVICE agent is the payment INITIATOR. It must:
    SEND    : RequestPayment, CompletePayment, CancelPayment
    RECEIVE : CommitPayment, RejectPayment

`Protocol(spec=payment_protocol_spec, role="seller")` is the role whose
`on_message` registration accepts exactly {CommitPayment, RejectPayment} — the
messages we must receive — and the interaction graph lets that same role *send*
RequestPayment (the initiating edge) and reply to CommitPayment with
CompletePayment / CancelPayment. So:

        ►►►  VERIFIED ROLE:  role="seller"  ◄◄◄

This is the INVERSE of the human-readable Fetch docs ("buyer requests"), which is
exactly the landmine the PAY brief warned about. We register on the *installed*
spec, never the prose.

──────────────────────────────────────────────────────────────────────────────
Environment knobs (see .env additions in the PAY report — no secrets):
    STOCKPILE_PAYMENT_AMOUNT_FET   default "0.1"   FET charged per settlement
    PAYMENT_VERIFY_ONCHAIN         default "true"  "false" => skip cosmpy query
                                                   and trust the commit (DEV ONLY)
    FETCH_NETWORK                  "testnet" (guardrail; we pin testnet config)
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

# Import agent_base FIRST so the Python 3.14 event-loop workaround is installed
# before anything constructs an Agent/Protocol (contract rule).
import agent_base  # noqa: F401  (side-effect: installs current event loop)

from uagents import Context, Protocol

# Official Fetch payment classes come exclusively from protocol.py (re-exported,
# matched by schema digest). NEVER redefine them locally.
from protocol import (
    Funds,
    RequestPayment,
    CommitPayment,
    CompletePayment,
    CancelPayment,
    RejectPayment,
    payment_protocol_spec,
)

# ---------------------------------------------------------------------------
# Configuration (env-driven; testnet only).
# ---------------------------------------------------------------------------

#: The verified role string for our SERVICE agent (see module docstring).
PAYMENT_ROLE = "seller"

#: FET amount charged per settlement (string, as the protocol expects).
PAYMENT_AMOUNT_FET = os.getenv("STOCKPILE_PAYMENT_AMOUNT_FET", "0.1")

#: Seconds the user has to approve + sign the payment before it expires.
PAYMENT_DEADLINE_S = int(os.getenv("STOCKPILE_PAYMENT_DEADLINE_S", "300"))

#: testnet denom for FET on the Fetch stable testnet (dorado-1).
TESTNET_DENOM = "atestfet"

#: agent-address -> our FET wallet (fetch1...) address. Populated by
#: register_recipient_wallet() at agent-construction time, so request_payment()
#: can resolve the payee even from inside a handler (where ctx.agent is an
#: AgentRepresentation that does NOT expose .wallet).
_RECIPIENT_WALLETS: dict[str, str] = {}


def register_recipient_wallet(agent) -> str:
    """Record `agent`'s FET wallet address so payments can name it as recipient.

    Call this ONCE at construction time (module scope) with the full Agent
    object — that is where `agent.wallet.address()` is available. Returns the
    resolved wallet address.

    Why: inside an on_event/on_message handler, `ctx.agent` is an
    AgentRepresentation without a `.wallet`, so we cache the mapping here.
    """
    wallet_addr = str(agent.wallet.address())
    _RECIPIENT_WALLETS[agent.address] = wallet_addr
    return wallet_addr


def resolve_recipient_wallet(ctx: Context) -> str:
    """Best-effort resolve OUR FET wallet (fetch1...) address for `ctx`'s agent.

    Order:
      1. `ctx.agent.wallet.address()` if present (full Agent context).
      2. the fetch1... address registered via register_recipient_wallet() for
         this agent (the canonical path — call it once at construction time).
      3. last resort: the agent's agent1... address, so we never crash. NOTE: a
         real on-chain transfer needs a fetch1... wallet; if you see an agent1...
         recipient it means register_recipient_wallet() was not called — wire it
         in at construction. (The negotiation core's agents are built in
         agent_base.build_hospital_agent; register there or right after.)
    """
    # 1) full Agent context (rare inside handlers, but cheap to try).
    wallet = getattr(ctx.agent, "wallet", None)
    if wallet is not None:
        try:
            return str(wallet.address())
        except Exception:
            pass
    # 2) registered mapping (set at construction via register_recipient_wallet).
    addr = getattr(ctx.agent, "address", None)
    if addr and addr in _RECIPIENT_WALLETS:
        return _RECIPIENT_WALLETS[addr]
    # 3) never crash (see note above — this is not a valid on-chain payee).
    return str(addr) if addr else ""


def _verify_onchain_enabled() -> bool:
    """Read PAYMENT_VERIFY_ONCHAIN at call time (so spikes can flip it)."""
    return os.getenv("PAYMENT_VERIFY_ONCHAIN", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def make_funds(amount_fet: Optional[str] = None) -> Funds:
    """Build the Funds block for one settlement (FET via direct transfer)."""
    return Funds(
        currency="FET",
        amount=str(amount_fet if amount_fet is not None else PAYMENT_AMOUNT_FET),
        payment_method="fet_direct",
    )


# ---------------------------------------------------------------------------
# On-chain verification (DEFENSIVE — must never hang or crash the agent).
# ---------------------------------------------------------------------------

def verify_payment_onchain(transaction_id: str) -> bool:
    """Return True iff `transaction_id` is a successful tx on the FET testnet.

    Defensive by design:
      * If PAYMENT_VERIFY_ONCHAIN is false, SKIP the chain query and treat the
        commit as valid (DEV/SPIKE only — no real settlement guarantee).
      * Any cosmpy/network error (not-found, RPC down, timeout) => returns False
        rather than propagating, so a flaky node can never wedge the handler.
    """
    if not _verify_onchain_enabled():
        return True  # DEV ONLY: trust the commit without touching the chain.

    if not transaction_id:
        return False

    try:
        # Imported lazily so importing settlement never requires a live node.
        from cosmpy.aerial.client import LedgerClient, NetworkConfig

        client = LedgerClient(NetworkConfig.fetchai_stable_testnet())
        resp = client.query_tx(transaction_id)
        return bool(resp.is_successful())
    except Exception:
        # NotFoundError, grpc.RpcError, connection timeouts, etc. all land here.
        return False


# ---------------------------------------------------------------------------
# Protocol factory + handlers.
# ---------------------------------------------------------------------------

def build_payment_protocol() -> Protocol:
    """The SELLER-side Payment Protocol for our service agent.

    Returns a Protocol(spec=payment_protocol_spec, role="seller") with handlers
    for the two messages we RECEIVE: CommitPayment and RejectPayment. Attach to
    an agent with `agent.include(proto, publish_manifest=True)`.
    """
    proto = Protocol(spec=payment_protocol_spec, role=PAYMENT_ROLE)

    @proto.on_message(CommitPayment)
    async def on_commit(ctx: Context, sender: str, msg: CommitPayment):
        """User signed + committed a payment; verify it, then complete/cancel."""
        ctx.logger.info(
            f"[payment] CommitPayment from {sender} "
            f"tx={msg.transaction_id} ref={msg.reference} "
            f"amount={msg.funds.amount} {msg.funds.currency}"
        )
        # Run the (blocking) cosmpy on-chain query in a worker thread so a slow or
        # unreachable testnet RPC can never freeze the agent's event loop (it would
        # otherwise stall all other messages/intervals for the whole query).
        ok = await asyncio.to_thread(verify_payment_onchain, msg.transaction_id)
        if ok:
            verified = "skipped (PAYMENT_VERIFY_ONCHAIN=false)" \
                if not _verify_onchain_enabled() else "succeeded on testnet"
            ctx.logger.info(
                f"[payment] Verification {verified} for tx={msg.transaction_id} "
                f"-> sending CompletePayment."
            )
            await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
        else:
            ctx.logger.warning(
                f"[payment] Verification FAILED for tx={msg.transaction_id} "
                f"-> sending CancelPayment."
            )
            await ctx.send(sender, CancelPayment(
                transaction_id=msg.transaction_id,
                reason="on-chain verification failed (tx not found or unsuccessful)",
            ))

    @proto.on_message(RejectPayment)
    async def on_reject(ctx: Context, sender: str, msg: RejectPayment):
        """User declined the payment request — log and stop (no settlement)."""
        ctx.logger.info(f"[payment] RejectPayment from {sender}: {msg.reason}")

    return proto


# ---------------------------------------------------------------------------
# Sending side — request a payment.
# ---------------------------------------------------------------------------

async def request_payment(
    ctx: Context,
    user_address: str,
    amount: Optional[str] = None,
    reference: Optional[str] = None,
    description: Optional[str] = None,
) -> RequestPayment:
    """SEND a RequestPayment to `user_address` and return the message we sent.

    recipient is OUR FET wallet address (where the FET should land), per the
    protocol. We resolve it via resolve_recipient_wallet(ctx) — equivalent to
    `ctx.agent.wallet.address()` at module scope, but also works inside handlers
    (where ctx.agent has no .wallet) provided register_recipient_wallet() was
    called at construction time. The user's ASI:One wallet replies with
    CommitPayment once they approve + sign.
    """
    funds = make_funds(amount)
    req = RequestPayment(
        accepted_funds=[funds],
        recipient=resolve_recipient_wallet(ctx),
        deadline_seconds=PAYMENT_DEADLINE_S,
        reference=reference,
        description=description,
    )
    ctx.logger.info(
        f"[payment] Sending RequestPayment to {user_address}: "
        f"{funds.amount} {funds.currency} -> {req.recipient} "
        f"(ref={reference}, deadline={PAYMENT_DEADLINE_S}s)"
    )
    await ctx.send(user_address, req)
    return req


# ---------------------------------------------------------------------------
# Pricing.
# ---------------------------------------------------------------------------

def _amount_for_total(total_units: int) -> str:
    """FET to charge for a settlement.

    Per-unit when STOCKPILE_PAYMENT_PER_UNIT_FET is set (amount = per_unit *
    units, minimum one unit), otherwise the flat STOCKPILE_PAYMENT_AMOUNT_FET.
    Read at call time so deployments/tests can change pricing without re-import.
    """
    per_unit = os.getenv("STOCKPILE_PAYMENT_PER_UNIT_FET", "").strip()
    if per_unit:
        try:
            return str(round(float(per_unit) * max(int(total_units), 1), 6))
        except (ValueError, TypeError):
            pass
    return os.getenv("STOCKPILE_PAYMENT_AMOUNT_FET", PAYMENT_AMOUNT_FET)


# ---------------------------------------------------------------------------
# Integrator entry point — called from stockpile_agents.settle_transfer.
# ---------------------------------------------------------------------------

async def settle_via_payment_protocol(
    ctx: Context,
    req_id: str,
    plan,
    user_address: str,
    reply_to: Optional[str] = None,
) -> str:
    """Kick off settlement for a completed negotiation via the Payment Protocol.

    The integrator wires this into stockpile_agents.settle_transfer. It computes
    the charge for `plan`, sends a RequestPayment to `user_address` (the ASI:One
    user who must approve the FET payment), and returns a settlement reference.

    NOTE: this returns as soon as the RequestPayment is *sent*. Final
    confirmation (CompletePayment / CancelPayment) is driven asynchronously by
    the on_commit handler in build_payment_protocol() once the user signs. The
    returned reference (`pay-<req_id>`) ties the request to its eventual commit
    via RequestPayment.reference, so the integrator/UI can correlate them.

    Args:
        ctx:          the agent Context (must be an agent carrying the payment
                      protocol — include build_payment_protocol()).
        req_id:       the negotiation request id (used as the payment reference).
        plan:         the RankedPlan being settled (for the human description).
        user_address: agent address of the ASI:One user who pays.
        reply_to:     optional chat address for narration (unused here; the
                      negotiation core already narrates via _step()).

    Returns:
        The settlement reference string (`pay-<req_id>`).
    """
    reference = f"pay-{req_id}"
    legs = getattr(plan, "allocations", []) or []
    n = len(legs)
    total = sum(getattr(a, "quantity", 0) for a in legs)
    amount = _amount_for_total(total)
    description = (
        f"STOCKPILE inter-facility transfer settlement {req_id}: "
        f"{n} leg(s), {total} unit(s) total."
    )
    await request_payment(
        ctx,
        user_address=user_address,
        amount=amount,
        reference=reference,
        description=description,
    )
    ctx.logger.info(
        f"[payment] settle_via_payment_protocol: requested {amount} "
        f"FET for {req_id} from {user_address}; reference={reference}. "
        f"Awaiting CommitPayment."
    )
    return reference


__all__ = [
    "PAYMENT_ROLE",
    "PAYMENT_AMOUNT_FET",
    "PAYMENT_DEADLINE_S",
    "TESTNET_DENOM",
    "make_funds",
    "register_recipient_wallet",
    "resolve_recipient_wallet",
    "verify_payment_onchain",
    "build_payment_protocol",
    "request_payment",
    "settle_via_payment_protocol",
]
