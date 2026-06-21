"""tailscale_smoke_test.py — verify Tailscale connectivity for the two-camera demo.

Checks that MacBook A can reach Redis (local), MacBook B's camera worker, and that
peer B can reach A's Redis — the same paths the live dashboard uses.

Requires Tailscale on both machines and env vars set (see tailscale_hosts.py).

Run on MacBook A (server):
  BAYMAX_USE_TAILSCALE=1 TAILSCALE_SERVER=baymax-a TAILSCALE_PEER_B=baymax-b \\
    ./.venv/bin/python tailscale_smoke_test.py --role server

Run on MacBook B (after A has Redis + worker A up; B's worker should be running):
  BAYMAX_USE_TAILSCALE=1 TAILSCALE_SERVER=baymax-a TAILSCALE_PEER_B=baymax-b \\
    ./.venv/bin/python tailscale_smoke_test.py --role peer-b

Exits 0 on success, 1 on failure (self-contained harness — no pytest).
"""
from __future__ import annotations
import argparse
import sys

import httpx
import tailscale_hosts as ts


def _redis_ping(url: str) -> None:
    import redis
    client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=3.0)
    if client.ping() is not True:
        raise RuntimeError(f"redis ping failed: {url}")


def _worker_health(url: str) -> dict:
    r = httpx.get(f"{url.rstrip('/')}/health", timeout=5.0)
    r.raise_for_status()
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"worker unhealthy: {url} -> {body}")
    return body


def run_server() -> None:
    urls = ts.urls_for_role("server")
    print(f"[smoke] resolved peer B -> {urls['TAILSCALE_PEER_B_IP']}")
    print(f"[smoke] redis (local) {urls['REDIS_URL']}")
    _redis_ping(urls["REDIS_URL"])
    print("[smoke] redis ping OK")
    print(f"[smoke] worker B {urls['WORKER_B_URL']}")
    body = _worker_health(urls["WORKER_B_URL"])
    print(f"[smoke] worker B OK hospital={body.get('hospital')!r}")


def run_peer_b() -> None:
    urls = ts.urls_for_role("peer-b")
    print(f"[smoke] resolved server -> {urls['TAILSCALE_SERVER_IP']}")
    print(f"[smoke] redis (remote) {urls['REDIS_URL']}")
    _redis_ping(urls["REDIS_URL"])
    print("[smoke] remote redis ping OK")
    wp = ts.worker_port()
    local = f"http://127.0.0.1:{wp}"
    print(f"[smoke] local worker {local}")
    body = _worker_health(local)
    print(f"[smoke] local worker OK hospital={body.get('hospital')!r}")


def main() -> int:
    p = argparse.ArgumentParser(description="Tailscale connectivity smoke test.")
    p.add_argument("--role", choices=["server", "peer-b"], required=True)
    args = p.parse_args()
    if not ts.tailscale_enabled():
        print("Set BAYMAX_USE_TAILSCALE=1 and TAILSCALE_SERVER / TAILSCALE_PEER_B", file=sys.stderr)
        return 1
    try:
        if args.role == "server":
            run_server()
        else:
            run_peer_b()
    except Exception as e:
        print(f"[smoke] FAIL: {e}", file=sys.stderr)
        return 1
    print("[smoke] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
