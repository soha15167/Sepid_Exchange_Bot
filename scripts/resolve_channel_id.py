"""Print numeric chat id for CHANNEL_USERNAME from .env (bot must be channel admin)."""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv()
token = (os.getenv("BOT_TOKEN") or "").strip()
channel = (os.getenv("CHANNEL_USERNAME") or "Sepid_Exchange").strip().lstrip("@")
if not token:
    print("BOT_TOKEN missing in .env", file=sys.stderr)
    sys.exit(1)

q = urllib.parse.urlencode({"chat_id": f"@{channel}"})
url = f"https://api.telegram.org/bot{token}/getChat?{q}"
with urllib.request.urlopen(url, timeout=30) as resp:
    data = json.loads(resp.read().decode())
if not data.get("ok"):
    print(json.dumps(data, ensure_ascii=False, indent=2), file=sys.stderr)
    sys.exit(1)
chat = data["result"]
print(f"CHANNEL_USERNAME=@{channel}")
print(f"ADVERT_CHANNEL_ID={chat['id']}")
print(f"title={chat.get('title', '')}")
