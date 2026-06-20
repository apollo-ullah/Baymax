"""Redis connection helpers for the stockpile track."""

import os

import redis
from dotenv import load_dotenv

load_dotenv()

DEFAULT_REDIS_URL = "redis://localhost:6379"


def get_redis() -> redis.Redis:
    """Return a Redis client built from REDIS_URL (with string decoding)."""
    redis_url = os.getenv("REDIS_URL", DEFAULT_REDIS_URL)
    return redis.Redis.from_url(redis_url, decode_responses=True)


def ping_redis() -> bool:
    """Return True if Redis responds to PING, False otherwise."""
    try:
        client = get_redis()
        client.ping()
        return True
    except redis.ConnectionError as exc:
        print(f"[redis] Could not connect to Redis: {exc}")
        return False
    except redis.RedisError as exc:
        print(f"[redis] Redis error during ping: {exc}")
        return False
