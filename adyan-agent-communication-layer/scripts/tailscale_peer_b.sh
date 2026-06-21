#!/usr/bin/env bash
# MacBook B — camera worker pointing at MacBook A's Redis over Tailscale.
#
# Prereqs:
#   tailscale up
#   tailscale set --hostname=baymax-b
#   Same .env tailscale vars as MacBook A (BAYMAX_USE_TAILSCALE, TAILSCALE_*)
#
#   ./scripts/tailscale_peer_b.sh
#   ./scripts/tailscale_peer_b.sh smoke

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export BAYMAX_USE_TAILSCALE="${BAYMAX_USE_TAILSCALE:-1}"
eval "$(./.venv/bin/python tailscale_hosts.py --role peer-b --shell)"

if [[ "${1:-}" == "smoke" ]]; then
  exec ./.venv/bin/python tailscale_smoke_test.py --role peer-b
fi

exec ./.venv/bin/python camera_worker.py --hospital b "$@"
