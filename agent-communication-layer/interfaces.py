"""interfaces.py — the two stubbed seams, with deterministic working mocks.

The agent network has exactly two external dependencies, and both are owned by
other workstreams. Wave 0 ships mocks so the network is NEVER blocked waiting on
them. Each function's signature and return type ARE the contract; the real
implementations must match them exactly.

    get_inventory(hospital, item) -> InventoryState   # Redis seam   (Workstream C)
    rank_offers(need, offers)      -> RankedPlan        # Claude seam  (Workstream B)

These functions intentionally do NOT import uagents: the seams are plain Python
so the inventory/intelligence owners need not touch the agent framework. The
agent layer (agent_base.py / baymax_agents.py) adapts between these plain
types and the protocol.py wire models.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

_log = logging.getLogger("baymax.interfaces")


# ---------------------------------------------------------------------------
# Seam data types (plain dataclasses — no uagents dependency)
# ---------------------------------------------------------------------------

@dataclass
class InventoryState:
    """A single facility's live stock of one item. Mirrors the Redis schema
    `hospital:{id}:inventory  item -> {qty, pct, status, updated_at}` plus the
    facility meta (lat/lng, capacity, safety_threshold) needed to reason about
    transfers. Returned by get_inventory()."""

    hospital: str
    item: str
    qty: int
    capacity: int
    safety_threshold: int
    lat: float
    lng: float
    updated_at: str = "2026-06-20T00:00:00Z"  # mock timestamp (Redis sets real)
    present: bool = True                       # False => item unknown at facility

    @property
    def pct(self) -> float:
        """Fill level 0..1 relative to capacity."""
        return 0.0 if self.capacity <= 0 else round(self.qty / self.capacity, 4)

    @property
    def spare_capacity(self) -> int:
        """How much this facility could give away while staying at/above its
        own safety threshold."""
        return max(0, self.qty - self.safety_threshold)

    @property
    def shortfall(self) -> int:
        """How far below the safety threshold this facility currently sits."""
        return max(0, self.safety_threshold - self.qty)

    @property
    def status(self) -> str:
        if self.qty < self.safety_threshold:
            return "critical" if self.qty < self.safety_threshold * 0.5 else "low"
        return "ok"


@dataclass
class SupplyNeed:
    """The requester's need — the first argument to rank_offers()."""

    item: str
    quantity_needed: int
    requester: str
    urgency: str = "urgent"            # routine | urgent | critical
    requester_lat: Optional[float] = None
    requester_lng: Optional[float] = None
    needed_by: Optional[str] = None    # ISO-8601, or None for ASAP


@dataclass
class OfferView:
    """A surplus facility's offer as seen by the ranker — the second argument
    to rank_offers(). The agent layer builds these from SupplyOffer wire
    messages so the ranker stays decoupled from uagents."""

    offerer: str
    quantity_available: int
    distance_km: float
    eta_minutes: int
    expiry: Optional[str] = None       # ISO date of the offered stock


@dataclass
class Allocation:
    """One leg of a resolution: take `quantity` from `offerer`."""

    offerer: str
    quantity: int
    distance_km: float
    eta_minutes: int
    expiry: Optional[str] = None


@dataclass
class RankedPlan:
    """rank_offers() output: the chosen split plus a human-readable rationale
    for the chat narration."""

    allocations: List[Allocation] = field(default_factory=list)
    total_covered: int = 0
    shortfall_remaining: int = 0
    fully_covered: bool = False
    rationale: str = ""


@dataclass
class SupplierOrder:
    """An external-supplier purchase order. Returned by order_from_supplier().

    For the mock + Browserbase 'prepared' flow this represents a cart-review /
    prepared order (we do NOT pay the vendor in crypto; settlement is a separate
    FET tx). total_price/currency are the vendor's quote (display only); the FET
    charge is derived from quantity by the settlement layer."""

    item: str
    quantity: int
    vendor: str
    unit_price: Optional[float] = None
    total_price: Optional[float] = None
    currency: str = "USD"
    confirmation_ref: str = ""
    live_view_url: Optional[str] = None   # Browserbase session/screenshot artifact
    status: str = "prepared"              # prepared | confirmed | failed


