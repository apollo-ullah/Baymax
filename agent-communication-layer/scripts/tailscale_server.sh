#!/usr/bin/env bash
# MacBook A — start the Tailscale-backed demo stack (run each block in its own terminal).
#
# Prereqs:
#   tailscale up
#   tailscale set --hostname=baymax-a
#   BAYMAX_USE_TAILSCALE=1, TAILSCALE_SERVER=baymax-a, TAILSCALE_PEER_B=baymax-b in .env
#
# Terminal 1:  ./scripts/tailscale_server.sh redis
# Terminal 2:  ./scripts/tailscale_server.sh front
# Terminal 3:  ./scripts/tailscale_server.sh worker-a
# Terminal 4:  ./scripts/tailscale_server.sh dashboard

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
eval "$(./.venv/bin/python tailscale_hosts.py --role server --shell)"

CMD="${1:-}"
case "$CMD" in
  redis)
    docker compose -f ../../redis/docker-compose.redis.yml up
    ;;
  front)
    exec ./.venv/bin/python run_front.py
    ;;
  worker-a)
    exec ./.venv/bin/python camera_worker.py --hospital a "$@"
    ;;
  dashboard)
    exec ./.venv/bin/python scan_dashboard.py
    ;;
  env)
    ./.venv/bin/python tailscale_hosts.py --role server
    ;;
  smoke)
    exec ./.venv/bin/python tailscale_smoke_test.py --role server
    ;;
  *)
    echo "Usage: $0 {redis|front|worker-a|dashboard|env|smoke}" >&2
    exit 1
    ;;
esac
