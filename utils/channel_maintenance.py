"""
utils/channel_maintenance.py — Channel sync when bot on/off / همگام‌سازی کانال

EN: Refresh recent channel ad posts when bot enabled state changes.
FA: به‌روزرسانی دکمه/متن پست‌های اخیر کانال هنگام فعال/غیرفعال شدن.
"""

from __future__ import annotations

import asyncio
import logging

from config.settings import ADVERT_CHANNEL_ID, LIST_RECENT_LIMIT
from database.db import list_recent_channel_advert_rowids

logger = logging.getLogger(__name__)

# How many recent channel posts to refresh on toggle (env CHANNEL_SYNC_LIMIT)
import os

try:
    CHANNEL_SYNC_LIMIT = max(
        5, min(int((os.getenv("CHANNEL_SYNC_LIMIT") or "25").strip()), 80)
    )
except ValueError:
    CHANNEL_SYNC_LIMIT = 25


async def sync_channel_adverts_for_bot_status(bot) -> tuple[int, int]:
    """
    Re-render recent channel posts (keyboard + maintenance banner).
    Returns (success_count, fail_count).
    """
    from handlers.offers import refresh_advert_channel_post

    ids = list_recent_channel_advert_rowids(CHANNEL_SYNC_LIMIT)
    ok = fail = 0
    for aid in ids:
        try:
            await refresh_advert_channel_post(bot, int(aid))
            ok += 1
        except Exception:
            fail += 1
            logger.exception("channel sync failed advert %s", aid)
        await asyncio.sleep(0.05)
    logger.info("channel sync done: ok=%s fail=%s ids=%s", ok, fail, len(ids))
    return ok, fail


async def broadcast_bot_reopened(bot, *, batch_pause: float = 0.04) -> int:
    """Notify registered users that bot is active again. Returns send count."""
    from database.db import get_all_registered_telegram_ids

    text = (
        "\u200f✅ <b>ربات Sepid دوباره فعال شد.</b>\n\n"
        "می‌توانید آگهی ثبت کنید یا پیشنهاد بفرستید."
    )
    sent = 0
    for uid in get_all_registered_telegram_ids():
        try:
            await bot.send_message(
                int(uid),
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(batch_pause)
    logger.info("broadcast_bot_reopened sent=%s", sent)
    return sent