# ---------------------------------------------------------------------------
# Mock inventory (Redis seam — Workstream C replaces with real Redis reads)
# ---------------------------------------------------------------------------
# Coordinates chosen so Hospital B (~Palo Alto) is clearly nearer to the
# requester (Hospital A, ~SF) than Hospital C (~Sacramento). This produces the
# canonical demo scenario from the PRD: A is short 200 IV fluids, B can spare
# 150 (near), C can spare 80 (far, nearer-expiry stock) -> a split resolution.

_FACILITY_META = {
    "Hospital A": {"lat": 37.7560, "lng": -122.4050, "capacity_default": 480},
    "Hospital B": {"lat": 37.4419, "lng": -122.1430, "capacity_default": 500},
    "Hospital C": {"lat": 38.5816, "lng": -121.4944, "capacity_default": 400},
}

# (hospital, item) -> (qty, capacity, safety_threshold)
_MOCK_INVENTORY = {
    # --- IV fluids: the SPLIT scenario (no single facility covers A's 200) ---
    ("Hospital A", "IV fluids"): (40, 480, 240),    # need = 200 (critical)
    ("Hospital B", "IV fluids"): (350, 400, 200),   # spare = 150 (near)
    ("Hospital C", "IV fluids"): (180, 300, 100),   # spare = 80  (far)

    # --- Saline: the FULL-COVER scenario (B alone covers A's need) ----------
    ("Hospital A", "saline"): (20, 240, 120),       # need = 100
    ("Hospital B", "saline"): (400, 500, 150),      # spare = 250 (covers alone)
    ("Hospital C", "saline"): (160, 300, 100),      # spare = 60

    # --- Sutures: the NO-OFFER scenario (nobody has spare) ------------------
    ("Hospital A", "sutures"): (10, 60, 60),        # need = 50
    ("Hospital B", "sutures"): (40, 80, 60),        # spare = 0  -> declines
    ("Hospital C", "sutures"): (30, 70, 50),        # spare = 0  -> declines
}

# Nearest-expiry of each facility's stock (drives the expiry constraint). The
# canonical demo: C's IV-fluid stock expires soonest, so a smart ranker (Claude,
# Workstream B) might trade ETA vs expiry; the Wave 0 mock keeps it nearest-first.
_MOCK_EXPIRY = {
    ("Hospital B", "IV fluids"): "2026-12-01",
    ("Hospital C", "IV fluids"): "2026-07-05",   # nearer expiry
    ("Hospital B", "saline"): "2026-11-15",
    ("Hospital C", "saline"): "2026-09-10",
}


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two lat/lng points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


# ---------------------------------------------------------------------------
# SEAM 1 — inventory state (Redis, Workstream C)
# ---------------------------------------------------------------------------

def get_inventory(hospital: str, item: str) -> InventoryState:
    """Return the live inventory of `item` at `hospital`.

    Workstream C is now LIVE: when BAYMAX_REDIS=1, this reads the teammates'
    Redis (via redis_inventory.py) and returns an InventoryState. On ANY failure
    — Redis unreachable, missing `redis` lib, hospital unknown to Redis — it
    falls back to the deterministic mock below, so the seam NEVER hangs (FR1) and
    the offline harnesses keep working with no Redis.
    """
    import logging

    import redis_inventory  # lazy: avoids import cycle + keeps redis optional

    if redis_inventory.redis_enabled():
        try:
            inv = redis_inventory.redis_get_inventory(hospital, item)
            logging.getLogger("baymax.inventory").info(
                "[inventory] backend=redis %s/%s qty=%s spare=%s safety=%s present=%s",
                hospital, item, inv.qty, inv.spare_capacity,
                inv.safety_threshold, inv.present,
            )
            return inv
        except Exception as exc:  # noqa: BLE001 — fail-closed to the mock
            logging.getLogger("baymax.inventory").warning(
                "[inventory] backend=redis FAILED for %s/%s (%s) -> mock fallback",
                hospital, item, exc,
            )
    return _mock_get_inventory(hospital, item)


