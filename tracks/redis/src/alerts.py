"""Alert detection and publishing for inventory shortfalls."""

import json
from datetime import datetime, timezone

from redis_client import get_redis
from inventory import get_inventory
from forecast import get_forecast
from schema import ALERTS_CHANNEL, ALERTS_STREAM


def detect_shortfall_and_publish(hospital_id, region):
    """Detect low-stock / demand-driven shortfalls and publish alerts.

    Compares a hospital's inventory against the regional forecast and raises:
      - compound_alert (severity critical): low stock AND rising demand
      - low_stock_alert (severity warning): low stock only
    """
    client = get_redis()
    inventory = get_inventory(hospital_id)
    forecast = get_forecast(region)
    forecast_items = forecast.get("items", {}) if forecast else {}

    alerts = []

    for item, record in inventory.items():
        pct = record.get("pct", 0)
        status = record.get("status")
        low_stock = pct < 25 or status == "low"

        demand = forecast_items.get(item, {})
        predicted_increase = demand.get("predicted_demand_increase_pct", 0)
        demand_rising = predicted_increase >= 30

        if low_stock and demand_rising:
            alert = {
                "type": "compound_alert",
                "severity": "critical",
                "hospital_id": hospital_id,
                "region": region,
                "item": item,
                "pct": pct,
                "status": status,
                "predicted_demand_increase_pct": predicted_increase,
                "reason": demand.get("reason"),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        elif low_stock:
            alert = {
                "type": "low_stock_alert",
                "severity": "warning",
                "hospital_id": hospital_id,
                "region": region,
                "item": item,
                "pct": pct,
                "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            continue

        payload = json.dumps(alert)
        client.publish(ALERTS_CHANNEL, payload)
        client.xadd(ALERTS_STREAM, {"alert": payload})
        alerts.append(alert)

        print(
            f"[{alert['severity'].upper()}] {alert['type']} "
            f"{hospital_id}/{item}: pct={pct} status={status} "
            f"demand_increase={alert.get('predicted_demand_increase_pct', 0)}%"
        )

    if not alerts:
        print(f"No shortfalls detected for {hospital_id} in {region}.")

    return alerts
