#!/bin/bash
# Sepid Exchange — start API + UI on server (run as root)
# Usage: bash /root/telegram_bot_project2/scripts/server_start_web.sh

set -e

BOT_ROOT="/root/telegram_bot_project2"
WEB_ROOT="/root/web"
API_PORT=8100
UI_PORT=3100

log() { echo "[sepid-web] $*"; }

# --- API (prefer systemd; kill stale manual processes) ---
if systemctl is-enabled sepid-web-api >/dev/null 2>&1; then
  log "Restarting sepid-web-api via systemd ..."
  fuser -k "${API_PORT}/tcp" 2>/dev/null || true
  sleep 1
  systemctl restart sepid-web-api
  sleep 2
elif ss -tlnp 2>/dev/null | grep -q ":${API_PORT} "; then
  log "API already listening on :${API_PORT}"
else
  log "Starting API on :${API_PORT} ..."
  cd "$BOT_ROOT"
  # shellcheck disable=SC1091
  source venv/bin/activate
  nohup python3 scripts/run_web_api.py >> /var/log/sepid-web-api.log 2>&1 &
  sleep 2
fi

if curl -sf "http://127.0.0.1:${API_PORT}/api/health" >/dev/null; then
  log "API OK"
else
  log "ERROR: API failed — check journalctl -u sepid-web-api"
  exit 1
fi

# --- UI build (if needed) ---
cd "$WEB_ROOT"
if [ ! -d ".next" ]; then
  log "Building UI (may take 2–5 min on VPS) ..."
  export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=1536}"
  npm run build
fi

# --- UI start ---
if ss -tlnp 2>/dev/null | grep -q ":${UI_PORT} "; then
  log "UI already listening on :${UI_PORT}"
else
  log "Starting UI on 0.0.0.0:${UI_PORT} ..."
  export NEXT_PUBLIC_API_URL="http://127.0.0.1:${API_PORT}"
  nohup npx next start -H 0.0.0.0 -p "${UI_PORT}" >> /var/log/sepid-web-ui.log 2>&1 &
  sleep 3
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${UI_PORT}/" || echo "000")
  if [ "$CODE" = "200" ] || [ "$CODE" = "304" ]; then
    log "UI OK — http://49.13.132.230:${UI_PORT}"
  else
    log "ERROR: UI not responding (HTTP $CODE) — check /var/log/sepid-web-ui.log"
    tail -20 /var/log/sepid-web-ui.log 2>/dev/null || true
    exit 1
  fi
fi

log "Done. API :${API_PORT} | UI :${UI_PORT}"