def _mock_get_inventory(hospital: str, item: str) -> InventoryState:
    """Deterministic mock inventory (the original Wave-0 seam). Unknown
    (hospital, item) returns a not-present InventoryState rather than raising,
    so callers can treat a missing item as 'no stock / no spare'."""
    meta = _FACILITY_META.get(hospital, {"lat": 0.0, "lng": 0.0, "capacity_default": 0})
    key = (hospital, item)
    if key not in _MOCK_INVENTORY:
        return InventoryState(
            hospital=hospital, item=item, qty=0,
            capacity=meta["capacity_default"], safety_threshold=0,
            lat=meta["lat"], lng=meta["lng"], present=False,
        )
    qty, capacity, safety = _MOCK_INVENTORY[key]
    return InventoryState(
        hospital=hospital, item=item, qty=qty, capacity=capacity,
        safety_threshold=safety, lat=meta["lat"], lng=meta["lng"], present=True,
    )


def distance_between(a: str, b: str) -> float:
    """Helper: km between two facilities. In Redis mode (BAYMAX_REDIS=1) the
    coordinates come from Redis meta so distance matches the live inventory;
    otherwise (and on any Redis failure) it uses the mock coordinates. Used by
    the agent layer to populate SupplyOffer.distance_km."""
    import redis_inventory  # lazy

    if redis_inventory.redis_enabled():
        try:
            ma = redis_inventory.redis_facility_meta(a)
            mb = redis_inventory.redis_facility_meta(b)
            if ma and mb:
                return _haversine_km(ma["lat"], ma["lng"], mb["lat"], mb["lng"])
        except Exception:  # noqa: BLE001 — fall back to mock coordinates
            pass
    ma = _FACILITY_META.get(a)
    mb = _FACILITY_META.get(b)
    if not ma or not mb:
        return 0.0
    return _haversine_km(ma["lat"], ma["lng"], mb["lat"], mb["lng"])


def eta_minutes_for(distance_km: float, avg_speed_kmh: float = 60.0) -> int:
    """Helper: rough drive ETA in minutes for a distance, ground-transport."""
    if distance_km <= 0:
        return 0
    return int(round(distance_km / avg_speed_kmh * 60))


def expiry_for(hospital: str, item: str) -> Optional[str]:
    """Helper: nearest-expiry ISO date of a facility's stock, if known."""
    return _MOCK_EXPIRY.get((hospital, item))


# ---------------------------------------------------------------------------
# SEAM 2 — offer ranking (Claude, Workstream B)
# ---------------------------------------------------------------------------

