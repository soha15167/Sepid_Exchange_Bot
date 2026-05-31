"""
utils/bot_lifecycle.py — Enable/disable bot side effects / فعال‌سازی و خاموشی ربات
"""

from __future__ import annotations

import logging

from config.settings import ADMIN_IDS, ADVERT_CHANNEL_ID, BROADCAST_ON_ENABLE
from database.db import is_bot_enabled, log_admin_action, set_setting
from utils.channel_maintenance import broadcast_bot_reopened, sync_channel_adverts_for_bot_status

logger = logging.getLogger(__name__)


async def set_bot_enabled_state(
    bot,
    *,
    enabled: bool,
    admin_telegram_id: int | None = None,
    notify_channel: bool = True,
    sync_channel_posts: bool = True,
    broadcast_users: bool = True,
) -> dict:
    """
    Persist bot_enabled, optional channel notice, refresh recent ads, broadcast on enable.
    """
    set_setting("bot_enabled", "1" if enabled else "0")
    try:
        log_admin_action(
            admin_telegram_id or 0,
            "bot_enable" if enabled else "bot_disable",
            f"enabled={enabled}",
        )
    except Exception:
        logger.exception("log_admin_action failed (bot toggle continues)")

    if notify_channel and ADVERT_CHANNEL_ID:
        try:
            msg = "✅ ربات فعال شد." if enabled else "⛔️ ربات غیرفعال شد."
            await bot.send_message(chat_id=ADVERT_CHANNEL_ID, text=msg)
        except Exception:
            logger.exception("channel bot status notice failed")

    sync_ok = sync_fail = 0
    if sync_channel_posts:
        sync_ok, sync_fail = await sync_channel_adverts_for_bot_status(bot)

    broadcast_n = 0
    if enabled and broadcast_users and BROADCAST_ON_ENABLE:
        broadcast_n = await broadcast_bot_reopened(bot)

    return {
        "enabled": enabled,
        "sync_ok": sync_ok,
        "sync_fail": sync_fail,
        "broadcast_n": broadcast_n,
    }


def admin_status_banner_html() -> str:
    if is_bot_enabled():
        return "\u200f🟢 <b>وضعیت ربات:</b> فعال\n"
    return "\u200f🔴 <b>وضعیت ربات:</b> غیرفعال\n"
