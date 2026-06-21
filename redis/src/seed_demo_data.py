"""Seed demo data into Redis."""

import json
import os
from datetime import datetime, timezone

from redis_client import get_redis
from schema import (
    forecast_key,
    inventory_key,
    meta_key,
    scenario_key,
    surplus_key,
)

HOSPITALS = {
    "hospital_a": {
        "name": "SF General",
        "region": "san_francisco",
        "lat": "37.7559",
        "lng": "-122.4048",
        "capacity": "600",
    },
    "hospital_b": {
        "name": "UCSF Mission Bay",
        "region": "san_francisco",
        "lat": "37.7679",
        "lng": "-122.3915",
        "capacity": "550",
    },
    "hospital_c": {
        "name": "Kaiser SF",
        "region": "san_francisco",
        "lat": "37.7858",
        "lng": "-122.4380",
        "capacity": "450",
    },
}


INVENTORY = {
    "hospital_a": {
        "IV Fluids": {"qty": 40, "pct": 20, "status": "low"},
        "N95 Masks": {"qty": 500, "pct": 80, "status": "ok"},
        "Saline": {"qty": 90, "pct": 45, "status": "warning"},
    },
    "hospital_b": {
        "IV Fluids": {"qty": 320, "pct": 88, "status": "ok"},
        "N95 Masks": {"qty": 300, "pct": 60, "status": "ok"},
        "Saline": {"qty": 240, "pct": 75, "status": "ok"},
    },
    "hospital_c": {
        "IV Fluids": {"qty": 210, "pct": 70, "status": "ok"},
        "N95 Masks": {"qty": 200, "pct": 50, "status": "ok"},
        "Saline": {"qty": 180, "pct": 65, "status": "ok"},
    },
}


SURPLUS = {
    "hospital_a": {
        "IV Fluids": 0,
        "N95 Masks": 120,
        "Saline": 20,
    },
    "hospital_b": {
        "IV Fluids": 150,
        "N95 Masks": 60,
        "Saline": 80,
    },
    "hospital_c": {
        "IV Fluids": 80,
        "N95 Masks": 30,
        "Saline": 40,
    },
}


def _demo_mode() -> bool:
    return os.getenv("BAYMAX_DEMO_MODE", "").lower() in ("1", "true", "yes") or \
        os.getenv("BAYMAX_VISION_DEMO", "").lower() in ("1", "true", "yes")


# Bottle-desk demo: max ~4 props per camera; negotiate 1–3 units.
DEMO_INVENTORY = {
    "hospital_a": {
        "IV Fluids": {"qty": 1, "pct": 25, "status": "low", "reserve": 3},
        "Saline": {"qty": 1, "pct": 25, "status": "low", "reserve": 3},
        "Sutures": {"qty": 0, "pct": 0, "status": "low", "reserve": 3},
        "N95 Masks": {"qty": 4, "pct": 100, "status": "ok", "reserve": 1},
    },
    "hospital_b": {
        "IV Fluids": {"qty": 3, "pct": 75, "status": "ok", "reserve": 1},
        "Saline": {"qty": 4, "pct": 100, "status": "ok", "reserve": 1},
        "Sutures": {"qty": 1, "pct": 25, "status": "low", "reserve": 1},
        "N95 Masks": {"qty": 3, "pct": 75, "status": "ok", "reserve": 1},
    },
    "hospital_c": {
        "IV Fluids": {"qty": 2, "pct": 50, "status": "warning", "reserve": 1},
        "Saline": {"qty": 2, "pct": 50, "status": "warning", "reserve": 1},
        "Sutures": {"qty": 1, "pct": 25, "status": "low", "reserve": 1},
        "N95 Masks": {"qty": 2, "pct": 50, "status": "warning", "reserve": 1},
    },
}

DEMO_SURPLUS = {
    "hospital_a": {"IV Fluids": 0, "Saline": 0, "Sutures": 0, "N95 Masks": 3},
    "hospital_b": {"IV Fluids": 2, "Saline": 3, "Sutures": 0, "N95 Masks": 2},
    "hospital_c": {"IV Fluids": 1, "Saline": 1, "Sutures": 0, "N95 Masks": 1},
}


