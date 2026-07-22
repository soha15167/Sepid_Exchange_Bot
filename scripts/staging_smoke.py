#!/usr/bin/env python3
"""Opt-in Telegram staging smoke check; only messages STAGING_SMOKE_CHAT_ID."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run() -> int:
    from telegram import Bot

    token = (os.getenv("STAGING_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("STAGING_SMOKE_CHAT_ID") or "").strip()
    if not token or not chat_id:
        print(
            "ERROR: STAGING_BOT_TOKEN and STAGING_SMOKE_CHAT_ID are required",
            file=sys.stderr,
        )
        return 2
    bot = Bot(token)
    identity = await bot.get_me()
    message = await bot.send_message(
        chat_id=int(chat_id),
        text="Sepid staging smoke check: bot API and outbound delivery are healthy.",
        disable_notification=True,
    )
    print(f"OK @{identity.username} message_id={message.message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
