"""two_agent_payment_spike.py — STREAM PAY: self-contained Payment Protocol spike.

Proves the full SELLER-side handshake end to end IN ISOLATION (one process, two
agents, no ASI:One, no real wallet) so we can verify the role decision and the
Request -> Commit -> Complete message flow before wiring it into the negotiation.

    SERVICE agent  (role="seller", our production logic from settlement.py)
        └─ on startup: sends RequestPayment to the buyer
        └─ on CommitPayment: verifies (or skips) then sends CompletePayment
                             / CancelPayment
    BUYER agent    (role="buyer", a STAND-IN for the ASI:One user wallet)
        └─ on RequestPayment: replies CommitPayment with a placeholder tx id
        └─ on CompletePayment / CancelPayment: logs the terminal result, exits

Ports >= 8100 so this never collides with the negotiation Bureau (8001-8003).

PAYMENT_VERIFY_ONCHAIN (default "true"):
    "false"  => SPIKE MODE. settlement.verify_payment_onchain() SKIPS the cosmpy
                chain query and trusts the placeholder commit, so the spike can
                demonstrate Request -> Commit -> Complete with no on-chain tx.
                THIS IS DEV/SPIKE ONLY — it is NOT a real settlement guarantee.
    "true"   => the placeholder tx id will (correctly) fail verification on the
                real testnet and the service will send CancelPayment. Useful to
                see the failure path; for the green-path demo, run with false.

Run:
    PAYMENT_VERIFY_ONCHAIN=false ./.venv/bin/python two_agent_payment_spike.py

The process self-terminates (os._exit) once the buyer sees a terminal message.
"""

from __future__ import annotations

import os

# Import agent_base FIRST — installs the Python 3.14 event-loop workaround before
# any Agent is constructed (contract rule).
import agent_base  # noqa: F401
from agent_base import FET_NETWORK

from uagents import Agent, Bureau, Context, Protocol

from protocol import (
    RequestPayment,
    CommitPayment,
    CompletePayment,
    CancelPayment,
    Funds,
    payment_protocol_spec,
)

# Our production seller logic (the thing being spiked).
from settlement import (
    PAYMENT_ROLE,
    build_payment_protocol,
    make_funds,
    register_recipient_wallet,
    request_payment,
)

# A placeholder tx id standing in for what a real signed FET transfer would
# return. On the real testnet this hash will NOT exist, so it only "verifies"
# when PAYMENT_VERIFY_ONCHAIN=false (spike mode).
SPIKE_TX_ID = os.getenv("SPIKE_TX_ID", "0xSPIKEPLACEHOLDERTX0000000000000000000000000000000000000000000000")

# Distinct ports / seeds so the spike is fully independent of the negotiation.
SERVICE_PORT = 8100
BUYER_PORT = 8101


# ---------------------------------------------------------------------------
# SERVICE agent — uses the real production seller protocol from settlement.py.
# ---------------------------------------------------------------------------
service = Agent(
    name="baymax_pay_service",
    port=SERVICE_PORT,
    seed="baymax-spike-service-seed",
    network=FET_NETWORK,
)

service_payment = build_payment_protocol()  # role="seller", on_commit/on_reject
service.include(service_payment, publish_manifest=True)

# Record the service's FET wallet (fetch1...) so RequestPayment.recipient can
# name it from inside the startup handler (ctx.agent has no .wallet there).
SERVICE_WALLET = register_recipient_wallet(service)


# ---------------------------------------------------------------------------
# BUYER agent — STAND-IN for the ASI:One user wallet (role="buyer").
# In production this side is ASI:One: the user clicks "Approve FET Payment" and
# their wallet signs + broadcasts, then ASI:One emits the CommitPayment. Here we
# simulate that by auto-committing a placeholder tx.
# ---------------------------------------------------------------------------
buyer = Agent(
    name="baymax_pay_buyer",
    port=BUYER_PORT,
    seed="baymax-spike-buyer-seed",
    network=FET_NETWORK,
)

buyer_payment = Protocol(spec=payment_protocol_spec, role="buyer")


@buyer_payment.on_message(RequestPayment)
async def buyer_on_request(ctx: Context, sender: str, msg: RequestPayment):
    """Simulate the user approving + signing: reply with CommitPayment."""
    funds = msg.accepted_funds[0] if msg.accepted_funds else make_funds()
    ctx.logger.info(
        f"[buyer] RequestPayment from {sender}: pay {funds.amount} {funds.currency} "
        f"-> {msg.recipient} (ref={msg.reference}). Simulating user approval."
    )
    await ctx.send(sender, CommitPayment(
        funds=funds,
        recipient=msg.recipient,
        transaction_id=SPIKE_TX_ID,
        reference=msg.reference,
        description=msg.description,
    ))
    ctx.logger.info(f"[buyer] Sent CommitPayment tx={SPIKE_TX_ID}.")


@buyer_payment.on_message(CompletePayment)
async def buyer_on_complete(ctx: Context, sender: str, msg: CompletePayment):
    """Service confirmed the payment — happy path. Spike is done."""
    ctx.logger.info(
        f"[buyer] CompletePayment from {sender} tx={msg.transaction_id}. "
        f"SPIKE SUCCESS: Request -> Commit -> Complete verified. Exiting."
    )
    os._exit(0)


@buyer_payment.on_message(CancelPayment)
async def buyer_on_cancel(ctx: Context, sender: str, msg: CancelPayment):
    """Service rejected the payment — failure path (expected with onchain=true)."""
    ctx.logger.info(
        f"[buyer] CancelPayment from {sender} tx={msg.transaction_id} "
        f"reason={msg.reason!r}. SPIKE took the CANCEL path. Exiting."
    )
    os._exit(0)


buyer.include(buyer_payment, publish_manifest=True)


# ---------------------------------------------------------------------------
# Kick off: the service requests payment from the buyer on startup.
# ---------------------------------------------------------------------------
@service.on_event("startup")
async def _kickoff(ctx: Context):
    verify = os.getenv("PAYMENT_VERIFY_ONCHAIN", "true")
    ctx.logger.info(
        f"=== Payment spike: role={PAYMENT_ROLE!r}, network={FET_NETWORK}, "
        f"PAYMENT_VERIFY_ONCHAIN={verify} ==="
    )
    await request_payment(
        ctx,
        user_address=buyer.address,
        amount=None,  # uses BAYMAX_PAYMENT_AMOUNT_FET (default 0.1)
        reference="spike-req-1",
        description="Baymax payment-protocol spike settlement.",
    )


# Safety net: if no terminal message arrives, don't hang a CI run forever.
@buyer.on_event("startup")
async def _watchdog(ctx: Context):
    ctx.logger.info("[buyer] Ready, awaiting RequestPayment.")


@service.on_interval(period=15.0)
async def _service_watchdog(ctx: Context):
    ctx.logger.warning(
        "[service] 15s elapsed with no terminal payment message — exiting "
        "(watchdog) so the spike never hangs."
    )
    os._exit(2)


bureau = Bureau()
bureau.add(service)
bureau.add(buyer)


if __name__ == "__main__":
    bureau.run()
