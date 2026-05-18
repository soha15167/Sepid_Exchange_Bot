"""
handlers/access_gate.py — Access control / کنترل دسترسی

EN: Blocks restricted users; redirects unregistered users to signup flow.
FA: کاربر محدودشده و ثبت‌نام‌نشده را از منو/خدمات بازمی‌دارد.
"""

import re

from telegram import Update
from telegram.ext import ContextTypes, ApplicationHandlerStop

from config.settings import ADMIN_IDS
from keyboards.menus import (
    CHANNEL_RULES_REPLY_BUTTON_TEXT,
    EXCHANGE_OPTION,
    FEE_INFO_REPLY_BUTTON_TEXT,
    MY_ADVERTS_REPLY_BUTTON_TEXT,
    MY_OFFERS_REPLY_BUTTON_TEXT,
    reply_menu_text_matches,
)
from database.db import get_restriction_block_message, get_user
from state import user_data_store
from utils.telegram_utils import (
    normalize_telegram_callback_data,
    send_or_replace_main_menu,
    send_registration_welcome,
)

_UNREGISTERED_BLOCK_CALLBACKS = frozenset(
    {"main_profile", "main_services", "main_offers", "main_my_adverts"}
)


def _is_main_menu_reply_text(text: str) -> bool:
    labels = (
        "🚀 ثبت درخواست خدمات",
        "🧾 مشاهده پروفایل",
        MY_OFFERS_REPLY_BUTTON_TEXT,
        MY_ADVERTS_REPLY_BUTTON_TEXT,
        CHANNEL_RULES_REPLY_BUTTON_TEXT,
        FEE_INFO_REPLY_BUTTON_TEXT,
        EXCHANGE_OPTION,
    )
    for label in labels:
        if reply_menu_text_matches(label, text):
            return True
    return False

_RESTRICTED_OK_CALLBACKS = frozenset(
    {
        "main_profile",
        "main_services",
        "main_offers",
        "main_my_adverts",
        "main_rules",
        "main_fees",
        "info_close",
        "start_begin",
        "terms_accept",
        "terms_decline",
        "inline_cancel",
        "svc_cancel",
        "cancel",
        "my_offers_close",
    }
)


_RESTRICTED_OK_CALLBACK_PREFIXES = (
    "offer_del|",
    "offer_edit|",
    "neg_focus|",
    "neg_pc|",
    "neg_send|",
    "neg_gc|",
    "user_adv|",
)

_CANCEL_TEXT_RE = re.compile(
    r"^(❌ بازگشت|بازگشت ❌|❌ بازگشت به منوی اصلی|بازگشت به منوی اصلی ❌|❌ انصراف|انصراف ❌)$"
)


def _restricted_update_allowed(update: Update) -> bool:
    q = update.callback_query
    if q:
        d = q.data or ""
        if normalize_telegram_callback_data(d) in _RESTRICTED_OK_CALLBACKS:
            return True
        return any(d.startswith(p) for p in _RESTRICTED_OK_CALLBACK_PREFIXES)
    m = update.message
    if not m:
        return False
    text = (m.text or "").strip()
    if not text:
        return False
    if _CANCEL_TEXT_RE.match(text):
        return True
    if reply_menu_text_matches(MY_ADVERTS_REPLY_BUTTON_TEXT, text):
        return True
    if reply_menu_text_matches(MY_OFFERS_REPLY_BUTTON_TEXT, text):
        return True
    if reply_menu_text_matches(CHANNEL_RULES_REPLY_BUTTON_TEXT, text):
        return True
    if reply_menu_text_matches(FEE_INFO_REPLY_BUTTON_TEXT, text):
        return True
    if reply_menu_text_matches("🧾 مشاهده پروفایل", text):
        return True
    if reply_menu_text_matches("🚀 ثبت درخواست خدمات", text):
        return True
    if reply_menu_text_matches(EXCHANGE_OPTION, text):
        return True
    if not text.startswith("/"):
        return False
    cmd = text.split()[0].split("@", 1)[0]
    return cmd in ("/start", "/menu", "/admin")


async def restricted_user_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not u:
        return
    if u.id in set(ADMIN_IDS or []):
        return
    msg = get_restriction_block_message(u.id)
    if not msg:
        return
    if _restricted_update_allowed(update):
        return
    if update.callback_query:
        q = update.callback_query
        try:
            await q.answer()
        except Exception:
            pass
        if q.message:
            try:
                await q.message.delete()
            except Exception:
                pass
            chat_id = q.message.chat_id
        else:
            chat_id = update.effective_chat.id
        await send_or_replace_main_menu(
            context.bot,
            chat_id=chat_id,
            user_id=u.id,
            store=user_data_store,
            text=msg,
        )
        raise ApplicationHandlerStop
    if update.message:
        chat_id = update.effective_chat.id
        try:
            await update.message.delete()
        except Exception:
            pass
        await send_or_replace_main_menu(
            context.bot,
            chat_id=chat_id,
            user_id=u.id,
            store=user_data_store,
            text=msg,
        )
        raise ApplicationHandlerStop


async def unregistered_user_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """کاربر بدون رکورد users نباید به منوی اصلی (ریپلای/اینلاین) دسترسی داشته باشد."""
    u = update.effective_user
    if not u:
        return
    if u.id in set(ADMIN_IDS or []):
        return
    if get_user(u.id) is not None:
        return

    m = update.message
    if m and m.text:
        t = m.text.strip()
        if reply_menu_text_matches(CHANNEL_RULES_REPLY_BUTTON_TEXT, t):
            return
        if reply_menu_text_matches(FEE_INFO_REPLY_BUTTON_TEXT, t):
            return

    q = update.callback_query
    if q:
        if normalize_telegram_callback_data(q.data or "") not in _UNREGISTERED_BLOCK_CALLBACKS:
            return
        try:
            await q.answer("ابتدا ثبت‌نام را تکمیل کنید.", show_alert=True)
        except Exception:
            pass
        chat_id = q.message.chat_id if q.message else update.effective_chat.id
        await send_registration_welcome(
            context.bot,
            chat_id=chat_id,
            user_id=u.id,
            store=user_data_store,
            context=context,
        )
        raise ApplicationHandlerStop

    if context.user_data.get("registration_active"):
        return
    if context.user_data.get("state") == "TERMS":
        return

    if not m or not m.text:
        return
    t = m.text.strip()
    if t.startswith("/"):
        cmd = t.split()[0].split("@", 1)[0].lower()
        if cmd in ("/start", "/menu", "/admin"):
            return
    if _is_main_menu_reply_text(t):
        try:
            await m.delete()
        except Exception:
            pass
    else:
        try:
            await m.delete()
        except Exception:
            pass
    await send_registration_welcome(
        context.bot,
        chat_id=m.chat_id,
        user_id=u.id,
        store=user_data_store,
        context=context,
    )
    raise ApplicationHandlerStop
