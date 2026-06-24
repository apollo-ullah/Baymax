"""run_claude_demo.py — live entrypoint for the Claude multi-agent engine.

Replaces the old uAgents `run_dashboard_demo.py`. There is NO Bureau and NO
uAgents here — just a plain asyncio loop that bridges the frozen Redis seam to
the Claude orchestrator (`claude_negotiation.py`):

    * IN  — LPOP `baymax:crisis` / `baymax:trigger` / `baymax:decision`
            (dashboard_bus.pop_*), driven by the Flask bridge (ui/app.py) and
            the Next.js web UI.
    * OUT — every milestone is published to `baymax:narration`
            (dashboard_bus.publish_narration, registered as the narration sink).

One negotiation runs at a time (a crisis/trigger is only picked up while the
engine is idle); decisions and the 45s auto-approve watchdog run every tick so a
halted negotiation always resolves.

A tiny health server binds BAYMAX_HEALTH_PORT (default 8000) purely so the
existing readiness checks keep working unchanged: start_demo.sh's
`wait_port 8000` and ui/app.py's `_free_bureau_port()` both key on :8000.

Run (with Redis up + seeded):
    BAYMAX_REDIS=1 REDIS_URL=redis://localhost:6379 python run_claude_demo.py
The web demo launches this via ./start_demo.sh.
"""
from __future__ import annotations

import asyncio
import logging
import os

# Default the Claude paths ON so the real research/ranking/per-facility reasoning
# fires under the live demo. setdefault => an explicit env (or start_demo.sh) wins.
os.environ.setdefault("BAYMAX_REDIS", "1")
os.environ.setdefault("BAYMAX_CLAUDE_RESEARCH", "1")
os.environ.setdefault("BAYMAX_CLAUDE_RANKING", "1")
os.environ.setdefault("BAYMAX_HOSPITAL_LLM", "1")

import claude_negotiation as cn  # noqa: E402
import dashboard_bus  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("baymax.run")

HEALTH_PORT = int(os.getenv("BAYMAX_HEALTH_PORT")
                  or os.getenv("BAYMAX_DASHBOARD_PORT")  # start_demo passes 8079; bureau used 8000
                  or "8000")
POLL_INTERVAL_S = float(os.getenv("BAYMAX_POLL_INTERVAL", "0.5"))


async def _start_health_server() -> None:
    """Bind a no-op TCP server so `wait_port`/`_free_bureau_port` see :PORT bound.
    Fail-soft: if the port is taken, log and continue (the loop still works)."""
    async def _handle(reader, writer):
        try:
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            writer.close()

    try:
        server = await asyncio.start_server(_handle, "0.0.0.0", HEALTH_PORT)
        log.info("health server listening on :%s", HEALTH_PORT)
        return server
    except Exception as exc:  # noqa: BLE001 — readiness probe only; never fatal
        log.warning("health server could not bind :%s (%s) — continuing", HEALTH_PORT, exc)
        return None


async def _process_decision() -> None:
    """Route one queued admin decision to the matching halted negotiation."""
    dec = dashboard_bus.pop_decision()
    if not dec:
        return
    decision = dec.get("decision")
    if decision not in ("approve", "order", "reject"):
        return
    req_id = dec.get("req_id") or cn.find_awaiting_approval()
    if not req_id:
        return
    log.info("decision: %s -> %s", req_id, decision)
    await cn.resume_after_admin_decision(req_id, decision)


async def _process_new_work() -> None:
    """Pick up a crisis (preferred) or a trigger — only while idle, so the demo
    runs one legible negotiation at a time."""
    if cn.has_active_negotiation():
        return

    crisis = dashboard_bus.pop_crisis()
    if crisis:
        crisis_text = (crisis.get("crisis_text") or "").strip()
        if crisis_text:
            requester = crisis.get("requester") or cn.REQUESTER
            region = crisis.get("region") or "san_francisco"
            log.info("crisis -> start_crisis(%r)", crisis_text)
            await cn.start_crisis(crisis_text, requester=requester, region=region,
                                  source="dashboard")
        return

    trig = dashboard_bus.pop_trigger()
    if trig:
        item = trig.get("item") or os.getenv("BAYMAX_ITEM", "Saline")
        requester = trig.get("requester") or cn.REQUESTER
        qty = trig.get("quantity")
        need = int(qty) if qty is not None else None
        log.info("trigger -> start_negotiation(%r, need=%s)", item, need)
        await cn.start_negotiation(item, requester=requester, need=need, source="dashboard")


async def main() -> None:
    cn.register_narration_sink(dashboard_bus.publish_narration)

    # Optional observability: register the Arize/Phoenix emitter (opt-in via
    # BAYMAX_ARIZE; fail-open). One-way hook — the core never imports arize.
    try:
        import arize_hook  # type: ignore
        if arize_hook.arize_enabled():
            cn.register_trace_hook(arize_hook.emit_trace)
            log.info("arize trace hook registered")
    except Exception:  # noqa: BLE001
        pass

    await _start_health_server()

    print("=" * 70)
    print("Baymax LIVE — Claude multi-agent negotiation engine (no uAgents)")
    print(f"  REDIS_URL          : {os.getenv('REDIS_URL') or 'redis://localhost:6379'}")
    print(f"  BAYMAX_REDIS       : {os.getenv('BAYMAX_REDIS')}")
    print(f"  BAYMAX_CLAUDE_*    : research={os.getenv('BAYMAX_CLAUDE_RESEARCH')} "
          f"ranking={os.getenv('BAYMAX_CLAUDE_RANKING')} hospital_llm={os.getenv('BAYMAX_HOSPITAL_LLM')}")
    print(f"  health port        : {HEALTH_PORT}")
    print(f"  surplus facilities : {', '.join(cn._surplus_facilities(cn.REQUESTER))}")
    print("-" * 70)
    print("Listening on baymax:crisis / baymax:trigger / baymax:decision …")
    print("=" * 70)

    while True:
        try:
            await _process_decision()
            await cn.check_approval_timeouts()
            await _process_new_work()
        except Exception as exc:  # noqa: BLE001 — one bad tick must never kill the loop
            log.warning("run loop tick error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nshutting down.")
