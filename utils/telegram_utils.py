"""
utils/telegram_utils.py — Telegram UI helpers / کمک‌تابع تلگرام

EN: Safe delete, main menu anchor, registration flow messages, callback normalize.
FA: حذف پیام، جایگزینی منو، شروع ثبت‌نام، نرمال‌سازی callback_data.
"""

from __future__ import annotations

from telegram import Message, ReplyKeyboardRemove
from telegram.error import BadRequest

from database.db import (
    clear_dm_trackable_messages,
    clear_main_menu_anchor,
    fetch_dm_trackable_messages,
    fetch_main_menu_anchor,
    record_dm_trackable_message,
    save_main_menu_anchor,
)


async def safe_delete_message(message: Message | None) -> None:
    """
    Best-effort delete (ignore all errors).
    Telegram may refuse deletion due to permissions/timeouts.
    """
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        pass


def is_message_not_modified_error(exc: BaseException) -> bool:
    return "message is not modified" in str(exc).lower()


async def safe_edit_message_text(
    bot,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
    disable_web_page_preview: bool | None = None,
) -> bool:
    """
    ویرایش پیام؛ True اگر نمایش درست شد یا محتوا تغییری نکرده (message is not modified).
    """
    kw: dict = {
        "chat_id": int(chat_id),
        "message_id": int(message_id),
        "text": text,
    }
    if reply_markup is not None:
        kw["reply_markup"] = reply_markup
    if parse_mode is not None:
        kw["parse_mode"] = parse_mode
    if disable_web_page_preview is not None:
        kw["disable_web_page_preview"] = disable_web_page_preview
    try:
        await bot.edit_message_text(**kw)
        return True
    except BadRequest as exc:
        if is_message_not_modified_error(exc):
            return True
        raise


def remember_cleanup_id(store: dict, user_id: int, message_id: int | None, key: str) -> None:
    if not message_id:
        return
    user_bucket = store.setdefault(user_id, {})
    ids = user_bucket.setdefault(key, [])
    if message_id not in ids:
        ids.append(message_id)
    if key in _PERSIST_DM_TRACKABLE_KEYS:
        record_dm_trackable_message(user_id, message_id)


async def cleanup_ids(bot, chat_id: int, ids: list[int]) -> None:
    for mid in ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass


# کلیدهای ردیابی پیام‌های موقت DM (کاربر + ربات در فلو)
EURO_FLOW_CLEANUP_KEY = "euro_cleanup_message_ids"
EXCHANGE_FLOW_CLEANUP_KEY = "exchange_cleanup_message_ids"
MAIN_EXTRA_CLEANUP_KEY = "main_cleanup_message_ids"
REGISTRATION_CLEANUP_KEY = "registration_cleanup_message_ids"
FLOW_KEEP_MESSAGE_IDS_KEY = "flow_keep_message_ids"

_ALL_FLOW_CLEANUP_STORE_KEYS = (
    EURO_FLOW_CLEANUP_KEY,
    EXCHANGE_FLOW_CLEANUP_KEY,
    MAIN_EXTRA_CLEANUP_KEY,
    REGISTRATION_CLEANUP_KEY,
)

_PERSIST_DM_TRACKABLE_KEYS = frozenset(_ALL_FLOW_CLEANUP_STORE_KEYS)


def _collect_keep_ids(store: dict, user_id: int, context_user_data: dict | None) -> set[int]:
    """فقط پیام‌های صریح mark_flow_keep_message — نه anchor منو."""
    keep: set[int] = set()
    bucket = store.get(user_id)
    if isinstance(bucket, dict):
        for mid in bucket.pop(FLOW_KEEP_MESSAGE_IDS_KEY, None) or []:
            try:
                keep.add(int(mid))
            except (TypeError, ValueError):
                pass
    if context_user_data:
        for mid in context_user_data.pop(FLOW_KEEP_MESSAGE_IDS_KEY, None) or []:
            try:
                keep.add(int(mid))
            except (TypeError, ValueError):
                pass
    return keep


def pop_menu_anchor_message_id(store: dict, user_id: int) -> int | None:
    bucket = store.get(user_id)
    old = None
    if isinstance(bucket, dict):
        old = bucket.pop(MAIN_MENU_ANCHOR_KEY, None)
    if not old:
        db = fetch_main_menu_anchor(user_id)
        if db:
            old = {"cid": db[0], "mid": db[1]}
    clear_main_menu_anchor(user_id)
    if not old:
        return None
    try:
        return int(old["mid"])
    except (TypeError, ValueError, KeyError):
        return None


