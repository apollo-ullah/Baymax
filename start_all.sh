#!/usr/bin/env bash
# start_all.sh — idempotent one-shot launcher for the whole Baymax stack.
# Always kills strays FIRST, so re-running can never create duplicate bureaus,
# watchers, or tunnels (the root cause of every "0/2 offers" / collision bug).
#
# Order:  Redis -> seed -> camera watcher -> ngrok+Flask UI
# Then prints the public ngrok URL. You only touch the browser after this.
#
#   ./start_all.sh                 # hospital_a camera, default item
#   HOSPITAL=hospital_b ./start_all.sh
#
# Leaves everything running in the background; logs in /tmp/baymax_*.log.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
PY="$REPO_ROOT/.venv/bin/python"
HOSPITAL="${HOSPITAL:-hospital_a}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379}"

echo "############################################################"
echo "# start_all — repo: $REPO_ROOT"
echo "############################################################"

# ── 0. Kill strays (idempotent) ─────────────────────────────────────────────
echo; echo "▶ [0/5] killing strays (keeping Redis if already up)…"
./kill_all.sh >/dev/null 2>&1 || true

# ── 1. Redis ────────────────────────────────────────────────────────────────
echo; echo "▶ [1/5] Redis…"
if lsof -nP -iTCP:6379 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "  Redis already up on :6379 — reusing."
else
  nohup redis-server --save "" --appendonly no >/tmp/baymax_redis.log 2>&1 &
  echo "  started redis-server (pid $!)"
fi
# wait for ping
for _ in $(seq 1 20); do
  "$PY" -c "import redis,sys; sys.exit(0 if redis.Redis.from_url('$REDIS_URL',socket_connect_timeout=1,socket_timeout=1).ping() else 1)" 2>/dev/null && break
  sleep 0.5
done
"$PY" -c "import redis; redis.Redis.from_url('$REDIS_URL').config_set('protected-mode','no')" 2>/dev/null \
  && echo "  protected-mode=no (laptop B reachable)" || echo "  WARN: could not reach Redis"

# ── 2. Seed demo data ───────────────────────────────────────────────────────
echo; echo "▶ [2/5] seeding demo data…"
( cd "$REPO_ROOT/redis/src" && REDIS_URL="$REDIS_URL" "$PY" seed_demo_data.py >/tmp/baymax_seed.log 2>&1 ) \
  && echo "  seeded (A/B/C inventory + surplus + forecast + scenario)" \
  || echo "  WARN: seed failed — see /tmp/baymax_seed.log"

# ── 3. Camera watcher ───────────────────────────────────────────────────────
echo; echo "▶ [3/5] camera watcher ($HOSPITAL)…"
KEY="$(grep -E '^ANTHROPIC_API_KEY=' "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]')"
[ -z "$KEY" ] && echo "  WARN: ANTHROPIC_API_KEY not in .env — vision will fall back to count 0"
( cd "$REPO_ROOT/hardware/camera connection" && \
  HOSPITAL_ID="$HOSPITAL" REDIS_URL="$REDIS_URL" ANTHROPIC_API_KEY="$KEY" \
  nohup "$PY" capture_single.py --watch >/tmp/capture_watch.log 2>&1 & )
echo "  started watcher — log: /tmp/capture_watch.log (allow the camera prompt!)"

# ── 4. ngrok + Flask UI ─────────────────────────────────────────────────────
echo; echo "▶ [4/5] ngrok + Flask UI…"
nohup ./fetch/approval/serve_with_ngrok.sh >/tmp/baymax_serve.log 2>&1 &
echo "  started serve_with_ngrok (pid $!) — waiting for tunnel…"
URL=""
for _ in $(seq 1 60); do
  URL="$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | "$PY" -c 'import sys,json;t=json.load(sys.stdin).get("tunnels",[]);print(next((x["public_url"] for x in t if x["public_url"].startswith("https")),""))' 2>/dev/null)"
  [ -n "$URL" ] && break
  sleep 1
done

# ── 5. Verify single-instance + report ──────────────────────────────────────
echo; echo "▶ [5/5] verifying single instances…"
chk() { n=$(pgrep -f "$1" | wc -l | tr -d ' '); printf "  %-26s %s\n" "$2:" "$n instance(s)"; }
chk "redis-server|6379"   "redis (by port below)"
printf "  %-26s %s\n" "redis:" "$(lsof -nP -iTCP:6379 -sTCP:LISTEN -t 2>/dev/null | wc -l | tr -d ' ') listener(s)"
chk "capture_single.py --watch" "camera watcher"
chk "ngrok http 5001"     "ngrok"
chk "ui/app.py"           "flask UI"
chk "run_dashboard_demo"  "bureau (should be 0 until you click Run)"

echo
echo "############################################################"
if [ -n "$URL" ]; then
  echo "✓ STACK UP"
  echo "  Dashboard (public): $URL"
  echo "  Dashboard (local):  http://localhost:5001"
  echo "  → open it and click ⚡ Run Fetch.ai Negotiation (auto-starts the bureau)"
else
  echo "✗ tunnel not detected — check /tmp/baymax_serve.log"
fi
echo "  logs: /tmp/baymax_redis.log /tmp/baymax_seed.log /tmp/capture_watch.log /tmp/baymax_serve.log"
echo "############################################################"
