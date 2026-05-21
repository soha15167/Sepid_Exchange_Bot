"""
handlers/bonbast_daily.py — Daily Bonbast post / پست روزانهٔ نرخ بن‌بست

EN: Scheduled job posts formatted rates to BONBAST_CHANNEL_ID (or ADVERT_CHANNEL_ID) at Iran time.
FA: هر روز ساعت تنظیم‌شده (پیش‌فرض ۱۲ ظهر تهران) نرخ ارز در کانال هدف منتشر می‌شود.
"""

from __future__ import annotations

import asyncio
import logging
import time

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config.settings import (
    BONBAST_CHANNEL_ID,
    BONBAST_CURRENCY_CODES,
    BONBAST_DAILY_POST_ENABLED,
)
from utils.bonbast_rates import (
    CURRENCY_LABELS,
    fetch_bonbast_rates,
    format_bonbast_channel_html,
)

logger = logging.getLogger(__name__)


async def post_daily_bonbast_rates(context: ContextTypes.DEFAULT_TYPE) -> None:
    """EN/FA: Job callback — fetch Bonbast and send to advert channel."""
    if not BONBAST_DAILY_POST_ENABLED:
        return
    if not BONBAST_CHANNEL_ID:
        logger.warning("bonbast_daily: BONBAST_CHANNEL_ID / ADVERT_CHANNEL_ID not set")
        return

    try:
        t0 = time.monotonic()
        data = await asyncio.to_thread(fetch_bonbast_rates)
        codes = BONBAST_CURRENCY_CODES or list(CURRENCY_LABELS.keys())
        text = format_bonbast_channel_html(data, currency_codes=codes)
        logger.info("bonbast_daily: fetch took %.1fs", time.monotonic() - t0)
        await context.bot.send_message(
            chat_id=int(BONBAST_CHANNEL_ID),
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logger.info("bonbast_daily: posted rates to channel %s", BONBAST_CHANNEL_ID)
    except Exception:
        logger.exception("bonbast_daily: failed to post rates")


async def post_bonbast_rates_now(bot) -> bool:
    """EN: Manual trigger (e.g. admin). FA: ارسال فوری نرخ — برای تست."""
    if not BONBAST_CHANNEL_ID:
        return False
    t0 = time.monotonic()
    data = await asyncio.to_thread(fetch_bonbast_rates)
    logger.info("bonbast_manual: fetch took %.1fs", time.monotonic() - t0)
    codes = BONBAST_CURRENCY_CODES or list(CURRENCY_LABELS.keys())
    text = format_bonbast_channel_html(data, currency_codes=codes)
    await bot.send_message(
        chat_id=int(BONBAST_CHANNEL_ID),
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return True
