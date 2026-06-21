#!/usr/bin/env bash
# Launch the approval service behind an ngrok tunnel so the iMessage accept/reject
# links are tappable from a phone. One command:
#
#     ./fetch/approval/serve_with_ngrok.sh
#
# Requires (one-time): `ngrok config add-authtoken <token>` and the .venv with
# fastapi/uvicorn. Reads optional NGROK_DOMAIN from the environment / .env to use
# a stable ngrok domain (otherwise a random https URL is used each run).
#
# What it does:
#   1. starts ngrok http <PORT>
#   2. reads the public https URL from ngrok's local API (127.0.0.1:4040)
#   3. exports APPROVAL_BASE_URL = that URL  (so service.py builds tappable links)
#   4. starts uvicorn; on Ctrl-C it kills ngrok too.
set -euo pipefail

PORT="${PORT:-8080}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Activate venv if present.
[ -f .venv/bin/activate ] && source .venv/bin/activate

# Pull NGROK_AUTHTOKEN / NGROK_DOMAIN from .env if not already in the environment.
_from_env() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]'; }
if [ -f .env ]; then
  [ -z "${NGROK_AUTHTOKEN:-}" ] && NGROK_AUTHTOKEN="$(_from_env NGROK_AUTHTOKEN)"
  [ -z "${NGROK_DOMAIN:-}" ] && NGROK_DOMAIN="$(_from_env NGROK_DOMAIN)"
fi

# ngrok reads NGROK_AUTHTOKEN from the environment natively (no `ngrok config` needed).
# Export it if we have one; otherwise fall back to a previously saved ngrok config.
if [ -n "${NGROK_AUTHTOKEN:-}" ]; then
  export NGROK_AUTHTOKEN
  echo "▶ using NGROK_AUTHTOKEN from environment/.env"
else
  echo "▶ no NGROK_AUTHTOKEN set — relying on saved ngrok config (~/.config/ngrok/ngrok.yml)"
fi

echo "▶ starting ngrok on port $PORT ${NGROK_DOMAIN:+(domain: $NGROK_DOMAIN)} ..."
if [ -n "${NGROK_DOMAIN:-}" ]; then
  ngrok http "$PORT" --domain "$NGROK_DOMAIN" --log=stdout >/tmp/ngrok_approval.log 2>&1 &
else
  ngrok http "$PORT" --log=stdout >/tmp/ngrok_approval.log 2>&1 &
fi
NGROK_PID=$!
cleanup() { echo; echo "▶ stopping ngrok ($NGROK_PID)"; kill "$NGROK_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# Wait for ngrok's local API to report the public URL.
echo "▶ waiting for ngrok tunnel ..."
PUBLIC_URL=""
for _ in $(seq 1 30); do
  PUBLIC_URL="$(curl -s http://127.0.0.1:4040/api/tunnels \
    | python -c 'import sys,json; t=json.load(sys.stdin).get("tunnels",[]); print(next((x["public_url"] for x in t if x["public_url"].startswith("https")), ""))' 2>/dev/null || true)"
  [ -n "$PUBLIC_URL" ] && break
  sleep 0.5
done

if [ -z "$PUBLIC_URL" ]; then
  echo "✗ could not get ngrok URL. Is the authtoken set? (ngrok config add-authtoken <token>)"
  echo "  ngrok log:"; tail -20 /tmp/ngrok_approval.log
  exit 1
fi

export APPROVAL_BASE_URL="$PUBLIC_URL"
echo "✓ tunnel up: $PUBLIC_URL"
echo "✓ APPROVAL_BASE_URL=$APPROVAL_BASE_URL"
echo "▶ trigger a demo with:  curl -XPOST $PUBLIC_URL/shortage"
echo "▶ starting uvicorn (Ctrl-C to stop both) ..."
exec uvicorn fetch.approval.service:app --host 0.0.0.0 --port "$PORT"