def rank_offers(need: SupplyNeed, offers: List[OfferView]) -> RankedPlan:
    """Choose which offers to take and how much from each, to cover `need`.

    CONTRACT (real impl, Workstream B): Claude reasons over all constraints
    (quantity, distance/ETA, expiry, urgency) and returns the same RankedPlan
    shape with a natural-language rationale. Signature is frozen here.

    CLAUDE: when BAYMAX_CLAUDE_RANKING is truthy, delegate to
    claude_ranking.rank_offers_via_claude() (lazy import), which asks Claude to
    reason over all constraints and returns the SAME RankedPlan shape with an
    intelligent natural-language rationale. On ANY failure (missing key, API
    error, malformed output, lib absent) we fall back to the mock below —
    fail-closed, so the negotiation never hangs and offline harnesses need no
    network. Default (no env) = mock.

    MOCK: a deterministic nearest-first greedy allocator. Sorts offers by
    distance (ties broken by larger quantity), then takes from each in turn
    until the need is met. This deterministically produces the canonical split
    (150 from the near facility + 50 from the far one) for the demo scenario,
    and degrades cleanly to full-cover (one allocation) and no-offer (empty).

    NOTE: Claude API is NOT called here. To enable Claude reasoning, replace the
    greedy logic below with an anthropic.Anthropic().messages.create() call and
    parse the structured JSON response into a RankedPlan.
    """
    import logging

    import claude_ranking  # lazy: keeps the anthropic SDK optional

    if claude_ranking.claude_ranking_enabled():
        try:
            plan = claude_ranking.rank_offers_via_claude(need, offers)
            logging.getLogger("baymax.ranking").info(
                "[ranking] backend=claude need=%s/%s offers=%s -> covered=%s legs=%s",
                need.quantity_needed, need.item, len(offers),
                plan.total_covered, len(plan.allocations),
            )
            return plan
        except Exception as exc:  # noqa: BLE001 — fail-closed to the mock
            logging.getLogger("baymax.ranking").warning(
                "[ranking] backend=claude FAILED for %s/%s (%s) -> mock fallback",
                need.quantity_needed, need.item, exc,
            )

    _log.info(
        "[CLAUDE_REASONING] action=rank_offers_start backend=mock_greedy "
        "item=%s quantity_needed=%d requester=%s offer_count=%d "
        "offers=%s",
        need.item, need.quantity_needed, need.requester, len(offers),
        [(o.offerer, o.quantity_available, o.distance_km) for o in offers],
    )
    usable = [o for o in offers if o.quantity_available > 0]
    usable.sort(key=lambda o: (o.distance_km, -o.quantity_available))

    remaining = max(0, need.quantity_needed)
    allocations: List[Allocation] = []
    for o in usable:
        if remaining <= 0:
            break
        take = min(remaining, o.quantity_available)
        if take <= 0:
            continue
        allocations.append(Allocation(
            offerer=o.offerer, quantity=take,
            distance_km=o.distance_km, eta_minutes=o.eta_minutes, expiry=o.expiry,
        ))
        remaining -= take

    total_covered = sum(a.quantity for a in allocations)
    fully_covered = remaining <= 0 and total_covered >= need.quantity_needed

    if not allocations:
        rationale = (
            f"No facility has spare {need.item}. Need {need.quantity_needed} "
            f"for {need.requester} remains unmet."
        )
    else:
        legs = ", ".join(
            f"{a.quantity} from {a.offerer} ({a.distance_km:.0f} km / ~{a.eta_minutes} min)"
            for a in allocations
        )
        if fully_covered:
            cover = f"covers the full need ({total_covered}/{need.quantity_needed})"
        else:
            cover = (
                f"covers {total_covered}/{need.quantity_needed}; "
                f"{remaining} still short"
            )
        split_note = "Single-facility transfer" if len(allocations) == 1 else \
            f"Split across {len(allocations)} facilities"
        rationale = f"{split_note}, nearest-first: {legs}. This {cover}."

    plan = RankedPlan(
        allocations=allocations,
        total_covered=total_covered,
        shortfall_remaining=max(0, remaining),
        fully_covered=fully_covered,
        rationale=rationale,
    )
    _log.info(
        "[CLAUDE_REASONING] action=rank_offers_complete backend=mock_greedy "
        "item=%s total_covered=%d shortfall_remaining=%d fully_covered=%s "
        "allocations=%s rationale=%r",
        need.item, plan.total_covered, plan.shortfall_remaining, plan.fully_covered,
        [(a.offerer, a.quantity) for a in plan.allocations],
        plan.rationale,
    )
    return plan


# ---------------------------------------------------------------------------
# SEAM 3 — external supplier order (Browserbase, Wave 3). Mirrors get_inventory:
# delegates to supplier_order.py when BAYMAX_BROWSERBASE is on, else a
# deterministic mock; fail-closed to the mock on ANY error so offline harnesses
# never need Browserbase/keys.
# ---------------------------------------------------------------------------

# item -> (vendor, unit price USD). Deterministic so the demo + tests are stable.
_MOCK_VENDORS = {
    "IV fluids": ("MedSupply Direct", 12.50),
    "saline": ("MedSupply Direct", 3.20),
    "sutures": ("SurgiSupply Co", 8.75),
}