FORECAST = {
    "region": "san_francisco",
    "items": {
        "IV Fluids": {
            "predicted_demand_increase_pct": 40,
            "reason": "Respiratory illness spike and cold front",
        },
        "N95 Masks": {
            "predicted_demand_increase_pct": 15,
            "reason": "Moderate respiratory activity",
        },
        "Saline": {
            "predicted_demand_increase_pct": 25,
            "reason": "Higher emergency department volume",
        },
        "Sutures": {
            "predicted_demand_increase_pct": 20,
            "reason": "Trauma / wound-care surge",
        },
    },
}


HEATSTROKE_SCENARIO = {
    "disease": {
        "name": "Heatstroke",
        "required_supplies": [
            "IV Fluids",
            "Saline",
            "Cooling Blankets",
            "Electrolyte Solution",
        ],
    },
    "facility_a": {
        "inventory": {},
        "inventory_status": "awaiting_live_inventory",
    },
    "facility_b": {
        "inventory": {},
        "inventory_status": "awaiting_live_inventory",
    },
    "forecast": {
        "status": "awaiting_live_data",
        "predicted_case_growth": None,
    },
    "recommendation": {
        "status": "pending_reasoning",
        "source_facility": None,
        "destination_facility": None,
        "transfer_quantity": None,
        "estimated_arrival": None,
    },
}


def seed_hospital_meta() -> None:
    """Write each hospital's metadata to a Redis hash and print it."""
    client = get_redis()
    for hospital_id, meta in HOSPITALS.items():
        key = meta_key(hospital_id)
        client.hset(key, mapping=meta)
        stored = client.hgetall(key)
        print(f"{hospital_id} -> {key}: {stored}")


def seed_inventory() -> None:
    """Write each hospital's inventory items to a Redis hash and print a summary."""
    client = get_redis()
    updated_at = datetime.now(timezone.utc).isoformat()
    table = DEMO_INVENTORY if _demo_mode() else INVENTORY
    for hospital_id, items in table.items():
        key = inventory_key(hospital_id)
        mapping = {}
        for item_name, fields in items.items():
            record = {**fields, "updated_at": updated_at}
            mapping[item_name] = json.dumps(record)
        client.hset(key, mapping=mapping)

        print(f"{hospital_id} -> {key}")
        for item_name, fields in items.items():
            print(
                f"  {item_name}: qty={fields['qty']} "
                f"pct={fields['pct']} status={fields['status']}"
            )


def seed_surplus() -> None:
    """Write each hospital's surplus counts to a Redis hash and print them."""
    client = get_redis()
    table = DEMO_SURPLUS if _demo_mode() else SURPLUS
    for hospital_id, items in table.items():
        key = surplus_key(hospital_id)
        mapping = {item_name: str(qty) for item_name, qty in items.items()}
        client.hset(key, mapping=mapping)

        print(f"{hospital_id} -> {key}")
        for item_name, qty in items.items():
            print(f"  {item_name}: {qty}")


def seed_forecast() -> None:
    """Write the regional forecast as a JSON string via Redis SET and print it."""
    client = get_redis()
    region = FORECAST["region"]
    key = forecast_key(region)
    record = {
        "region": region,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": FORECAST["items"],
    }
    client.set(key, json.dumps(record))

    print(f"{region} -> {key}")
    print(json.dumps(record, indent=2))


def seed_scenarios() -> None:
    """Store demo scenario profiles as JSON strings via Redis SET."""
    client = get_redis()
    client.set(scenario_key("heatstroke"), json.dumps(HEATSTROKE_SCENARIO))
    print("Seeded scenario: Heatstroke")


def main() -> None:
    seed_hospital_meta()
    seed_inventory()
    seed_surplus()
    seed_forecast()
    seed_scenarios()


if __name__ == "__main__":
    main()
