"""
Smoke test: confirm the Fetch track is wired to the Redis track.

Run from the repo root:
    python -m fetch.scripts.redis_smoke_test

It (1) pings Redis, (2) writes a weather slice and an illness slice into
forecast:{region}, and (3) reads the key back to prove both signals coexist —
i.e. the upsert merge works and we are connected.
"""

from fetch.shared import redis_io

REGION = "san_francisco"


def main():
    ok = redis_io.ping_redis()
    print(f"[1] Redis ping: {'OK' if ok else 'FAILED'}")
    if not ok:
        raise SystemExit("Redis not reachable — is it running? Check REDIS_URL.")

    redis_io.upsert_forecast_items(REGION, {
        "weather_temperature_c": 18.9,
        "weather_code": 0,
    })
    redis_io.upsert_forecast_items(REGION, {
        "illness_influenza": "High",
        "illness_covid": "Moderate",
    })

    fc = redis_io.get_forecast(REGION)
    print(f"[2] forecast:{REGION} round-trip:")
    print(f"    {fc}")

    items = (fc or {}).get("items", {})
    assert items.get("weather_temperature_c") == 18.9, "weather slice missing"
    assert items.get("illness_influenza") == "High", "illness slice missing"
    print("[3] Both weather + illness signals coexist in forecast — wiring works ✅")


if __name__ == "__main__":
    main()