def mark_flow_keep_message(
    store: dict, user_id: int, context_user_data: dict | None, message_id: int | None
) -> None:
    """پیام‌هایی که بعد از پاک‌سازی فلو باید بمانند (تأیید آگهی / پیشنهاد / پذیرش)."""
    if not message_id:
        return
    mid = int(message_id)
    remember_cleanup_id(store, user_id, mid, FLOW_KEEP_MESSAGE_IDS_KEY)
    if context_user_data is not None:
        xs = context_user_data.setdefault(FLOW_KEEP_MESSAGE_IDS_KEY, [])
        if mid not in xs:
            xs.append(mid)


def reset_flow_message_tracking(
    store: dict,
    user_id: int,
    context_user_data: dict | None,
    *,
    key: str = REGISTRATION_CLEANUP_KEY,
) -> None:
    bucket = store.setdefault(user_id, {})
    bucket[key] = []
    if context_user_data is not None and key == REGISTRATION_CLEANUP_KEY:
        context_user_data["registration_cleanup_mids"] = []


def track_flow_message(
    store: dict,
    user_id: int,
    context_user_data: dict | None,
    message_id: int | None,
    *,
    key: str = EURO_FLOW_CLEANUP_KEY,
) -> None:
    if not message_id:
        return
    remember_cleanup_id(store, user_id, message_id, key)
    if context_user_data is not None and key == REGISTRATION_CLEANUP_KEY:
        mids = context_user_data.setdefault("registration_cleanup_mids", [])
        mid = int(message_id)
        if mid not in mids:
            mids.append(mid)


def track_flow_user_message(
    update: object,
    store: dict,
    user_id: int,
    context_user_data: dict | None,
    *,
    key: str = EURO_FLOW_CLEANUP_KEY,
) -> None:
    msg = getattr(update, "message", None)
    if msg:
        track_flow_message(store, user_id, context_user_data, msg.message_id, key=key)


async def cleanup_transient_dm_messages(
    bot,
    *,
    chat_id: int,
    user_id: int,
    store: dict,
    context_user_data: dict | None,
    extra_message_ids: list[int] | None = None,
    keep_message_ids: list[int] | None = None,
) -> None:
    """
    حذف پیام‌های موقت فلو (ثبت‌نام، یورو، معاوضه، پیشنهاد، منو).
    پیام‌های mark_flow_keep_message و register_offer_thread_message حفظ می‌شوند.
    """
    keep = _collect_keep_ids(store, user_id, context_user_data)
    if keep_message_ids:
        for mid in keep_message_ids:
            try:
                keep.add(int(mid))
            except (TypeError, ValueError):
                pass

    ids: list[int] = []
    if extra_message_ids:
        for mid in extra_message_ids:
            try:
                ids.append(int(mid))
            except (TypeError, ValueError):
                pass
    if context_user_data:
        for ctx_key in ("offer_flow_mids", "offer_flow_input_mids", "registration_cleanup_mids"):
            raw = context_user_data.pop(ctx_key, None)
            if isinstance(raw, list):
                for x in raw:
                    if x is None:
                        continue
                    try:
                        ids.append(int(x))
                    except (TypeError, ValueError):
                        pass
    bucket = store.get(user_id)
    if isinstance(bucket, dict):
        for key in _ALL_FLOW_CLEANUP_STORE_KEYS:
            part = bucket.pop(key, None) or []
            for mid in part:
                if mid is None:
                    continue
                try:
                    ids.append(int(mid))
                except (TypeError, ValueError):
                    pass
    seen: set[int] = set()
    unique: list[int] = []
    for mid in ids:
        if mid in keep or mid in seen:
            continue
        seen.add(mid)
        unique.append(mid)
    await cleanup_ids(bot, chat_id=chat_id, ids=unique)


def clear_user_bot_session(store: dict, telegram_id: int) -> None:
    """پس از حذف کاربر توسط ادمین — حافظهٔ موقت و لیست پیام‌های قابل حذف."""
    store.pop(telegram_id, None)
    clear_dm_trackable_messages(telegram_id)


