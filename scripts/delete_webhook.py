"""One-shot: remove Telegram webhook so Python polling can run (after PHP bot)."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from dotenv import load_dotenv

load_dotenv()
token = (os.getenv("BOT_TOKEN") or "").strip()
if not token:
    print("BOT_TOKEN missing in .env", file=sys.stderr)
    sys.exit(1)

url = f"https://api.telegram.org/bot{token}/deleteWebhook?drop_pending_updates=true"
with urllib.request.urlopen(url, timeout=30) as resp:
    data = json.loads(resp.read().decode())
print(json.dumps(data, ensure_ascii=False, indent=2))
sys.exit(0 if data.get("ok") else 1)
