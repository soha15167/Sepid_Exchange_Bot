"""
utils/channel_ad_publish.py — After channel post / پس از انتشار آگهی

EN: Open channel post via callback answer(url=...); no forward to DM (offer button stays on channel only).
FA: باز شدن پست کانال در کلاینت؛ بدون فوروارد به چت خصوصی.
"""

from __future__ import annotations

import logging

from telegram import Bot
from telegram.constants import ParseMode

from config.settings import ADVERT_CHANNEL_ID

logger = logging.getLogger(__name__)


async def try_open_telegram_url(query, url: str | None) -> None:
    """EN: Opens channel post / bot deep link in the Telegram app (best-effort)."""
    if not query or not url:
        return
    try:
        await query.answer(url=url)
    except Exception:
        try:
            await query.answer()
        except Exception:
            pass


async def forward_published_ad_to_user(
    bot: Bot,
    *,
    user_chat_id: int,
    channel_message_id: int,
) -> bool:
    """Forward the channel post into user's private chat."""
    if not ADVERT_CHANNEL_ID:
        return False
    try:
        await bot.forward_message(
            chat_id=int(user_chat_id),
            from_chat_id=int(ADVERT_CHANNEL_ID),
            message_id=int(channel_message_id),
        )
        return True
    except Exception as exc:
        logger.warning(
            "forward_published_ad failed chat=%s mid=%s: %s",
            user_chat_id,
            channel_message_id,
            exc,
        )
        return False


async def send_ad_published_notice(
    bot: Bot,
    *,
    user_chat_id: int,
    channel_message_id: int | None = None,
) -> None:
    """Deprecated: publish flow opens the channel post via try_open_telegram_url only."""
    _ = (bot, user_chat_id, channel_message_id)
