"""Simple CLI to check Redis connectivity."""

from redis.src.redis_client import ping_redis


def main() -> None:
    if ping_redis():
        print("Redis connected successfully")
    else:
        print("Redis connection failed")


if __name__ == "__main__":
    main()
