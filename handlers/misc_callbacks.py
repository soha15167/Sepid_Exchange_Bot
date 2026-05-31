"""handlers/misc_callbacks.py — Small global callbacks / callbackهای عمومی"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from messages.user_errors import BOT_DISABLED


async def handle_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q:
        try:
            await q.answer()
        except Exception:
            pass


async def handle_bot_closed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دکمهٔ پست کانال وقتی ربات غیرفعال است."""
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer(BOT_DISABLED.replace("<b>", "").replace("</b>", ""), show_alert=True)
    except Exception:
        pass
