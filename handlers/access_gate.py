"""
handlers/access_gate.py — Access control / کنترل دسترسی

EN: Blocks restricted users; redirects unregistered users to signup flow;
    blocks all non-admin use when bot is disabled (bot_enabled=0).
FA: کاربر محدودشده و ثبت‌نام‌نشده؛ در حالت غیرفعال بودن ربات، مسدودسازی کاربران.
"""

import re

from telegram import Update
from telegram.constants import ChatType
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
from database.db import get_restriction_block_message, get_user, is_bot_enabled
from models.enums import UserState
from state import user_data_store
from utils.telegram_utils import (
    normalize_telegram_callback_data,
    purge_all_trackable_dm_messages,
    send_or_replace_main_menu,
    send_registration_welcome,
)

from messages.user_errors import BOT_DISABLED as BOT_DISABLED_USER_TEXT


async def _notify_bot_disabled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not u:
        return
    q = update.callback_query
    if q:
        try:
            await q.answer("⛔️ ربات موقتاً غیرفعال است.", show_alert=True)
        except Exception:
            pass
    chat_id = update.effective_chat.id if update.effective_chat else u.id
    if q and q.message:
        try:
            await q.message.delete()
        except Exception:
            pass
    elif update.message:
        try:
            await update.message.delete()
        except Exception:
            pass
    await send_or_replace_main_menu(
        context.bot,
        chat_id=chat_id,
        user_id=u.id,
        store=user_data_store,
        text=BOT_DISABLED_USER_TEXT,
        parse_mode="HTML",
    )


async def bot_disabled_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """وقتی bot_enabled=0 است، فقط ادمین‌ها می‌توانند از ربات استفاده کنند."""
    if _skip_non_private_chat(update):
        return
    u = update.effective_user
    if not u:
        return
    if u.id in set(ADMIN_IDS or []):
        return
    if is_bot_enabled():
        return
    q = update.callback_query
    if q and normalize_telegram_callback_data(q.data or "") == "bot_closed":
        return
    await _notify_bot_disabled(update, context)
    raise ApplicationHandlerStop


_UNREGISTERED_ALLOWED_CALLBACKS = frozenset(
    {
        "main_rules",
        "main_fees",
        "info_close",
        "ch_member_ok",
        "terms_accept",
        "terms_decline",
        "start_begin",
        "reg_cancel",
        "reg_otp_resend_sms",
        "reg_otp_telegram",
        "reg_otp_wait",
        "inline_cancel",
        "svc_cancel",
        "cancel",
    }
)

_REGISTRATION_STATES = frozenset(
    {
        "VERIFY_CODE",
        "VERIFYING_PHONE",
        "PHONE",
        "ADDRESS",
        "EMAIL",
        "LAST_NAME",
        "FIRST_NAME",
        "TERMS",
        "START_REGISTRATION",
    }
)


def _callback_allowed_while_unregistered(data: str) -> bool:
    if data in _UNREGISTERED_ALLOWED_CALLBACKS:
        return True
    if data.startswith("reg_"):
        return True
    if data.startswith("offer_gate_") or re.match(r"^offer_\d+$", data):
        return True
    return False


def _skip_non_private_chat(update: Update) -> bool:
    """پیام/آپدیت کانال و گروه — ربات فقط در چت خصوصی با کاربر کار می‌کند."""
    chat = update.effective_chat
    return not chat or chat.type != ChatType.PRIVATE


def _unregistered_registration_in_progress(context: ContextTypes.DEFAULT_TYPE) -> bool:
    ud = context.user_data
    if not ud:
        return False
    if ud.get("registration_active"):
        return True
    st = (ud.get("state") or "").upper()
    return st in _REGISTRATION_STATES


async def _redirect_unregistered_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    alert: str = "ابتدا ثبت‌نام را تکمیل کنید.",
    extra_message_ids: list[int] | None = None,
) -> None:
    u = update.effective_user
    if not u:
        return
    q = update.callback_query
    if q:
        try:
            await q.answer(alert, show_alert=True)
        except Exception:
            pass
    chat_id = update.effective_chat.id
    extras: list[int] = list(extra_message_ids or [])
    if q and q.message:
        extras.append(q.message.message_id)
    elif update.message:
        try:
            await update.message.delete()
        except Exception:
            pass
        extras.append(update.message.message_id)
    context.user_data.clear()
    context.user_data["state"] = UserState.START.name
    user_data_store.pop(u.id, None)
    await purge_all_trackable_dm_messages(
        context.bot,
        chat_id=chat_id,
        user_id=u.id,
        store=user_data_store,
        context_user_data=context.user_data,
        extra_message_ids=extras,
    )
    await send_registration_welcome(
        context.bot,
        chat_id=chat_id,
        user_id=u.id,
        store=user_data_store,
        context=context,
        purge_chat=False,
    )


async def ensure_registered_or_redirect(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """True if user is not registered and was redirected — caller must return."""
    u = update.effective_user
    if not u or u.id in set(ADMIN_IDS or []):
        return False
    if get_user(u.id) is not None:
        return False
    if _unregistered_registration_in_progress(context):
        return False
    await _redirect_unregistered_user(update, context)
    return True


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
        "reg_otp_telegram",
        "reg_otp_resend_sms",
        "reg_cancel",
        "admin_add_otp_resend",
        "admin_add_otp_show",
        "ch_member_ok",
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
    "my_off|",
    "user_adv|",
    "pg|",
)

_CANCEL_TEXT_RE = re.compile(
    r"^(❌ بازگشت|بازگشت ❌|❌ بازگشت به منوی اصلی|بازگشت به منوی اصلی ❌|❌ انصراف|انصراف ❌|❌ انصراف از ثبت‌نام|انصراف از ثبت‌نام)$"
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
    if reply_menu_text_matches("🚀 درخواست خدمات", text):
        return True
    if reply_menu_text_matches(EXCHANGE_OPTION, text):
        return True
    if not text.startswith("/"):
        return False
    cmd = text.split()[0].split("@", 1)[0]
    return cmd in ("/start", "/menu", "/admin")


async def restricted_user_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _skip_non_private_chat(update):
        return
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
    if _skip_non_private_chat(update):
        return
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
        data = normalize_telegram_callback_data(q.data or "")
        if _callback_allowed_while_unregistered(data):
            return
        await _redirect_unregistered_user(update, context)
        raise ApplicationHandlerStop

    if _unregistered_registration_in_progress(context):
        return

    if not m:
        return
    if m.text:
        t = m.text.strip()
        if t.startswith("/"):
            cmd = t.split()[0].split("@", 1)[0].lower()
            if cmd in ("/start", "/menu", "/admin"):
                return
    await _redirect_unregistered_user(update, context)
    raise ApplicationHandlerStop