def _mock_order_from_supplier(item: str, quantity: int, *, hospital: str) -> SupplierOrder:
    vendor, unit = _MOCK_VENDORS.get(item, ("Generic Medical Supplier", 10.0))
    qty = max(int(quantity), 1)
    total = round(unit * qty, 2)
    # Deterministic, human-readable PO ref (no hash() — that is per-process random).
    ref = f"MOCK-PO-{hospital.split()[-1]}-{item.replace(' ', '')[:4].upper()}-{qty}"
    return SupplierOrder(
        item=item, quantity=qty, vendor=vendor, unit_price=unit,
        total_price=total, currency="USD", confirmation_ref=ref,
        live_view_url=None, status="prepared",
    )


def order_from_supplier(item: str, quantity: int, *, hospital: str) -> SupplierOrder:
    """Place (prepare) an external-supplier order for `quantity` of `item`.

    BAYMAX_BROWSERBASE=1 -> drive a real vendor site via supplier_order.py
    (Stagehand/Playwright over Browserbase). On ANY failure (missing lib, no key,
    anti-bot, timeout) fall back to the deterministic mock. This is a SYNC
    function: callers in async handlers invoke it via asyncio.to_thread()."""
    import logging

    try:
        import supplier_order  # lazy: keeps Browserbase/playwright optional

        if supplier_order.browserbase_enabled():
            o = supplier_order.browserbase_order(item, quantity, hospital=hospital)
            logging.getLogger("baymax.order").info(
                "[order] backend=browserbase %s x%s vendor=%s total=%s ref=%s",
                item, quantity, o.vendor, o.total_price, o.confirmation_ref,
            )
            return o
    except Exception as exc:  # noqa: BLE001 — fail-closed to the mock (missing lib too)
        logging.getLogger("baymax.order").warning(
            "[order] backend=browserbase FAILED for %s x%s (%s) -> mock fallback",
            item, quantity, exc,
        )
    return _mock_order_from_supplier(item, quantity, hospital=hospital)


# ---------------------------------------------------------------------------
# SEAM 4 — per-facility admin confirmation (Wave 3 hybrid HITL). The surplus
# facility's admin confirms releasing stock before a TransferAccept. Default:
# auto-approve + log a notification ("each agent tied to an admin"). The reserved
# BAYMAX_REQUIRE_FACILITY_APPROVAL flag denies (fail-closed) until a real,
# non-blocking deferred-approval channel is built — it must NOT block the
# on_proposal event-loop handler.
# ---------------------------------------------------------------------------

def approve_release(facility: str, item: str, qty: int) -> bool:
    import logging
    import os

    require = os.getenv("BAYMAX_REQUIRE_FACILITY_APPROVAL", "").strip().lower() in (
        "1", "true", "yes",
    )
    logging.getLogger("baymax.facility").info(
        "[facility-admin] %s: release %s %s -> %s",
        facility, qty, item,
        "REQUIRES APPROVAL (reserved gate: denying until channel exists)"
        if require else "auto-approved + notified",
    )
    return not require


# ---------------------------------------------------------------------------
# SEAM 5 — crisis research (Claude, the realignment's new front-of-funnel).
#
# The demo's two surfaces now start from a stated CRISIS ("wildfires near
# Hospital A"), not a hand-named item. research_crisis() infers the crisis type
# and a RANKED list of at-risk supplies (constrained to the items the inventory
# layer knows), so the agent layer can pick a top at-risk item and drive the
# EXISTING negotiation chain. Mirrors get_inventory / rank_offers / order:
# delegates to claude_research.py when BAYMAX_CLAUDE_RESEARCH=1, else a
# deterministic keyword mock; fail-closed to the mock on ANY error so the
# offline harnesses need no key and the funnel never hangs.
# ---------------------------------------------------------------------------

# Canonical items the inventory layer (and the negotiation) actually know about.
# Kept here so the mock + the Claude backend can both clamp to them.
KNOWN_ITEMS = ("IV fluids", "saline", "sutures")


@dataclass
class AtRiskSupply:
    """One supply the research step flags as at risk for a crisis type.
    `item` is a canonical KNOWN_ITEMS string so it can feed get_inventory /
    start_negotiation directly."""

    item: str
    risk: str = "elevated"   # high | elevated | moderate
    rationale: str = ""


