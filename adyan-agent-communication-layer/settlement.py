"""settlement.py — STREAM PAY: production SELLER-side Payment Protocol logic.

The negotiation core (baymax_agents.py) ends a successful deal at its
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
    BAYMAX_PAYMENT_AMOUNT_FET   default "0.1"   FET charged per settlement
    PAYMENT_VERIFY_ONCHAIN         default "true"  "false" => skip cosmpy query
                                                   and trust the commit (DEV ONLY)
    BAYMAX_PAYMENT_VERIFY_TIMEOUT default "20"  wall-clock budget (s) for the
                                                   bounded on-chain verification
    PAYMENT_VERIFY_STRICT          default "false" "true" => CancelPayment when the
                                                   RPC is unreachable (else accept)
    FETCH_NETWORK                  "testnet" (guardrail; we pin testnet config)
"""

from __future__ import annotations

import asyncio
import os
import time
from enum import Enum
from typing import Optional

# Import agent_base FIRST so the Python 3.14 event-loop workaround is installed
# before anything constructs an Agent/Protocol (contract rule).
from agent_base import create_text_chat  # noqa: E402  (after loop setup)

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
PAYMENT_AMOUNT_FET = os.getenv("BAYMAX_PAYMENT_AMOUNT_FET", "0.1")

#: Seconds the user has to approve + sign the payment before it expires.
PAYMENT_DEADLINE_S = int(os.getenv("BAYMAX_PAYMENT_DEADLINE_S", "300"))

#: testnet denom for FET on the Fetch stable testnet (dorado-1).
TESTNET_DENOM = "atestfet"

#: Total wall-clock budget (seconds) for verifying a CommitPayment on-chain. The
#: handler NEVER blocks longer than this, so a slow/unreachable dorado-1 RPC can no
#: longer freeze settlement; we poll within the budget to absorb indexing lag.
PAYMENT_VERIFY_TIMEOUT_S = float(os.getenv("BAYMAX_PAYMENT_VERIFY_TIMEOUT", "20"))

#: Cap (seconds) for a single query attempt, so one hung cosmpy call cannot eat the
#: whole budget (its worker thread is orphaned and we move on).
_PAYMENT_VERIFY_ATTEMPT_S = float(os.getenv("BAYMAX_PAYMENT_VERIFY_ATTEMPT", "8"))

#: Strict mode: when the RPC is unreachable / times out (verification INCONCLUSIVE),
#: REJECT the payment instead of accepting it on ASI:One's own verification. Default
#: false — ASI:One verifies the tx on-chain BEFORE sending CommitPayment, so an
#: unreachable re-check should not reject a real, already-settled payment.
PAYMENT_VERIFY_STRICT = os.getenv("PAYMENT_VERIFY_STRICT", "").strip().lower() in (
    "1", "true", "yes", "on",
)

#: agent-address -> our FET wallet (fetch1...) address. Populated by
#: register_recipient_wallet() at agent-construction time, so request_payment()
#: can resolve the payee even from inside a handler (where ctx.agent is an
#: AgentRepresentation that does NOT expose .wallet).
_RECIPIENT_WALLETS: dict[str, str] = {}

#: pay-<req_id> -> {reply_to, req_id} so CompletePayment/CancelPayment can
#: narrate back into the ASI:One chat (CommitPayment is async after CONFIRMED).
_PAYMENT_PENDING: dict[str, dict] = {}


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

class VerifyResult(str, Enum):
    """Outcome of an on-chain verification attempt.

    The key distinction is NOT_FOUND (the RPC answered and the tx is absent or
    unsuccessful — a confident negative) vs INCONCLUSIVE (the node was unreachable
    or errored — we genuinely cannot tell). The handler rejects only on a confident
    negative, so a flaky RPC never causes a real, already-settled payment to be
    cancelled.
    """

    VERIFIED = "verified"          # tx found and successful on the testnet
    NOT_FOUND = "not_found"        # RPC reachable, tx absent or unsuccessful
    INCONCLUSIVE = "inconclusive"  # RPC unreachable / timed out — cannot tell
    SKIPPED = "skipped"            # PAYMENT_VERIFY_ONCHAIN=false (DEV)


