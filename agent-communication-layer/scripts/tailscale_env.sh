#!/usr/bin/env bash
# Print shell exports for the Baymax Tailscale demo.
#
#   eval "$(./scripts/tailscale_env.sh server)"   # MacBook A
#   eval "$(./scripts/tailscale_env.sh peer-b)"   # MacBook B
#
# Requires BAYMAX_USE_TAILSCALE=1 and TAILSCALE_SERVER / TAILSCALE_PEER_B in .env
# (or exported in the shell). Loads agent-communication-layer/.env if present.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

ROLE="${1:-status}"
exec ./.venv/bin/python tailscale_hosts.py --role "$ROLE" --shell
