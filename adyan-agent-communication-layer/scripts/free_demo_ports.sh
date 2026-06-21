#!/usr/bin/env bash
# Free Baymax demo ports before starting run_front / run_hospital_* / scan_dashboard.
#
# Common squatters:
#   8000 — uAgents Bureau (run_dashboard_demo / baymax_agents / wave harnesses)
#   8080 — scan_dashboard UI (default; avoids collision with Bureau on 8000)
#   8001 — Hospital A (run_front.py Mailbox mode only)
#   8002 — Hospital B
#   8003 — Hospital C
#
# Usage: ./scripts/free_demo_ports.sh

set -euo pipefail

PORTS=(8000 8001 8002 8003 8080)

echo "Stopping known Baymax demo processes..."
pkill -f "hello_world_agent.py" 2>/dev/null || true
pkill -f "run_front.py" 2>/dev/null || true
pkill -f "run_hospital_b.py" 2>/dev/null || true
pkill -f "run_hospital_c.py" 2>/dev/null || true
pkill -f "front_agent.py" 2>/dev/null || true
pkill -f "scan_dashboard.py" 2>/dev/null || true
sleep 1

for port in "${PORTS[@]}"; do
  pids=$(lsof -ti:"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "Port $port in use by PID(s): $pids"
    echo "$pids" | xargs kill -9 2>/dev/null || true
  fi
done

sleep 1
echo "--- port status ---"
for port in "${PORTS[@]}"; do
  if lsof -ti:"$port" >/dev/null 2>&1; then
    echo "  $port: STILL IN USE ($(lsof -ti:"$port" | tr '\n' ' '))"
  else
    echo "  $port: free"
  fi
done
