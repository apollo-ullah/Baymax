#!/usr/bin/env bash
# Single-MacBook live demo — real Claude Vision + full negotiation on the dashboard.
#
# Prerequisites:
#   - OrbStack/Docker running
#   - ANTHROPIC_API_KEY in adyan-agent-communication-layer/.env
#   - Mac camera permission for Terminal/Python
#
# Usage:
#   ./scripts/single_mac_demo.sh           # print instructions + free ports
#   ./scripts/single_mac_demo.sh agents    # terminal 1: all 3 agents (Bureau)
#   ./scripts/single_mac_demo.sh worker-a  # terminal 2: Hospital A camera
#   ./scripts/single_mac_demo.sh worker-b  # terminal 3: Hospital B camera (port 8766)
#   ./scripts/single_mac_demo.sh dashboard # terminal 4: scan dashboard
#   ./scripts/single_mac_demo.sh redis     # start Redis only

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export BAYMAX_REDIS="${BAYMAX_REDIS:-1}"
export BAYMAX_ITEM="${BAYMAX_ITEM:-Saline}"
# Desk demo: water bottles / props count as Saline inventory (see vision_count.py)
export BAYMAX_VISION_DEMO="${BAYMAX_VISION_DEMO:-1}"
export BAYMAX_DEMO_CAPACITY="${BAYMAX_DEMO_CAPACITY:-3}"
# Two-bottle desk demo: A needs 3 on hand (vision sees 2 → shortfall 1),
# B keeps 1 safe (vision sees 2 → spare 1) → agents trade 1 bottle.
export BAYMAX_DEMO_TARGET="${BAYMAX_DEMO_TARGET:-3}"
export BAYMAX_DEMO_RESERVE="${BAYMAX_DEMO_RESERVE:-1}"

CMD="${1:-}"

case "$CMD" in
  redis)
    docker compose -f ../tracks/redis/docker-compose.redis.yml up -d
    echo "Redis on :6379  RedisInsight on :8081"
    ;;
  agents)
    exec ./.venv/bin/python run_dashboard_demo.py
    ;;
  worker-a)
    exec ./.venv/bin/python camera_worker.py --hospital a --item "$BAYMAX_ITEM" \
      --capacity "$BAYMAX_DEMO_CAPACITY" --reserve "$BAYMAX_DEMO_TARGET" --demo
    ;;
  worker-b)
    exec ./.venv/bin/python camera_worker.py --hospital b --port 8766 --item "$BAYMAX_ITEM" \
      --capacity "$BAYMAX_DEMO_CAPACITY" --reserve "$BAYMAX_DEMO_RESERVE" --demo
    ;;
  dashboard)
    exec ./.venv/bin/python scan_dashboard.py
    ;;
  free)
    exec ./scripts/free_demo_ports.sh
    ;;
  ""|help)
    ./scripts/free_demo_ports.sh
    echo ""
    echo "=== Baymax single-Mac demo (4 terminals) ==="
    echo ""
    echo "Terminal 1:  ./scripts/single_mac_demo.sh redis      # once"
    echo "Terminal 1:  ./scripts/single_mac_demo.sh agents     # keep running"
    echo "Terminal 2:  ./scripts/single_mac_demo.sh worker-a   # real camera + Vision"
    echo "Terminal 3:  ./scripts/single_mac_demo.sh worker-b   # 2nd shelf or same cam"
    echo "Terminal 4:  ./scripts/single_mac_demo.sh dashboard"
    echo ""
    echo "Open http://localhost:8080 → Scan & Negotiate → Approve at the gate"
    echo ""
    echo "Two-bottle demo (default thresholds):"
    echo "  Hospital A  target=${BAYMAX_DEMO_TARGET} on hand  → vision counts 2 → need 1"
    echo "  Hospital B  reserve=${BAYMAX_DEMO_RESERVE} safe   → vision counts 2 → spare 1"
    echo "  Expected: agents negotiate a transfer of 1 ${BAYMAX_ITEM} from B → A"
    echo ""
    echo "IMPORTANT: use run_dashboard_demo.py (agents), NOT run_front.py alone."
    echo "B/C must be in-process; run_front cannot collect their offers by itself."
    ;;
  *)
    echo "Usage: $0 {redis|agents|worker-a|worker-b|dashboard|free|help}" >&2
    exit 1
    ;;
esac
