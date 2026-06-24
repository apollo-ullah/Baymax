#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Baymax — one-command demo launcher for the CURRENT stack:
#
#     Next.js web (:3000) ──▶ Flask bridge ui/app.py (:5001) ──▶ Redis (:6379)
#                                                          └────▶ Claude engine (run_claude_demo.py, health :8000)
#
# Brings up Redis (seeded) + Bureau + Flask + web in the background, health-checks
# each, and prints the URLs. This replaces the old 4-terminal single_mac_demo.sh
# (which targeted the retired scan_dashboard on :8080).
#
# Usage:
#   ./start_demo.sh           # bring the whole stack up (live mode)
#   ./start_demo.sh down      # stop everything we started (leaves Redis running)
#   ./start_demo.sh status    # show port + health status
#   ./start_demo.sh mock      # PANIC SWITCH: relaunch web in scripted-mock mode
#   ./start_demo.sh live      # relaunch web back in live mode
#   ./start_demo.sh logs      # tail the three component logs
#
# Gotchas this script encodes (all bit us before — see CLAUDE.md):
#   • venv + secrets live in the SIBLING dir adyan-agent-communication-layer/.
#   • .env ships REDIS_URL= empty; getenv returns "" → Redis.from_url rejects it.
#     We export a real REDIS_URL so python-dotenv (override=False) keeps ours.
#   • The negotiation engine is run_claude_demo.py — a plain asyncio loop (no
#     uAgents). The FRONT coordinator calls the Hospital B/C Claude agents
#     directly; settlement is simulated. It binds :8000 only as a health port.
#   • Flask /api/crisis does NOT auto-start the engine, so we start it explicitly.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$ROOT/agent-communication-layer"
VENV_PY="$ROOT/adyan-agent-communication-layer/.venv/bin/python"
WEB_DIR="$ROOT/web"

export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
export BAYMAX_REDIS=1

BUREAU_LOG=/tmp/baymax_bureau.log
FLASK_LOG=/tmp/baymax_flask.log
WEB_LOG=/tmp/baymax_web.log

c_green() { printf "\033[32m%s\033[0m\n" "$1"; }
c_red()   { printf "\033[31m%s\033[0m\n" "$1"; }
c_dim()   { printf "\033[2m%s\033[0m\n" "$1"; }

port_pids() { lsof -ti:"$1" 2>/dev/null || true; }

wait_port() {  # wait_port <port> <timeout_s> <label>
  local port="$1" timeout="$2" label="$3" i=0
  while ! lsof -ti:"$port" >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -ge "$timeout" ] && { c_red "  ✗ $label never bound :$port (after ${timeout}s)"; return 1; }
    sleep 1
  done
  c_green "  ✓ $label listening on :$port"
}

wait_url() {  # wait_url <url> <timeout_s> <label>
  local url="$1" timeout="$2" label="$3" i=0
  while ! curl -fsS --max-time 3 "$url" >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -ge "$timeout" ] && { c_red "  ✗ $label not responding at $url (after ${timeout}s)"; return 1; }
    sleep 1
  done
  c_green "  ✓ $label responding at $url"
}

preflight() {
  [ -x "$VENV_PY" ] || { c_red "venv python not found at $VENV_PY"; c_dim "  create it: python -m venv adyan-agent-communication-layer/.venv && .../pip install -r agent-communication-layer/requirements.txt"; exit 1; }
  command -v docker >/dev/null || { c_red "docker not found (needed for Redis)"; exit 1; }
  command -v npm >/dev/null    || { c_red "npm not found (needed for the web UI)"; exit 1; }
}

ensure_redis() {
  echo "[1/4] Redis"
  if ! lsof -ti:6379 >/dev/null 2>&1; then
    c_dim "  starting redis-stack via docker compose…"
    docker compose -f "$ROOT/redis/docker-compose.redis.yml" up -d >/dev/null 2>&1
    wait_port 6379 20 "Redis" || exit 1
  else
    c_green "  ✓ Redis already on :6379"
  fi
  # Seed if the demo keys are missing.
  local n
  n="$("$VENV_PY" - <<'PY' 2>/dev/null || echo 0
import os, redis
try:
    r = redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=2)
    print(1 if r.exists("hospital:hospital_a:inventory") else 0)
except Exception:
    print(0)
PY
)"
  if [ "$n" != "1" ]; then
    c_dim "  seeding demo data…"
    ( cd "$ROOT/redis/src" && "$VENV_PY" seed_demo_data.py >/dev/null 2>&1 ) \
      && c_green "  ✓ seeded" || c_red "  ✗ seed failed (check redis/src/seed_demo_data.py)"
  else
    c_green "  ✓ Redis already seeded"
  fi
}

seed_scenario() {
  # Deterministic pitch scenario: Hospital A short 3 saline; B & C each spare 2,
  # so need=3 forces a fully-covered 2+1 multi-facility split (the hero moment).
  # A is set empty here; the live camera scan overwrites A's count when used.
  echo "[1b/4] Demo scenario (full-cover split)"
  "$VENV_PY" - <<'PY'
import os, json, redis
r = redis.Redis.from_url(os.environ["REDIS_URL"])
def setinv(hid, qty, reserve, pct, status):
    r.hset(f"hospital:{hid}:inventory", "Saline",
           json.dumps({"qty": qty, "pct": pct, "status": status, "reserve": reserve}))
r.hdel("hospital:hospital_a:inventory", "saline")  # drop lowercase dup to avoid ambiguity
setinv("hospital_a", 0, 3, 0.0, "low");  r.hset("hospital:hospital_a:surplus", "Saline", 0)
setinv("hospital_b", 3, 1, 75.0, "ok");  r.hset("hospital:hospital_b:surplus", "Saline", 2)
setinv("hospital_c", 3, 1, 75.0, "ok");  r.hset("hospital:hospital_c:surplus", "Saline", 2)
print("ok")
PY
  c_green "  ✓ scenario: A short 3 saline; B & C spare 2 each → 2+1 split"
}