def _verify_status(transaction_id: str) -> "VerifyResult":
    """Classify `transaction_id` on the FET testnet (blocking; run in a thread).

    Returns a VerifyResult, distinguishing a confident NOT_FOUND from an
    INCONCLUSIVE (unreachable node) so the caller can reject only when sure.
    """
    if not _verify_onchain_enabled():
        return VerifyResult.SKIPPED
    if not transaction_id:
        return VerifyResult.NOT_FOUND

    try:
        # Imported lazily so importing settlement never requires a live node.
        from cosmpy.aerial.client import LedgerClient, NetworkConfig
        from cosmpy.aerial.exceptions import NotFoundError

        client = LedgerClient(NetworkConfig.fetchai_stable_testnet())
        try:
            resp = client.query_tx(transaction_id)
        except NotFoundError:
            # Reachable RPC, tx not indexed (yet) or absent — a confident negative
            # for THIS poll (the retry loop re-checks within the budget).
            return VerifyResult.NOT_FOUND
        return (
            VerifyResult.VERIFIED if resp.is_successful() else VerifyResult.NOT_FOUND
        )
    except Exception:
        # grpc.RpcError, connection refused/timeout, contract-version failure, etc.
        # We cannot tell — do NOT report a false negative.
        return VerifyResult.INCONCLUSIVE


def verify_payment_onchain(transaction_id: str) -> bool:
    """Backward-compatible bool: True iff verified (or verification skipped).

    Retained for the spike + external imports; new code should use _verify_status
    / verify_payment_onchain_with_retry to distinguish INCONCLUSIVE from NOT_FOUND.
    """
    return _verify_status(transaction_id) in (
        VerifyResult.VERIFIED, VerifyResult.SKIPPED,
    )


async def verify_payment_onchain_with_retry(
    transaction_id: str,
    *,
    timeout_s: Optional[float] = None,
    delay_s: float = 2.0,
) -> "VerifyResult":
    """Poll the testnet for `transaction_id` within a bounded wall-clock budget.

    Returns the BEST result observed before the deadline: VERIFIED as soon as the
    tx confirms (returns immediately), otherwise the last NOT_FOUND / INCONCLUSIVE
    seen when the budget expires.

    Why bounded: ASI:One sends CommitPayment the instant the wallet signs, and the
    dorado-1 RPC may need a few seconds to index (NOT_FOUND) or may be unreachable
    (INCONCLUSIVE). The old fixed-retry version could block the handler for minutes
    on a hung RPC, so ASI:One would sit on "processing your response" forever. Each
    query is capped (_PAYMENT_VERIFY_ATTEMPT_S) via asyncio.wait, which returns at
    the cap WITHOUT awaiting a still-running query (we cannot cancel a thread that
    is mid blocking-call) — the orphaned worker thread finishes harmlessly later and
    its result is discarded. (asyncio.wait_for would instead wait for the orphan,
    defeating the bound.)
    """
    if not _verify_onchain_enabled():
        return VerifyResult.SKIPPED

    budget = PAYMENT_VERIFY_TIMEOUT_S if timeout_s is None else timeout_s
    deadline = time.monotonic() + max(0.0, budget)
    last = VerifyResult.INCONCLUSIVE
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        task = asyncio.ensure_future(asyncio.to_thread(_verify_status, transaction_id))
        done, _pending = await asyncio.wait(
            {task}, timeout=min(remaining, _PAYMENT_VERIFY_ATTEMPT_S),
        )
        if task in done:
            try:
                last = task.result()
            except Exception:
                last = VerifyResult.INCONCLUSIVE
        else:
            # Query still running past the cap — orphan it (a thread mid
            # blocking-call cannot be cancelled) and swallow its eventual result so
            # asyncio does not warn about an un-retrieved exception.
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            last = VerifyResult.INCONCLUSIVE
        if last == VerifyResult.VERIFIED:
            return last
        # NOT_FOUND (indexing lag) and INCONCLUSIVE (flaky RPC) are both worth
        # another poll while the budget allows.
        if time.monotonic() + delay_s >= deadline:
            break
        await asyncio.sleep(delay_s)
    return last


