from __future__ import annotations

from telegram import Message, ReplyKeyboardRemove


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


def remember_cleanup_id(store: dict, user_id: int, message_id: int | None, key: str) -> None:
    if not message_id:
        return
    user_bucket = store.setdefault(user_id, {})
    ids = user_bucket.setdefault(key, [])
    if message_id not in ids:
        ids.append(message_id)


async def cleanup_ids(bot, chat_id: int, ids: list[int]) -> None:
    for mid in ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass


# همان کلیدهای handlers/services.py، euro_flow، exchange_flow، start_flow
_EURO_FLOW_CLEANUP_KEY = "euro_cleanup_message_ids"
_EXCHANGE_FLOW_CLEANUP_KEY = "exchange_cleanup_message_ids"
_MAIN_EXTRA_CLEANUP_KEY = "main_cleanup_message_ids"


async def cleanup_transient_dm_messages(
    bot,
    *,
    chat_id: int,
    user_id: int,
    store: dict,
    context_user_data: dict | None,
    extra_message_ids: list[int] | None = None,
) -> None:
    """
    حذف پیام‌های موقت فلو (یورو، معاوضه، گام‌های ثبت‌نام/منو، ویزارد پیشنهاد).
    پیام‌های ثبت‌شده با register_offer_thread_message / کارت معامله را دست نمی‌زند.
    """
    ids: list[int] = []
    if extra_message_ids:
        for mid in extra_message_ids:
            try:
                ids.append(int(mid))
            except (TypeError, ValueError):
                pass
    if context_user_data:
        raw = context_user_data.get("offer_flow_mids")
        if isinstance(raw, list):
            for x in raw:
                if x is None:
                    continue
                try:
                    ids.append(int(x))
                except (TypeError, ValueError):
                    pass
        context_user_data.pop("offer_flow_mids", None)
        raw_in = context_user_data.get("offer_flow_input_mids")
        if isinstance(raw_in, list):
            for x in raw_in:
                if x is None:
                    continue
                try:
                    ids.append(int(x))
                except (TypeError, ValueError):
                    pass
        context_user_data.pop("offer_flow_input_mids", None)
    bucket = store.get(user_id)
    if isinstance(bucket, dict):
        for key in (_EURO_FLOW_CLEANUP_KEY, _EXCHANGE_FLOW_CLEANUP_KEY, _MAIN_EXTRA_CLEANUP_KEY):
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
        if mid not in seen:
            seen.add(mid)
            unique.append(mid)
    await cleanup_ids(bot, chat_id=chat_id, ids=unique)


# آخرین پیام «منوی اصلی» برای حذف/جایگزینی و جلوگیری از انباشت
MAIN_MENU_ANCHOR_KEY = "main_menu_anchor"


def set_main_menu_anchor(store: dict, user_id: int, chat_id: int, message_id: int) -> None:
    bucket = store.setdefault(user_id, {})
    bucket[MAIN_MENU_ANCHOR_KEY] = {"cid": int(chat_id), "mid": int(message_id)}


async def remove_main_menu_anchor_message(bot, *, user_id: int, store: dict) -> None:
    """حذف پیام منوی اصلی بدون ارسال منوی جدید (مثلاً هنگام انتظار برای ورودی کاربر)."""
    bucket = store.setdefault(user_id, {})
    old = bucket.pop(MAIN_MENU_ANCHOR_KEY, None)
    if old:
        try:
            await bot.delete_message(chat_id=int(old["cid"]), message_id=int(old["mid"]))
        except Exception:
            pass


def reset_flow_user_bucket(store: dict, user_id: int) -> None:
    """حداقل دادهٔ فلو را بساز؛ anchor منوی اصلی را نگه دار تا send_or_replace بتواند حباب قبلی را حذف کند."""
    prev = store.pop(user_id, None)
    bucket: dict = {"methods": [], "operation": ""}
    if prev:
        anchor = prev.get(MAIN_MENU_ANCHOR_KEY)
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
) -> int:
    """خوش‌آمد + قوانین و دکمهٔ پذیرش — بدون کلیک «ثبت‌نام»."""
    from telegram.constants import ParseMode

    from keyboards.menus import terms_inline_keyboard
    from messages import texts
    from models.enums import UserState

    bucket = store.setdefault(user_id, {})
    old = bucket.pop(MAIN_MENU_ANCHOR_KEY, None)
    if old:
        try:
            await bot.delete_message(chat_id=int(old["cid"]), message_id=int(old["mid"]))
        except Exception:
            pass
    try:
        rm = await bot.send_message(chat_id=chat_id, text="\u2060", reply_markup=ReplyKeyboardRemove())
        await rm.delete()
    except Exception:
        pass
    await bot.send_message(chat_id=chat_id, text=texts.WELCOME_MESSAGE)
    sent = await bot.send_message(
        chat_id=chat_id,
        text=texts.TERMS_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=terms_inline_keyboard,
    )
    bucket[MAIN_MENU_ANCHOR_KEY] = {"cid": sent.chat_id, "mid": sent.message_id}
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
    bucket = store.setdefault(user_id, {})
    old = bucket.pop(MAIN_MENU_ANCHOR_KEY, None)
    if old:
        try:
            await bot.delete_message(chat_id=int(old["cid"]), message_id=int(old["mid"]))
        except Exception:
            pass
    try:
        rm = await bot.send_message(chat_id=chat_id, text="\u2060", reply_markup=ReplyKeyboardRemove())
        await rm.delete()
    except Exception:
        pass
    kwargs = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode
    sent = await bot.send_message(**kwargs)
    bucket[MAIN_MENU_ANCHOR_KEY] = {"cid": sent.chat_id, "mid": sent.message_id}
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