start_bureau() {
  echo "[2/4] Claude engine (multi-agent negotiation)"
  if lsof -ti:8000 >/dev/null 2>&1; then
    c_dim "  freeing stale :8000…"; port_pids 8000 | xargs kill -9 2>/dev/null || true; sleep 1
  fi
  ( cd "$AGENT_DIR" && \
    REDIS_URL="$REDIS_URL" BAYMAX_REDIS=1 \
    BAYMAX_CLAUDE_RESEARCH=1 BAYMAX_CLAUDE_RANKING=1 BAYMAX_HOSPITAL_LLM=1 \
    nohup "$VENV_PY" run_claude_demo.py >"$BUREAU_LOG" 2>&1 & )
  wait_port 8000 40 "Claude engine" || { c_red "  see $BUREAU_LOG"; exit 1; }
}

start_flask() {
  echo "[3/4] Flask bridge"
  if lsof -ti:5001 >/dev/null 2>&1; then
    c_dim "  freeing stale :5001…"; port_pids 5001 | xargs kill -9 2>/dev/null || true; sleep 1
  fi
  ( cd "$ROOT" && \
    REDIS_URL="$REDIS_URL" BAYMAX_REDIS=1 \
    nohup "$VENV_PY" ui/app.py >"$FLASK_LOG" 2>&1 & )
  wait_port 5001 20 "Flask" || { c_red "  see $FLASK_LOG"; exit 1; }
  # Confirm Flask actually reached Redis (the empty-REDIS_URL bug surfaces here).
  if curl -fsS --max-time 4 http://localhost:5001/api/state 2>/dev/null | grep -q '"redis_connected": *true\|"redis_connected":true'; then
    c_green "  ✓ Flask ↔ Redis connected"
  else
    c_red "  ✗ Flask up but Redis NOT connected — check REDIS_URL ($FLASK_LOG)"
  fi
}

start_web() {  # start_web <mode>
  local mode="${1:-live}"
  echo "[4/4] Next.js web ($mode)"
  if lsof -ti:3000 >/dev/null 2>&1; then
    c_dim "  freeing stale :3000…"; port_pids 3000 | xargs kill -9 2>/dev/null || true; sleep 1
  fi
  ( cd "$WEB_DIR" && \
    NEXT_PUBLIC_BAYMAX_MODE="$mode" BAYMAX_API_URL=http://localhost:5001 \
    nohup npm run dev >"$WEB_LOG" 2>&1 & )
  wait_url http://localhost:3000 40 "web" || { c_red "  see $WEB_LOG"; exit 1; }
}

banner() {
  echo ""
  c_green "════════════════════════════════════════════════════════════"
  c_green "  Baymax demo is UP"
  c_green "════════════════════════════════════════════════════════════"
  echo "  Web UI      → http://localhost:3000   (open this)"
  echo "  Flask API   → http://localhost:5001/api/state"
  echo "  Redis UI    → http://localhost:8081"
  echo ""
  c_dim  "  Logs: ./start_demo.sh logs"
  c_dim  "  Panic switch (if live breaks on stage): ./start_demo.sh mock"
  c_dim  "  Back to live: ./start_demo.sh live   |   Stop: ./start_demo.sh down"
  echo ""
}

case "${1:-up}" in
  up)
    preflight; ensure_redis; seed_scenario; start_bureau; start_flask; start_web live; banner ;;
  down)
    echo "Stopping Bureau / Flask / web (Redis left running)…"
    pkill -f run_claude_demo.py 2>/dev/null || true
    pkill -f "ui/app.py" 2>/dev/null || true
    for p in 3000 5001 8000 8079; do port_pids "$p" | xargs kill -9 2>/dev/null || true; done
    c_green "stopped." ;;
  status)
    for p in 6379 8000 5001 3000; do
      if lsof -ti:"$p" >/dev/null 2>&1; then c_green "  :$p up"; else c_red "  :$p down"; fi
    done
    curl -fsS --max-time 4 http://localhost:5001/api/state 2>/dev/null \
      | "$VENV_PY" -c "import sys,json;d=json.load(sys.stdin);print('  redis_connected:',d.get('redis_connected'),' bureau_running:',d.get('bureau_running'))" 2>/dev/null || true ;;
  mock)
    start_web mock; c_red "  WEB IS NOW IN SCRIPTED-MOCK MODE (deterministic, no live agents)"; ;;
  live)
    start_web live; c_green "  web back in LIVE mode"; ;;
  logs)
    echo "── tailing /tmp/baymax_{bureau,flask,web}.log (Ctrl-C to stop) ──"
    tail -n 20 -f "$BUREAU_LOG" "$FLASK_LOG" "$WEB_LOG" ;;
  *)
    echo "Usage: $0 {up|down|status|mock|live|logs}"; exit 1 ;;
esac
