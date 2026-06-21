"""End-to-end Redis demo runner for the Redis track.

Run from inside tracks/redis with:
    python src/demo_run.py
"""

import json

from redis_client import ping_redis
from seed_demo_data import (
    seed_forecast,
    seed_hospital_meta,
    seed_inventory,
    seed_surplus,
)
from inventory import get_inventory
from forecast import get_forecast
from alerts import detect_shortfall_and_publish
from transfers import get_recent_transfers, log_transfer
from vector_history import find_similar_periods, seed_history


def section(title):
    """Print a clean section header."""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main():
    section("1. Ping Redis")
    if not ping_redis():
        print("Redis is not connected. Start Redis first.")
        return
    print("Redis connected successfully")

    section("2. Seed demo data")
    seed_hospital_meta()
    seed_inventory()
    seed_surplus()
    seed_forecast()

    section("3. hospital_a inventory")
    inventory = get_inventory("hospital_a")
    print(json.dumps(inventory, indent=2))

    section("4. san_francisco forecast")
    forecast = get_forecast("san_francisco")
    print(json.dumps(forecast, indent=2))

    section("5. Detect shortfalls for hospital_a")
    detect_shortfall_and_publish("hospital_a", "san_francisco")

    section("6. Log transfer: hospital_b -> hospital_a")
    log_transfer(
        {
            "item": "IV Fluids",
            "quantity": 150,
            "from_hospital": "hospital_b",
            "to_hospital": "hospital_a",
            "eta_minutes": 18,
            "status": "confirmed",
            "settlement_status": "pending_payment_protocol",
        }
    )

    section("7. Recent transfers")
    for transfer in get_recent_transfers():
        print(json.dumps(transfer, indent=2))

    section("8. Vector history: similar past periods")
    try:
        seed_history()
        find_similar_periods(
            "cold front respiratory spike IV fluids demand increase", top_k=3
        )
    except Exception:
        print("Vector history skipped for demo reliability.")


if __name__ == "__main__":
    main()
