#!/usr/bin/env bash
# kill_all.sh — stop every Baymax/Stockpile process so a clean launch can't
# collide with a stray one. By default it leaves Redis running (it's the shared
# foundation); pass --redis to kill Redis too.
#
#   ./kill_all.sh            # kill app processes, keep Redis
#   ./kill_all.sh --redis    # also kill Redis on :6379
set -u

KILL_REDIS=0
[ "${1:-}" = "--redis" ] && KILL_REDIS=1

# App-layer process patterns (NOT redis — handled separately by port).
PATTERNS=(
  "serve_with_ngrok"
  "ngrok http 5001"
  "ui/app.py"
  "run_dashboard_demo"
  "run_front.py"
  "run_hospital_b.py"
  "run_hospital_c.py"
  "capture_single.py"
  "camera_worker.py"
  "scan_dashboard"
)

echo "=== killing app processes ==="
for pat in "${PATTERNS[@]}"; do
  pids=$(pgrep -f "$pat" | tr '\n' ' ')
  if [ -n "$pids" ]; then
    pkill -f "$pat" 2>/dev/null
    echo "  killed [$pat]: $pids"
  fi
done

if [ "$KILL_REDIS" = "1" ]; then
  rpid=$(lsof -nP -iTCP:6379 -sTCP:LISTEN -t 2>/dev/null)
  if [ -n "$rpid" ]; then kill $rpid 2>/dev/null; echo "  killed redis: $rpid"; else echo "  no redis on 6379"; fi
fi

sleep 2

echo "=== verify ==="
left=$(ps -ax -o pid,command | grep -iE "ui/app.py|run_dashboard|run_front|run_hospital|capture_single|camera_worker|scan_dashboard|ngrok http 5001" | grep -v grep)
if [ -n "$left" ]; then echo "  STILL RUNNING:"; echo "$left"; else echo "  all app processes stopped"; fi
[ "$KILL_REDIS" = "1" ] && { lsof -nP -iTCP:6379 -sTCP:LISTEN >/dev/null 2>&1 && echo "  redis STILL up" || echo "  redis stopped"; }
