"""tailscale_hosts.py — resolve Baymax demo peers over Tailscale.

Public WiFi and phone hotspots often block device-to-device ports. Tailscale gives
each MacBook a stable 100.x address (or MagicDNS name) so Redis, camera workers,
and the scan dashboard can talk without relying on the LAN.

Typical layout:
  MacBook A (server): Redis, run_front, camera_worker --hospital a, scan_dashboard
  MacBook B (peer):   camera_worker --hospital b  (REDIS_URL -> A)

On BOTH machines (same tailnet), set in `.env`:
  BAYMAX_USE_TAILSCALE=1
  TAILSCALE_SERVER=baymax-a
  TAILSCALE_PEER_B=baymax-b

Hostnames come from `tailscale set --hostname=baymax-a` (and `-b` on the other
machine). Full MagicDNS names also work.

Print shell exports:
  eval "$(./.venv/bin/python tailscale_hosts.py --role server --shell)"
  eval "$(./.venv/bin/python tailscale_hosts.py --role peer-b --shell)"
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import subprocess
import sys
from typing import Any

DEFAULT_REDIS_PORT = 6379
DEFAULT_WORKER_PORT = 8765
DEFAULT_DASHBOARD_PORT = 8080


def tailscale_enabled() -> bool:
    return os.getenv("BAYMAX_USE_TAILSCALE", "").lower() in ("1", "true", "yes")


def _looks_like_ipv4(host: str) -> bool:
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _tailscale_status() -> dict[str, Any] | None:
    try:
        out = subprocess.check_output(
            ["tailscale", "status", "--json"],
            text=True,
            timeout=5,
        )
        return json.loads(out)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return None


def _match_peer(name: str, peer: dict[str, Any]) -> bool:
    needle = name.strip().lower().rstrip(".")
    if not needle:
        return False
    dns = str(peer.get("DNSName", "")).lower().rstrip(".")
    host = str(peer.get("HostName", "")).lower()
    if needle in (host, dns):
        return True
    if dns.startswith(needle + ".") or dns.split(".")[0] == needle:
        return True
    return host == needle


def _ipv4_from_peer(peer: dict[str, Any]) -> str | None:
    for addr in peer.get("TailscaleIPs") or []:
        if ":" not in str(addr):
            return str(addr)
    return None


def resolve_host(name: str) -> str:
    """Resolve a Tailscale peer to an IPv4 address."""
    name = (name or "").strip()
    if not name:
        raise ValueError("empty host name")
    if _looks_like_ipv4(name):
        return name
    status = _tailscale_status()
    if status:
        for peer in (status.get("Peer") or {}).values():
            if _match_peer(name, peer):
                ip = _ipv4_from_peer(peer)
                if ip:
                    return ip
    try:
        infos = socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except socket.gaierror:
        pass
    raise RuntimeError(
        f"cannot resolve Tailscale peer {name!r} — set TAILSCALE_* to a 100.x IP, "
        f"run `tailscale status`, or check MagicDNS / hostname"
    )


def server_name() -> str:
    return (os.getenv("TAILSCALE_SERVER") or os.getenv("BAYMAX_SERVER_HOST") or "").strip()


def peer_b_name() -> str:
    return (os.getenv("TAILSCALE_PEER_B") or os.getenv("BAYMAX_PEER_B_HOST") or "").strip()


def redis_port() -> int:
    return int(os.getenv("REDIS_PORT", str(DEFAULT_REDIS_PORT)))


def worker_port() -> int:
    return int(os.getenv("CAMERA_WORKER_PORT", str(DEFAULT_WORKER_PORT)))


def urls_for_role(role: str) -> dict[str, str]:
    """Return the env vars a role needs (values only, not exported)."""
    role = role.strip().lower()
    wp = worker_port()
    rp = redis_port()
    if role == "server":
        peer = peer_b_name()
        if not peer:
            raise RuntimeError("TAILSCALE_PEER_B is required for role=server")
        peer_ip = resolve_host(peer)
        server = server_name() or "127.0.0.1"
        server_ip = resolve_host(server) if server not in ("localhost", "127.0.0.1") else "127.0.0.1"
        return {
            "REDIS_URL": f"redis://127.0.0.1:{rp}",
            "WORKER_A_URL": f"http://127.0.0.1:{wp}",
            "WORKER_B_URL": f"http://{peer_ip}:{wp}",
            "TAILSCALE_SERVER_IP": server_ip,
            "TAILSCALE_PEER_B_IP": peer_ip,
        }
    if role in ("peer-b", "client-b", "b"):
        server = server_name()
        if not server:
            raise RuntimeError("TAILSCALE_SERVER is required for role=peer-b")
        server_ip = resolve_host(server)
        return {
            "REDIS_URL": f"redis://{server_ip}:{rp}",
            "TAILSCALE_SERVER_IP": server_ip,
        }
    if role == "status":
        server = server_name()
        peer = peer_b_name()
        out: dict[str, str] = {}
        if server:
            out["TAILSCALE_SERVER_IP"] = resolve_host(server)
        if peer:
            out["TAILSCALE_PEER_B_IP"] = resolve_host(peer)
        if server:
            out["REDIS_URL_PEER_B"] = f"redis://{out['TAILSCALE_SERVER_IP']}:{rp}"
        if peer:
            out["WORKER_B_URL"] = f"http://{out['TAILSCALE_PEER_B_IP']}:{wp}"
        return out
    raise ValueError(f"unknown role {role!r} — use server, peer-b, or status")


def apply_env(role: str, *, overwrite: bool = False) -> dict[str, str]:
    """Set os.environ for the given demo role. Returns the applied mapping."""
    if not tailscale_enabled():
        return {}
    applied = urls_for_role(role)
    for key, value in applied.items():
        if overwrite or not os.getenv(key):
            os.environ[key] = value
    return applied


def _shell_exports(mapping: dict[str, str]) -> str:
    lines = []
    for key, value in mapping.items():
        if key.endswith("_IP"):
            continue
        safe = value.replace("'", "'\\''")
        lines.append(f"export {key}='{safe}'")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Resolve Baymax Tailscale peer URLs.")
    p.add_argument(
        "--role",
        choices=["server", "peer-b", "status"],
        default="status",
        help="Demo machine role (default: status — print resolved IPs/URLs)",
    )
    p.add_argument(
        "--shell",
        action="store_true",
        help="Print export statements for eval (server / peer-b only)",
    )
    args = p.parse_args(argv)
    try:
        mapping = urls_for_role(args.role)
    except RuntimeError as e:
        print(f"tailscale_hosts: {e}", file=sys.stderr)
        return 1
    if args.shell:
        if args.role == "status":
            print("tailscale_hosts: --shell requires --role server or peer-b", file=sys.stderr)
            return 1
        print(_shell_exports(mapping))
        return 0
    for key, value in mapping.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