def _resolve_pending_key(reference: Optional[str]) -> str:
    """Map a CommitPayment.reference back to our stored _PAYMENT_PENDING key.

    ASI:One does not always echo the exact RequestPayment.reference back in the
    CommitPayment (it may be empty or rewritten). When the reference does not
    match but there is exactly ONE payment in flight, fall back to it — otherwise
    the payment_confirmed / terminal CONFIRMED narration (which is keyed on this
    reference) would be silently skipped even though the payment succeeded.
    """
    key = reference or ""
    if key in _PAYMENT_PENDING:
        return key
    if len(_PAYMENT_PENDING) == 1:
        return next(iter(_PAYMENT_PENDING))
    return key


async def _narrate_payment(ctx: Context, reference: Optional[str], text: str) -> None:
    pending = _PAYMENT_PENDING.get(_resolve_pending_key(reference))
    reply_to = pending.get("reply_to") if pending else None
    if reply_to:
        await ctx.send(reply_to, create_text_chat(text, end_session=False))


async def _finalize_from_payment(
    ctx: Context, reference: Optional[str], tx_id: str | None,
) -> None:
    key = _resolve_pending_key(reference)
    pending = _PAYMENT_PENDING.pop(key, None)
    if not pending:
        return
    if pending.get("kind") == "order":
        from baymax_agents import finalize_order_after_payment  # lazy import

        await finalize_order_after_payment(ctx, pending["req_id"], key, tx_id=tx_id)
    else:
        from baymax_agents import finalize_after_payment  # lazy import

        await finalize_after_payment(ctx, pending["req_id"], key, tx_id=tx_id)


