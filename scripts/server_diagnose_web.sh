#!/bin/bash
# Quick diagnostics for Sepid web stack on server
set -e
echo "=== API manual test ==="
cd /root/telegram_bot_project2
source venv/bin/activate
python3 -c "from web_api.main import app; print('import ok', len(app.routes))" || exit 1
echo "=== UI .next ==="
ls -la /root/web/.next/BUILD_ID 2>/dev/null || echo "MISSING: run cd /root/web && npm run build"
echo "=== next binary ==="
ls -la /root/web/node_modules/.bin/next 2>/dev/null || echo "MISSING: run cd /root/web && npm install"
echo "=== ports ==="
ss -tlnp | grep -E ':8100|:3100' || echo "8100/3100 not listening"
echo "=== journal (last 15 lines each) ==="
journalctl -u sepid-web-api -n 15 --no-pager 2>/dev/null || true
journalctl -u sepid-web-ui -n 15 --no-pager 2>/dev/null || true
