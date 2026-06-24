"""wave5_crisis_e2e_check.py — offline end-to-end check for the CRISIS flow.

Drives the full realignment path with NO network / key / Redis:

    chat crisis intent ("wildfires near Hospital A")
      -> on_intent kind="crisis"
      -> interfaces.research_crisis (mock) infers crisis type + at-risk supplies
      -> start_crisis picks the top at-risk item that is short (saline)
      -> the EXISTING negotiation chain (offers -> rank -> approve -> settle)
      -> CONFIRMED

Builds a 4-agent in-process Bureau (FRONT + surplus B/C + a collector standing
in for the ASI:One chat user). The collector auto-approves at the gate, records
every narrated milestone, and on the terminal CONFIRMED asserts that the crisis
research actually ran and an at-risk item was acted on. Self-asserts + self-exits
(mirrors wave2/wave4_e2e_check.py).

Run:  python wave5_crisis_e2e_check.py     (prints WAVE5 CRISIS E2E SUCCESS, exit 0)
"""

import os

# Deterministic + fast: short offer window; we control exit via assertions (not
# BAYMAX_EXIT_WHEN_DONE) so we can validate the narration before terminating.
os.environ.setdefault("BAYMAX_OFFER_TIMEOUT", "3")
os.environ["BAYMAX_EXIT_WHEN_DONE"] = "0"

# agent_base FIRST (installs the 3.14 event loop before any Agent is built).
import agent_base  # noqa: F401
from agent_base import build_chat_protocol, build_hospital_agent, create_text_chat
from uagents import Agent, Bureau, Context

from baymax_agents import attach_front_handlers, attach_hospital_handlers
from front_agent import on_intent

CRISIS = os.getenv("BAYMAX_CRISIS_INTENT", "wildfires near Hospital A")
_WATCHDOG_S = float(os.getenv("BAYMAX_WAVE5_TIMEOUT", "60"))

narrated: list[str] = []
_approved = {"sent": False}
_ticks = {"n": 0}


def _finish(ok: bool, msg: str):
    import sys
    print(("WAVE5 CRISIS E2E SUCCESS: " if ok else "WAVE5 CRISIS E2E FAILURE: ") + msg)
    sys.stdout.flush()  # os._exit skips buffer flush — force the verdict out first
    sys.stderr.flush()
    os._exit(0 if ok else 1)


# --- The three negotiation agents (Bureau / in-process) --------------------
front = build_hospital_agent("Hospital A")
hospital_b = build_hospital_agent("Hospital B")
hospital_c = build_hospital_agent("Hospital C")
attach_front_handlers(front)
attach_hospital_handlers(hospital_b, "Hospital B")
attach_hospital_handlers(hospital_c, "Hospital C")
front.include(build_chat_protocol(on_intent), publish_manifest=True)

# --- Collector: stands in for the ASI:One chat user ------------------------
collector = Agent(name="baymax_wave5_collector", seed="baymax-wave5-collector-seed",
                  port=8011, network=os.getenv("FETCH_NETWORK", "testnet"))


async def _collect(ctx: Context, sender: str, text: str) -> None:
    narrated.append(text)
    ctx.logger.info(f"[NARRATION #{len(narrated)}] {text}")
    low = text.lower()

    # Auto-approve the inter-facility trade at the gate so the flow can settle.
    if "awaiting_approval" in low and not _approved["sent"]:
        _approved["sent"] = True
        ctx.logger.info("[WAVE5] admin decision -> 'approve'")
        await ctx.send(sender, create_text_chat("approve"))
        return

    if "**failed**" in low:
        _finish(False, f"flow reached FAILED: {text!r}")

    if "**confirmed**" in low or "transfer confirmed" in low:
        joined = "\n".join(narrated).lower()
        checks = {
            "research milestone narrated": "**researching**" in joined,
            "crisis type classified": "crisis type:" in joined,
            "at-risk item selected": "acting on" in joined,
            "negotiation confirmed": "transfer confirmed" in joined,
        }
        missing = [k for k, ok in checks.items() if not ok]
        if missing:
            _finish(False, "missing expected milestones: " + "; ".join(missing))
        _finish(True, f"crisis {CRISIS!r} -> research -> negotiate -> CONFIRMED "
                      f"({len(narrated)} milestones)")


collector.include(build_chat_protocol(_collect), publish_manifest=True)
collector_addr = collector.address


@front.on_event("startup")
async def _feed(ctx: Context):
    ctx.logger.info(f"=== WAVE5: feeding crisis intent {CRISIS!r} "
                    f"(reply_to=collector {collector_addr}) ===")
    await on_intent(ctx, collector_addr, CRISIS)


@collector.on_interval(period=1.0)
async def _watchdog(ctx: Context):
    _ticks["n"] += 1
    if _ticks["n"] >= _WATCHDOG_S:
        _finish(False, f"timed out after {_WATCHDOG_S:.0f}s without CONFIRMED "
                       f"({len(narrated)} milestones seen)")


if __name__ == "__main__":
    bureau = Bureau()
    bureau.add(front)
    bureau.add(hospital_b)
    bureau.add(hospital_c)
    bureau.add(collector)
    bureau.run()