@dataclass
class CrisisBrief:
    """research_crisis() output: the inferred crisis type plus a ranked
    at-risk-supply list (highest risk first) and a chat-ready rationale."""

    crisis_text: str
    crisis_type: str = "unknown"          # wildfire|heatwave|flu_surge|earthquake|storm|unknown
    region: str = "san_francisco"
    at_risk: List[AtRiskSupply] = field(default_factory=list)
    rationale: str = ""
    present: bool = True                  # False => could not classify at all

    @property
    def top_item(self) -> Optional[str]:
        """The highest-risk at-risk item, or None if the brief is empty."""
        return self.at_risk[0].item if self.at_risk else None


# crisis-type keyword profiles → ranked at-risk supplies (mock). First match
# wins; every profile returns at least one KNOWN_ITEMS entry so the downstream
# negotiation always has a valid item, offline, with no key.
_CRISIS_PROFILES: List[tuple] = [
    (("wildfire", "fire", "smoke", "burn", "blaze"), "wildfire", [
        AtRiskSupply("saline", "high", "Burn irrigation + wound flushing spike with fire/burn casualties."),
        AtRiskSupply("IV fluids", "high", "Smoke-inhalation + burn-shock resuscitation drives IV demand."),
        AtRiskSupply("sutures", "elevated", "Lacerations and surgical debridement from trauma."),
    ]),
    (("heatwave", "heat wave", "heatstroke", "heat stroke", "extreme heat"), "heatwave", [
        AtRiskSupply("IV fluids", "high", "Heatstroke + dehydration cases need aggressive rehydration."),
        AtRiskSupply("saline", "elevated", "Volume resuscitation for severe dehydration."),
    ]),
    (("flu", "influenza", "respiratory", "covid", "outbreak", "surge", "pandemic", "rsv"), "flu_surge", [
        AtRiskSupply("IV fluids", "high", "Respiratory-illness surge inflates inpatient fluid use."),
        AtRiskSupply("saline", "elevated", "Hydration + medication dilution for admitted patients."),
    ]),
    (("earthquake", "quake", "collapse", "crash", "mass casualty", "trauma", "explosion"), "earthquake", [
        AtRiskSupply("sutures", "high", "Mass-casualty lacerations + surgical repair."),
        AtRiskSupply("IV fluids", "high", "Trauma/hemorrhage resuscitation."),
        AtRiskSupply("saline", "elevated", "Wound irrigation + volume support."),
    ]),
    (("flood", "storm", "hurricane", "typhoon", "tornado"), "storm", [
        AtRiskSupply("IV fluids", "elevated", "Displacement + injury caseload raises fluid demand."),
        AtRiskSupply("saline", "elevated", "Wound care for storm injuries."),
        AtRiskSupply("sutures", "moderate", "Lacerations from debris."),
    ]),
]

# Default when no keyword matches — still actionable (the canonical demo item).
_CRISIS_DEFAULT = ("unknown", [
    AtRiskSupply("IV fluids", "elevated", "General surge contingency: IV fluids are the first consumable to run short."),
    AtRiskSupply("saline", "moderate", "Broadly used across most acute presentations."),
])


def _mock_research_crisis(crisis_text: str, region: str) -> CrisisBrief:
    text = (crisis_text or "").lower()
    for keywords, ctype, at_risk in _CRISIS_PROFILES:
        if any(kw in text for kw in keywords):
            top = at_risk[0]
            return CrisisBrief(
                crisis_text=crisis_text, crisis_type=ctype, region=region,
                at_risk=list(at_risk),
                rationale=(
                    f"Crisis classified as '{ctype}'. Highest supply risk: {top.item} "
                    f"({top.risk}) — {top.rationale}"
                ),
            )
    ctype, at_risk = _CRISIS_DEFAULT
    top = at_risk[0]
    return CrisisBrief(
        crisis_text=crisis_text, crisis_type=ctype, region=region,
        at_risk=list(at_risk),
        rationale=(
            f"Could not match a specific crisis type; treating as a general surge. "
            f"Prioritising {top.item} ({top.risk})."
        ),
    )