async def _fail_from_payment(ctx: Context, reference: Optional[str], reason: str) -> None:
    key = _resolve_pending_key(reference)
    pending = _PAYMENT_PENDING.pop(key, None)
    if not pending:
        return
    if pending.get("kind") == "order":
        from baymax_agents import fail_order_after_payment  # lazy import

        await fail_order_after_payment(ctx, pending["req_id"], reason)
    else:
        from baymax_agents import fail_after_payment  # lazy import

        await fail_after_payment(ctx, pending["req_id"], reason)


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
        # Bounded on-chain verification: NEVER blocks longer than
        # PAYMENT_VERIFY_TIMEOUT_S (runs the blocking cosmpy query in a worker
        # thread), so a slow/unreachable dorado-1 RPC cannot freeze settlement —
        # ASI:One would otherwise sit on "processing your response" indefinitely.
        result = await verify_payment_onchain_with_retry(msg.transaction_id)

        # Accept on VERIFIED/SKIPPED. On INCONCLUSIVE (RPC unreachable/timed out)
        # accept too UNLESS strict mode — ASI:One verifies the tx on-chain before
        # sending CommitPayment, so an unreachable re-check must not reject a real,
        # already-settled payment. Reject only on NOT_FOUND (a confident negative).
        accept = result in (VerifyResult.VERIFIED, VerifyResult.SKIPPED) or (
            result == VerifyResult.INCONCLUSIVE and not PAYMENT_VERIFY_STRICT
        )

        if accept:
            if result == VerifyResult.VERIFIED:
                note = "verified on testnet"
                chat = (
                    f"**payment_confirmed** — Testnet settlement verified on-chain "
                    f"(tx `{msg.transaction_id}`)."
                )
            elif result == VerifyResult.SKIPPED:
                note = "skipped (PAYMENT_VERIFY_ONCHAIN=false)"
                chat = (
                    f"**payment_confirmed** — Testnet settlement complete "
                    f"(tx `{msg.transaction_id}`)."
                )
            else:  # INCONCLUSIVE, accepted on ASI:One's own verification
                note = "inconclusive (testnet RPC unreachable) — accepted on ASI:One verification"
                chat = (
                    f"**payment_confirmed** — Settlement complete (tx "
                    f"`{msg.transaction_id}`); on-chain re-verification was "
                    f"inconclusive (testnet RPC unreachable)."
                )
            ctx.logger.info(
                f"[payment] Verification {note} for tx={msg.transaction_id} "
                f"-> sending CompletePayment."
            )
            await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
            await _narrate_payment(ctx, msg.reference, chat)
            await _finalize_from_payment(ctx, msg.reference, msg.transaction_id)
        else:
            reason = (
                "on-chain tx not found or unsuccessful"
                if result == VerifyResult.NOT_FOUND
                else "on-chain verification could not be completed (strict mode, RPC unreachable)"
            )
            ctx.logger.warning(
                f"[payment] Verification {result.value} for tx={msg.transaction_id} "
                f"-> sending CancelPayment ({reason})."
            )
            await ctx.send(sender, CancelPayment(
                transaction_id=msg.transaction_id,
                reason=reason,
            ))
            await _narrate_payment(
                ctx,
                msg.reference,
                f"**payment_failed** — {reason} (tx `{msg.transaction_id}`).",
            )
            await _fail_from_payment(ctx, msg.reference, reason)

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
    recipient: Optional[str] = None,
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
    # Explicit payee (e.g. a supplier wallet for external orders) overrides our
    # own wallet; otherwise bill to OUR FET wallet (the trade facilitation case).
    recipient = recipient or resolve_recipient_wallet(ctx)

    # ASI:One's chat UI builds the in-chat "Approve FET Payment" card from
    # RequestPayment.metadata — specifically metadata["provider_agent_wallet"]
    # (the fetch1... wallet where the FET should land) and metadata["fet_network"]
    # (which chain). If metadata is omitted (None), ASI:One cannot construct the
    # card and rejects the inbound payment message at ingestion, surfacing
    # "Failed to process payment response by agent. Please try sending a message
    # again." — BEFORE the user can approve anything. So we ALWAYS send these
    # keys, mirroring the canonical fetchai/innovation-lab-examples `fet-example`.
    # All values must be plain strings (metadata is dict[str, str | dict[str,str]]).
    # Testnet signals the ASI:One wallet card reads to charge TestFET (Dorado /
    # stable-testnet) rather than mainnet FET. The official fet-example sets
    # mainnet="false" + fet_network="stable-testnet" (driven by FET_USE_TESTNET=true
    # in its .env); we hardcode the testnet values because Baymax is testnet-only
    # (agent_base fails closed on any non-testnet network). "test"="true" mirrors the
    # Fetch.ai team's guidance to flag the payment as TestFET (harmless superset —
    # the documented card keys are mainnet/fet_network; extra string keys are ignored).
    metadata = {
        "provider_agent_wallet": recipient,
        "fet_network": "stable-testnet",
        "mainnet": "false",
        "test": "true",
        "content": (
            description
            or "Approve to finalize the Baymax inter-facility transfer settlement."
        ),
    }

    # Defensive: the card/on-chain transfer needs a real fetch1... wallet. An
    # agent1... recipient means register_recipient_wallet(agent) was not called at
    # construction (see resolve_recipient_wallet) — ASI:One would reject it. On the
    # live path run_front.py wires this, so this should never fire.
    if not recipient.startswith("fetch1"):
        ctx.logger.error(
            f"[payment] RequestPayment recipient {recipient!r} is NOT a fetch1... "
            f"wallet — register_recipient_wallet(agent) was not wired at "
            f"construction. ASI:One will reject this payment. "
            f"See run_front.py / settlement.register_recipient_wallet."
        )

    req = RequestPayment(
        accepted_funds=[funds],
        recipient=recipient,
        deadline_seconds=PAYMENT_DEADLINE_S,
        reference=reference,
        description=description,
        metadata=metadata,
    )
    ctx.logger.info(
        f"[payment] Sending RequestPayment to {user_address}: "
        f"{funds.amount} {funds.currency} -> {req.recipient} "
        f"(network={metadata['fet_network']}, ref={reference}, "
        f"deadline={PAYMENT_DEADLINE_S}s)"
    )
    await ctx.send(user_address, req)
    return req


