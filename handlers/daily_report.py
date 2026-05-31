"""
handlers/daily_report.py — Admin daily stats / گزارش روزانه ادمین
"""

from __future__ import annotations

import logging

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config.settings import ADMIN_IDS
from database.db import daily_stats_since_hours, is_bot_enabled

logger = logging.getLogger(__name__)
_RTL = "\u200f"


async def post_daily_admin_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_IDS:
        return
    try:
        st = daily_stats_since_hours(24)
    except Exception:
        logger.exception("daily_report: stats failed")
        return
    bot_on = "فعال" if is_bot_enabled() else "غیرفعال"
    text = (
        f"{_RTL}📊 <b>گزارش ۲۴ ساعت اخیر</b>\n\n"
        f"{_RTL}🤖 وضعیت ربات: <b>{bot_on}</b>\n"
        f"{_RTL}👥 کاربران جدید: <b>{st['new_users']}</b>\n"
        f"{_RTL}📢 آگهی‌های جدید: <b>{st['new_adverts']}</b>\n"
        f"{_RTL}📨 پیشنهادهای جدید: <b>{st['new_offers']}</b>\n"
        f"{_RTL}✅ پذیرفته‌شده: <b>{st['accepted_offers']}</b>\n"
        f"{_RTL}👥 کل کاربران: <b>{st['total_users']}</b>\n"
    )
    for admin_id in set(ADMIN_IDS or []):
        if not admin_id:
            continue
        try:
            await context.bot.send_message(
                int(admin_id),
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.warning("daily_report: send failed admin=%s", admin_id)