async def notify_account_deleted_by_admin(bot, telegram_id: int) -> None:
    """به کاربر حذف‌شده اطلاع بده؛ session در همان فرایند ربات با گیت بعدی پاک می‌شود."""
    try:
        await bot.send_message(
            chat_id=telegram_id,
            text="\u200fحساب شما توسط مدیر حذف شده است.\nبرای ادامه دوباره ثبت‌نام کنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception:
        pass


async def purge_all_trackable_dm_messages(
    bot,
    *,
    chat_id: int,
    user_id: int,
    store: dict,
    context_user_data: dict | None = None,
    extra_message_ids: list[int] | None = None,
    keep_message_ids: list[int] | None = None,
) -> None:
    """
    حذف پیام‌های ردیابی‌شده (حافظه + دیتابیس) — برای شروع تازهٔ ثبت‌نام.
    پیام‌هایی که ربات شناسه نداشته یا قدیمی‌تر از ۴۸ ساعت باشند ممکن است بمانند.
    """
    keep: set[int] = set()
    if keep_message_ids:
        for mid in keep_message_ids:
            try:
                keep.add(int(mid))
            except (TypeError, ValueError):
                pass
    ids: list[int] = list(fetch_dm_trackable_messages(user_id))
    if extra_message_ids:
        ids.extend(extra_message_ids)
    bucket = store.get(user_id)
    if isinstance(bucket, dict):
        anchor = bucket.get(MAIN_MENU_ANCHOR_KEY)
        if anchor:
            try:
                ids.append(int(anchor["mid"]))
            except (TypeError, ValueError, KeyError):
                pass
        for key in _ALL_FLOW_CLEANUP_STORE_KEYS:
            part = bucket.get(key) or []
            for mid in part:
                if mid is not None:
                    ids.append(int(mid))
    db_anchor = fetch_main_menu_anchor(user_id)
    if db_anchor:
        ids.append(db_anchor[1])
    if context_user_data:
        for ctx_key in (
            "offer_flow_mids",
            "offer_flow_input_mids",
            "registration_cleanup_mids",
        ):
            raw = context_user_data.get(ctx_key)
            if isinstance(raw, list):
                for x in raw:
                    if x is not None:
                        try:
                            ids.append(int(x))
                        except (TypeError, ValueError):
                            pass
    seen: set[int] = set()
    unique: list[int] = []
    for mid in ids:
        try:
            m = int(mid)
        except (TypeError, ValueError):
            continue
        if m in keep or m in seen:
            continue
        seen.add(m)
        unique.append(m)
    await cleanup_ids(bot, chat_id=chat_id, ids=unique)
    clear_dm_trackable_messages(user_id)
    clear_main_menu_anchor(user_id)
    if isinstance(bucket, dict):
        for key in _ALL_FLOW_CLEANUP_STORE_KEYS:
            bucket.pop(key, None)
        bucket.pop(MAIN_MENU_ANCHOR_KEY, None)
    if context_user_data:
        for ctx_key in (
            "offer_flow_mids",
            "offer_flow_input_mids",
            "registration_cleanup_mids",
            "registration_active",
            "registration",
        ):
            context_user_data.pop(ctx_key, None)


# آخرین پیام «منوی اصلی» برای حذف/جایگزینی و جلوگیری از انباشت
MAIN_MENU_ANCHOR_KEY = "main_menu_anchor"


def _take_main_menu_anchor_ref(store: dict, user_id: int) -> dict | None:
    """خواندن anchor از حافظه یا دیتابیس (بدون پاک کردن دیتابیس)."""
    bucket = store.get(user_id)
    if isinstance(bucket, dict):
        old = bucket.get(MAIN_MENU_ANCHOR_KEY)
        if old:
            return old
    db = fetch_main_menu_anchor(user_id)
    if db:
        return {"cid": db[0], "mid": db[1]}
    return None


def set_main_menu_anchor(store: dict, user_id: int, chat_id: int, message_id: int) -> None:
    bucket = store.setdefault(user_id, {})
    bucket[MAIN_MENU_ANCHOR_KEY] = {"cid": int(chat_id), "mid": int(message_id)}
    save_main_menu_anchor(user_id, chat_id, message_id)
    record_dm_trackable_message(user_id, message_id)


async def strip_reply_keyboard(bot, *, chat_id: int) -> None:
    """حذف کیبورد reply قدیمی تلگرام (بدون حباب دائمی در چت)."""
    try:
        rm = await bot.send_message(chat_id=chat_id, text="\u2063", reply_markup=ReplyKeyboardRemove())
        await rm.delete()
    except Exception:
        pass


async def _delete_main_menu_anchor_message(bot, *, user_id: int, store: dict) -> None:
    bucket = store.setdefault(user_id, {})
    old = bucket.pop(MAIN_MENU_ANCHOR_KEY, None)
    if not old:
        db = fetch_main_menu_anchor(user_id)
        if db:
            old = {"cid": db[0], "mid": db[1]}
    clear_main_menu_anchor(user_id)
    if old:
        try:
            await bot.delete_message(chat_id=int(old["cid"]), message_id=int(old["mid"]))
        except Exception:
            pass


async def remove_main_menu_anchor_message(bot, *, user_id: int, store: dict) -> None:
    """حذف پیام منوی اصلی بدون ارسال منوی جدید (مثلاً هنگام انتظار برای ورودی کاربر)."""
    await _delete_main_menu_anchor_message(bot, user_id=user_id, store=store)


def reset_flow_user_bucket(store: dict, user_id: int) -> None:
    """حداقل دادهٔ فلو را بساز؛ anchor منوی اصلی را نگه دار تا send_or_replace بتواند حباب قبلی را حذف کند."""
    prev = store.pop(user_id, None)
    bucket: dict = {"methods": [], "operation": ""}
    anchor = None
    if prev:
        anchor = prev.get(MAIN_MENU_ANCHOR_KEY)
    if not anchor:
        db = fetch_main_menu_anchor(user_id)
        if db:
            anchor = {"cid": db[0], "mid": db[1]}
    if anchor:
        bucket[MAIN_MENU_ANCHOR_KEY] = anchor
    store[user_id] = bucket


async def send_registration_welcome(
    bot,
    *,
    chat_id: int,
    user_id: int,
    store: dict,
    context=None,
    purge_chat: bool = True,
) -> int:
    """فقط خوش‌آمد + دکمهٔ ثبت‌نام (قوانین بعد از کلیک ثبت‌نام)."""
    from keyboards.menus import start_inline_keyboard
    from messages import texts
    from models.enums import UserState

    ctx = getattr(context, "user_data", None) if context is not None else None
    if purge_chat:
        await purge_all_trackable_dm_messages(
            bot,
            chat_id=chat_id,
            user_id=user_id,
            store=store,
            context_user_data=ctx,
        )

    await _delete_main_menu_anchor_message(bot, user_id=user_id, store=store)
    await strip_reply_keyboard(bot, chat_id=chat_id)
    sent = await bot.send_message(
        chat_id=chat_id,
        text=texts.WELCOME_MESSAGE,
        reply_markup=start_inline_keyboard,
    )
    set_main_menu_anchor(store, user_id, sent.chat_id, sent.message_id)
    if context is not None:
        context.user_data["state"] = UserState.START.name
    return sent.message_id


async def send_registration_terms(
    bot,
    *,
    chat_id: int,
    user_id: int,
    store: dict,
    context=None,
) -> int:
    """نمایش قوانین — فقط پس از زدن «ثبت‌نام»."""
    from telegram.constants import ParseMode

    from keyboards.menus import terms_inline_keyboard
    from messages import texts
    from models.enums import UserState

    await _delete_main_menu_anchor_message(bot, user_id=user_id, store=store)
    sent = await bot.send_message(
        chat_id=chat_id,
        text=texts.TERMS_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=terms_inline_keyboard,
    )
    set_main_menu_anchor(store, user_id, sent.chat_id, sent.message_id)
    if context is not None:
        context.user_data["state"] = UserState.TERMS.name
    return sent.message_id


async def send_or_replace_main_menu(
    bot,
    *,
    chat_id: int,
    user_id: int,
    store: dict,
    text: str = "🏠 منوی اصلی:",
    reply_markup=None,
    parse_mode: str | None = None,
) -> int:
    """حذف پیام منوی قبلی (در صورت وجود) و ارسال منوی جدید؛ message_id جدید را برمی‌گرداند."""
    from config.settings import ADMIN_IDS
    from database.db import get_user
    from keyboards.menus import main_menu_inline_keyboard

    if user_id not in set(ADMIN_IDS or []) and get_user(user_id) is None:
        return await send_registration_welcome(bot, chat_id=chat_id, user_id=user_id, store=store)

    if reply_markup is None:
        reply_markup = main_menu_inline_keyboard
    await _delete_main_menu_anchor_message(bot, user_id=user_id, store=store)
    await strip_reply_keyboard(bot, chat_id=chat_id)
    kwargs = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode
    sent = await bot.send_message(**kwargs)
    set_main_menu_anchor(store, user_id, sent.chat_id, sent.message_id)
    return sent.message_id


def normalize_telegram_callback_data(data: str | None) -> str:
    """حذف فاصلهٔ ابتدا/انتها و کاراکترهای نامرئی که گاهی باعث عدم تطابق pattern می‌شوند."""
    if data is None:
        return ""
    s = str(data).strip()
    for ch in (
        "\ufeff",
        "\u200b",
        "\u200c",
        "\u200d",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202c",
        "\u2060",
    ):
        s = s.replace(ch, "")
    return s.strip()


def is_main_offers_callback(data: object) -> bool:
    """برای CallbackQueryHandler: دقیقاً main_offers (با تحمل کاراکترهای نامرئی)."""
    if not isinstance(data, str):
        return False
    return normalize_telegram_callback_data(data) == "main_offers"


def is_my_offers_close_callback(data: object) -> bool:
    if not isinstance(data, str):
        return False
    return normalize_telegram_callback_data(data) == "my_offers_close"