# ---------------------------------------------------------------------------
# Pricing.
# ---------------------------------------------------------------------------

def _amount_for_total(total_units: int) -> str:
    """FET to charge for a settlement.

    Per-unit when BAYMAX_PAYMENT_PER_UNIT_FET is set (amount = per_unit *
    units, minimum one unit), otherwise the flat BAYMAX_PAYMENT_AMOUNT_FET.
    Read at call time so deployments/tests can change pricing without re-import.
    """
    per_unit = os.getenv("BAYMAX_PAYMENT_PER_UNIT_FET", "").strip()
    if per_unit:
        try:
            return str(round(float(per_unit) * max(int(total_units), 1), 6))
        except (ValueError, TypeError):
            pass
    return os.getenv("BAYMAX_PAYMENT_AMOUNT_FET", PAYMENT_AMOUNT_FET)


# ---------------------------------------------------------------------------
# Integrator entry point — called from baymax_agents.settle_transfer.
# ---------------------------------------------------------------------------

async def settle_via_payment_protocol(
    ctx: Context,
    req_id: str,
    plan,
    user_address: str,
    reply_to: Optional[str] = None,
) -> str:
    """Kick off settlement for a completed negotiation via the Payment Protocol.

    The integrator wires this into baymax_agents.settle_transfer. It computes
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
        f"Baymax inter-facility transfer settlement {req_id}: "
        f"{n} leg(s), {total} unit(s) total."
    )
    await request_payment(
        ctx,
        user_address=user_address,
        amount=amount,
        reference=reference,
        description=description,
    )
    chat = reply_to or user_address
    if chat:
        _PAYMENT_PENDING[reference] = {"reply_to": chat, "req_id": req_id}
    ctx.logger.info(
        f"[payment] settle_via_payment_protocol: requested {amount} "
        f"FET for {req_id} from {user_address}; reference={reference}. "
        f"Awaiting CommitPayment."
    )
    return reference


async def settle_order_via_payment_protocol(
    ctx: Context,
    req_id: str,
    order,
    user_address: str,
    reply_to: Optional[str] = None,
) -> str:
    """Settle an external-supplier ORDER via the Payment Protocol.

    Sends a RequestPayment to the chat admin for a SYMBOLIC FET amount
    representing the purchase (we cannot pay a vendor in crypto at checkout). The
    payee is BAYMAX_SUPPLIER_WALLET when set (a distinct 'supplier' fetch1...
    wallet), otherwise OUR FET wallet — and the description says so explicitly.
    The FET amount uses the same per-unit/flat pricing as trades (the vendor's
    USD total is shown in the narration, not converted). Reference is
    `order-<req_id>` so it never collides with a trade's `pay-<req_id>`.
    """
    reference = f"order-{req_id}"
    qty = int(getattr(order, "quantity", 0) or 0)
    amount = _amount_for_total(qty)
    supplier_wallet = os.getenv("BAYMAX_SUPPLIER_WALLET", "").strip() or None
    payee_note = "to the supplier wallet" if supplier_wallet else (
        "(symbolic FET settlement representing the external purchase)"
    )
    description = (
        f"Baymax external supplier order {req_id}: {qty} {getattr(order, 'item', '')} "
        f"from {getattr(order, 'vendor', 'supplier')} "
        f"(vendor quote {getattr(order, 'total_price', '?')} {getattr(order, 'currency', 'USD')}) "
        f"— {payee_note}."
    )
    await request_payment(
        ctx,
        user_address=user_address,
        amount=amount,
        reference=reference,
        description=description,
        recipient=supplier_wallet,
    )
    chat = reply_to or user_address
    if chat:
        _PAYMENT_PENDING[reference] = {
            "reply_to": chat, "req_id": req_id, "kind": "order",
        }
    ctx.logger.info(
        f"[payment] settle_order_via_payment_protocol: requested {amount} FET for "
        f"order {req_id} from {user_address}; payee="
        f"{supplier_wallet or 'OUR wallet (symbolic)'}; reference={reference}."
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
    "settle_order_via_payment_protocol",
]