def research_crisis(crisis_text: str, region: str = "san_francisco") -> CrisisBrief:
    """Infer the crisis type + ranked at-risk supplies from a natural-language
    crisis statement.

    BAYMAX_CLAUDE_RESEARCH=1 -> delegate to claude_research.research_crisis_via_claude
    (Claude, structured tool-use), clamped to KNOWN_ITEMS. On ANY failure
    (no key, API error, malformed output, lib absent) fall back to the
    deterministic keyword mock below. Default (no env) = mock, so offline
    harnesses need no network. SYNC: async callers wrap with asyncio.to_thread().
    """
    import logging

    import claude_research  # lazy: keeps the anthropic SDK optional

    if claude_research.claude_research_enabled():
        try:
            brief = claude_research.research_crisis_via_claude(crisis_text, region)
            logging.getLogger("baymax.research").info(
                "[research] backend=claude type=%s top=%s at_risk=%s",
                brief.crisis_type, brief.top_item, [a.item for a in brief.at_risk],
            )
            return brief
        except Exception as exc:  # noqa: BLE001 — fail-closed to the mock
            logging.getLogger("baymax.research").warning(
                "[research] backend=claude FAILED (%s) -> mock fallback", exc,
            )
    brief = _mock_research_crisis(crisis_text, region)
    logging.getLogger("baymax.research").info(
        "[research] backend=mock type=%s top=%s at_risk=%s",
        brief.crisis_type, brief.top_item, [a.item for a in brief.at_risk],
    )
    return brief


# ---------------------------------------------------------------------------
# SEAM 6 — forecast ingest (the "Ingest Data" loop). Runs the WHO + weather +
# illness (+ CDC stub) orchestrator and returns Claude's proactive supply-risk
# recommendation. Mirrors the other seams: delegates to ingest_orchestrator.py
# (which bridges to fetch/agents/who_agent) when BAYMAX_INGEST=1, else a
# deterministic mock; fail-closed to the mock so the offline harnesses never
# pull the fetch/Redis stack.
# ---------------------------------------------------------------------------

@dataclass
class ForecastRecommendation:
    """ingest_forecast() output — the proactive recommendation surfaced after an
    ingest run. Same shape as the Redis `reasoning:latest` key the dashboard
    already renders."""

    risk_level: str = "low"               # low | medium | high | critical
    priority_items: List[str] = field(default_factory=list)
    reasoning: str = ""
    recommended_action: str = ""
    region: str = "san_francisco"
    updated_at: str = ""


def _mock_ingest_forecast(region: str) -> ForecastRecommendation:
    return ForecastRecommendation(
        risk_level="medium",
        priority_items=["IV fluids", "saline"],
        reasoning=(
            "Mock ingest (no live feeds): seasonal illness + warm-weather signals "
            "suggest elevated fluid demand over the next 7 days."
        ),
        recommended_action=(
            "Pre-position IV fluids toward Hospital A; review surplus at B/C before a shortfall forms."
        ),
        region=region,
    )


def ingest_forecast(region: str = "san_francisco") -> ForecastRecommendation:
    """Run the forecast ingest loop (WHO + weather + illness + CDC stub → Redis →
    Claude reasoning) and return the proactive recommendation.

    BAYMAX_INGEST=1 -> delegate to ingest_orchestrator.run_ingest (which calls the
    real WHO fetcher). On ANY failure fall back to the deterministic mock. SYNC:
    async callers wrap with asyncio.to_thread()."""
    import logging

    try:
        import ingest_orchestrator  # lazy: keeps the fetch/Redis stack optional

        if ingest_orchestrator.ingest_enabled():
            rec = ingest_orchestrator.run_ingest(region)
            logging.getLogger("baymax.ingest").info(
                "[ingest] backend=live region=%s risk=%s priority=%s",
                region, rec.risk_level, rec.priority_items,
            )
            return rec
    except Exception as exc:  # noqa: BLE001 — fail-closed to the mock (missing stack too)
        logging.getLogger("baymax.ingest").warning(
            "[ingest] backend=live FAILED for region=%s (%s) -> mock fallback", region, exc,
        )
    return _mock_ingest_forecast(region)
