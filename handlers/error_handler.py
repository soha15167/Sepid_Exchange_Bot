"""
handlers/error_handler.py — Global errors / خطاهای سراسری
"""

from __future__ import annotations

import html
import logging
import traceback

from telegram import Update
from telegram.constants import ChatType
from telegram.error import NetworkError, TelegramError, TimedOut
from telegram.ext import ApplicationHandlerStop, ContextTypes

logger = logging.getLogger(__name__)

_MAX_ADMIN_TRACE = 2800


def _update_context_snippet(update: Update) -> str:
    parts: list[str] = []
    user = update.effective_user
    if user:
        uname = f"@{user.username}" if user.username else "—"
        parts.append(f"کاربر: <code>{user.id}</code> {html.escape(uname)}")
    chat = update.effective_chat
    if chat:
        parts.append(f"چت: <code>{chat.id}</code> ({html.escape(str(chat.type))})")
    if update.message:
        if update.message.text:
            parts.append(f"متن: <code>{html.escape(update.message.text[:300])}</code>")
        elif update.message.caption:
            parts.append(f"کپشن: <code>{html.escape(update.message.caption[:300])}</code>")
    elif update.callback_query:
        parts.append(f"callback: <code>{html.escape((update.callback_query.data or '')[:200])}</code>")
    return "\n".join(parts) if parts else "—"


async def _notify_admins_about_error(
    context: ContextTypes.DEFAULT_TYPE,
    update: Update,
    err: BaseException,
) -> None:
    from config.settings import ADMIN_IDS

    admin_ids = [int(x) for x in (ADMIN_IDS or []) if int(x) > 0]
    if not admin_ids:
        return

    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    if len(tb) > _MAX_ADMIN_TRACE:
        tb = "…\n" + tb[-_MAX_ADMIN_TRACE:]

    body = (
        f"{_update_context_snippet(update)}\n\n"
        f"<b>{html.escape(type(err).__name__)}:</b> "
        f"{html.escape(str(err)[:500])}\n\n"
        f"<pre>{html.escape(tb)}</pre>"
    )
    text = f"⚠️ <b>خطای ربات</b>\n\n{body}"
    if len(text) > 4096:
        text = text[:4090] + "…"

    for aid in admin_ids:
        try:
            await context.bot.send_message(
                aid,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("failed to notify admin %s about error", aid)


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if err is None:
        return

    # کنترل جریان عادی (گیت دسترسی، مسدودسازی پیشنهاد، …) — خطا نیست.
    if isinstance(err, ApplicationHandlerStop):
        raise err

    logger.error(
        "handler exception update=%s error=%s",
        update,
        err,
        exc_info=err,
    )

    # قطعی/شبکه — به کاربر پیام نده
    if isinstance(err, (TimedOut, NetworkError)):
        return

    # خطاهای API تلگرام (پیام حذف‌شده، دکمه قدیمی، …) — فقط لاگ
    if isinstance(err, TelegramError):
        return

    if not isinstance(update, Update) or not update.effective_chat:
        return

    await _notify_admins_about_error(context, update, err)

    from messages.user_errors import GENERIC_ERROR

    chat = update.effective_chat
    # در کانال/گروه پیام خطا نفرست (مثلاً اگر ربات ادمین کانال باشد)
    if chat.type != ChatType.PRIVATE:
        return

    try:
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        await context.bot.send_message(
            chat.id,
            GENERIC_ERROR,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("failed to notify user about error")
