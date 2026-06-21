"""claude_ranking_check.py — self-contained verification of the offer-ranking
seam (Workstream B), both the deterministic mock and the live Claude backend.

Bureau-free on purpose: it calls the seam functions directly and constructs no
uagents Agent/Bureau, so it never binds a network port (port 8000 is occupied by
a live demo). Run:

    ./.venv/bin/python claude_ranking_check.py            # mock + (Claude if key)

It always exercises the mock path (canonical IV-fluids split + no-offer). If
ANTHROPIC_API_KEY is present it also runs the live Claude path on a few scenarios
and asserts the validated invariants hold. Prints PASS/FAIL lines and exits 0/1.
"""

from __future__ import annotations

import os
import sys
import traceback

from dotenv import load_dotenv

load_dotenv()  # pull ANTHROPIC_API_KEY (+ any seeds) from .env, like the runners

from interfaces import OfferView, RankedPlan, SupplyNeed  # noqa: E402

_failures: list[str] = []


def _check(cond: bool, label: str) -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}")
        _failures.append(label)


def _assert_invariants(plan: RankedPlan, need: SupplyNeed, offers: list[OfferView], tag: str) -> None:
    """Assert the structural invariants that must hold for ANY valid plan,
    regardless of which backend produced it."""
    by_offerer = {o.offerer: o for o in offers}

    # Allocations only reference real offerers, never over-allocate.
    refs_ok = all(a.offerer in by_offerer for a in plan.allocations)
    _check(refs_ok, f"[{tag}] allocations reference only real offerers")

    no_over = all(
        a.quantity <= by_offerer[a.offerer].quantity_available
        for a in plan.allocations if a.offerer in by_offerer
    )
    _check(no_over, f"[{tag}] no leg over-allocates its offerer")

    pos = all(a.quantity > 0 for a in plan.allocations)
    _check(pos, f"[{tag}] every leg quantity is positive")

    # total_covered is the sum of legs and never exceeds the need.
    computed = sum(a.quantity for a in plan.allocations)
    _check(plan.total_covered == computed, f"[{tag}] total_covered == sum(legs)")
    _check(plan.total_covered <= need.quantity_needed, f"[{tag}] covered <= need")

    # shortfall + fully_covered are internally consistent with total_covered.
    exp_short = max(0, need.quantity_needed - plan.total_covered)
    _check(plan.shortfall_remaining == exp_short, f"[{tag}] shortfall_remaining consistent")
    _check(
        plan.fully_covered == (plan.total_covered >= need.quantity_needed),
        f"[{tag}] fully_covered consistent",
    )

    _check(bool(plan.rationale and plan.rationale.strip()), f"[{tag}] rationale is non-empty")


# --------------------------------------------------------------------------
# Scenarios (realistic SupplyNeed / OfferView inputs we build ourselves)
# --------------------------------------------------------------------------

def _iv_fluids_split() -> tuple[SupplyNeed, list[OfferView]]:
    # Canonical demo: A short 200; B (near) spares 150, C (far, sooner expiry) 80.
    need = SupplyNeed(item="IV fluids", quantity_needed=200, requester="Hospital A",
                      urgency="critical")
    offers = [
        OfferView("Hospital B", 150, 38.0, 38, "2026-12-01"),
        OfferView("Hospital C", 80, 130.0, 130, "2026-07-05"),
    ]
    return need, offers


def _saline_full_cover() -> tuple[SupplyNeed, list[OfferView]]:
    # B alone can cover the whole need.
    need = SupplyNeed(item="saline", quantity_needed=100, requester="Hospital A",
                      urgency="urgent")
    offers = [
        OfferView("Hospital B", 250, 38.0, 38, "2026-11-15"),
        OfferView("Hospital C", 60, 130.0, 130, "2026-09-10"),
    ]
    return need, offers


def _no_offer() -> tuple[SupplyNeed, list[OfferView]]:
    need = SupplyNeed(item="sutures", quantity_needed=50, requester="Hospital A",
                      urgency="urgent")
    return need, []  # nobody has spare


# --------------------------------------------------------------------------
# Mock path — always runs, no network. Asserts canonical behaviour.
# --------------------------------------------------------------------------

def run_mock_checks() -> None:
    print("--- MOCK path (BAYMAX_CLAUDE_RANKING unset) ---")
    os.environ.pop("BAYMAX_CLAUDE_RANKING", None)
    # Reimport-safe: rank_offers reads the env each call, no module reload needed.
    from interfaces import rank_offers

    # 1) Canonical IV-fluids split: 150 from B + 50 from C, fully covered.
    need, offers = _iv_fluids_split()
    plan = rank_offers(need, offers)
    _assert_invariants(plan, need, offers, "mock/iv-split")
    alloc = {a.offerer: a.quantity for a in plan.allocations}
    _check(alloc.get("Hospital B") == 150 and alloc.get("Hospital C") == 50,
           "[mock/iv-split] canonical 150(B)+50(C) split")
    _check(plan.total_covered == 200 and plan.fully_covered,
           "[mock/iv-split] fully covers 200")

    # 2) No-offer scenario: empty plan, full shortfall, not covered.
    need, offers = _no_offer()
    plan = rank_offers(need, offers)
    _assert_invariants(plan, need, offers, "mock/no-offer")
    _check(plan.allocations == [] and plan.total_covered == 0
           and plan.shortfall_remaining == 50 and not plan.fully_covered,
           "[mock/no-offer] empty plan, 50 short, not covered")


# --------------------------------------------------------------------------
# Claude path — only if a key is present. Asserts validated invariants.
# --------------------------------------------------------------------------

def run_claude_checks() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("--- CLAUDE path SKIPPED (no ANTHROPIC_API_KEY) ---")
        return

    print("--- CLAUDE path (live) ---")
    import claude_ranking

    scenarios = [
        ("claude/iv-split", *_iv_fluids_split()),
        ("claude/saline-full", *_saline_full_cover()),
        ("claude/no-offer", *_no_offer()),
    ]
    for tag, need, offers in scenarios:
        try:
            plan = claude_ranking.rank_offers_via_claude(need, offers)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  [{tag}] live call raised: {exc}")
            traceback.print_exc()
            _failures.append(f"[{tag}] live call raised")
            continue
        _assert_invariants(plan, need, offers, tag)
        # No-offer must yield an empty plan even from Claude.
        if not offers:
            _check(plan.allocations == [] and plan.total_covered == 0,
                   f"[{tag}] Claude returns empty plan when no offers")
        print(f"      [{tag}] rationale: {plan.rationale}")

    # Also exercise the full delegation path through interfaces.rank_offers with
    # the env flag set, to prove the wiring + fail-closed branch are sound.
    os.environ["BAYMAX_CLAUDE_RANKING"] = "1"
    try:
        from interfaces import rank_offers
        need, offers = _iv_fluids_split()
        plan = rank_offers(need, offers)
        _assert_invariants(plan, need, offers, "claude/via-interfaces")
    finally:
        os.environ.pop("BAYMAX_CLAUDE_RANKING", None)


def main() -> int:
    run_mock_checks()
    run_claude_checks()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} check(s) failed)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS (all checks passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
