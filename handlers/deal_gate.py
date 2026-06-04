"""
handlers/deal_gate.py — تأیید نهایی دوطرفه پس از پذیرش پیشنهاد

EN: Final yes/no gate, reminders, admin actions, account collection, transcript logging.
FA: دروازه تأیید، یادآوری، اقدام ادمین، جمع حساب، آرشیو در DB.
"""

from __future__ import annotations

import html as html_module
import json
import logging
import os
import re
import tempfile
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config.settings import ADMIN_IDS, BANK_CARDS
from database.db import (
    deal_gate_active_for_user,
    deal_gate_append_buyer_receipt,
    deal_gate_append_seller_receipt,
    deal_gate_append_seller_toman_admin,
    deal_gate_buyer_receipt_list,
    deal_gate_confirm_seller_receipt_buyer,
    deal_gate_delete,
    deal_gate_seller_receipt_list,
    deal_gate_seller_toman_admin_list,
    deal_gate_get,
    deal_gate_list_for_admin,
    deal_gate_upsert,
    get_advert_offer_joined,
    get_euro_advert_by_rowid,
    get_user,
    negotiation_transcript_append_line,
    update_advert_offer_status,
    update_euro_advert_status,
)
from state import user_data_store
from utils.bank_cards import display_bank_title, format_bank_card_html, parse_bank_cards

logger = logging.getLogger(__name__)

_RTL = "\u200f"
_ACC_PENDING_KEY = "deal_acc_pending"
_DEAL_RCPT_KEY = "deal_rcpt_pending"
_DEAL_ADMIN_STOM_KEY = "deal_admin_stom_pending"
_ACCOUNT_PHOTO_MARKER = "📷 عکس حساب"
_REMINDER1_SEC = 3600
_REMINDER2_SEC = 7200
_HOURLY_SEC = 3600


def _parse_admin_notify_mids(gate: dict) -> dict[int, int]:
    raw = (gate.get("admin_notify_mids") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {int(k): int(v) for k, v in data.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _serialize_admin_notify_mids(mids: dict[int, int]) -> str:
    return json.dumps({str(k): int(v) for k, v in mids.items()})


def _parse_admin_notify_photo_mids(gate: dict) -> dict[int, dict[str, int]]:
    raw = (gate.get("admin_notify_photo_mids") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        out: dict[int, dict[str, int]] = {}
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            out[int(k)] = {str(pk): int(pv) for pk, pv in v.items()}
        return out
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _serialize_admin_notify_photo_mids(mids: dict[int, dict[str, int]]) -> str:
    return json.dumps(
        {str(k): {str(pk): int(pv) for pk, pv in v.items()} for k, v in mids.items()}
    )


def _account_text_is_photo_marker(text: str | None) -> bool:
    return bool(text and str(text).strip().startswith(_ACCOUNT_PHOTO_MARKER))


def _photo_caption_html(html: str, *, limit: int = 1024) -> str:
    if len(html) <= limit:
        return html
    return html[: limit - 1] + "…"


async def _delete_message_safe(bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(int(chat_id), int(message_id))
    except Exception:
        pass


async def _purge_legacy_admin_photo_replies(
    bot,
    gate: dict,
    recipients: list[int],
) -> None:
    """حذف پیام‌های reply قدیمی عکس حساب (قبل از ادغام در یک پیام)."""
    stored = _parse_admin_notify_photo_mids(gate)
    if not stored:
        return
    for chat_id in recipients:
        mids = stored.get(int(chat_id)) or {}
        for key in ("seller", "buyer"):
            mid = mids.get(key)
            if mid:
                await _delete_message_safe(bot, chat_id, int(mid))


def _admin_account_photo_file_ids(gate: dict) -> list[str]:
    """ترتیب آلبوم: خریدار، سپس فروشنده (عکس کارت فروشنده پایین)."""
    out: list[str] = []
    for acct_key, fid_key in (
        ("buyer_accounts_text", "buyer_accounts_photo_file_id"),
        ("seller_accounts_text", "seller_accounts_photo_file_id"),
    ):
        acct = (gate.get(acct_key) or "").strip()
        fid = (gate.get(fid_key) or "").strip()
        if fid and _account_text_is_photo_marker(acct):
            out.append(fid)
    return out


def _primary_admin_photo_file_id(gate: dict) -> str | None:
    """
    یک عکس برای پیام واحد: اگر فروشنده عکس فرستاده همان (پایین پیام)؛
    وگرنه عکس خریدار.
    """
    fids = _admin_account_photo_file_ids(gate)
    if not fids:
        return None
    if len(fids) == 1:
        return fids[0]
    seller_fid = (gate.get("seller_accounts_photo_file_id") or "").strip()
    seller_acct = (gate.get("seller_accounts_text") or "").strip()
    if seller_fid and _account_text_is_photo_marker(seller_acct):
        return seller_fid
    return fids[-1]


async def _edit_or_send_admin_notification(
    bot,
    *,
    chat_id: int,
    old_mid: int | None,
    admin_html: str,
    photo_fids: list[str],
    reply_markup,
    plain: str,
    log_offer_id: int,
) -> int | None:
    """یک پیام واحد برای ادمین: متن کامل، یا عکس(ها) با caption کامل."""
    caption = _photo_caption_html(admin_html)
    album_fids = photo_fids if len(photo_fids) > 1 else []
    single_fid = photo_fids[0] if len(photo_fids) == 1 else None

    if single_fid:
        fid = single_fid
        media = InputMediaPhoto(
            media=fid,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        if old_mid:
            try:
                await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=int(old_mid),
                    media=media,
                )
                if reply_markup is not None:
                    try:
                        await bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=int(old_mid),
                            reply_markup=reply_markup,
                        )
                    except Exception:
                        pass
                return int(old_mid)
            except BadRequest as e:
                if "message is not modified" in str(e).lower():
                    return int(old_mid)
            except TelegramError:
                pass
            await _delete_message_safe(bot, chat_id, old_mid)
        try:
            sent = await bot.send_photo(
                chat_id,
                photo=fid,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            return sent.message_id
        except TelegramError as e:
            logger.warning(
                "deal_admin_sync: photo send offer=%s chat=%s: %s",
                log_offer_id,
                chat_id,
                e,
            )
            return old_mid

    if album_fids:
        if old_mid:
            await _delete_message_safe(bot, chat_id, old_mid)
        media = [
            InputMediaPhoto(
                media=album_fids[0],
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        ]
        for fid in album_fids[1:]:
            media.append(InputMediaPhoto(media=fid))
        try:
            msgs = await bot.send_media_group(chat_id=chat_id, media=media)
            return msgs[0].message_id if msgs else old_mid
        except TelegramError as e:
            logger.warning(
                "deal_admin_sync: album send offer=%s chat=%s: %s",
                log_offer_id,
                chat_id,
                e,
            )
            return old_mid

    if old_mid:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(old_mid),
                text=admin_html,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            return int(old_mid)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return int(old_mid)
        except TelegramError:
            pass
        await _delete_message_safe(bot, chat_id, old_mid)

    try:
        sent = await bot.send_message(
            chat_id,
            admin_html,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return sent.message_id
    except BadRequest:
        try:
            sent = await bot.send_message(
                chat_id,
                plain,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            return sent.message_id
        except TelegramError as e2:
            logger.warning(
                "deal_admin_sync: send failed offer=%s chat=%s: %s",
                log_offer_id,
                chat_id,
                e2,
            )
            return old_mid
    except Forbidden:
        logger.warning(
            "deal_admin_sync: forbidden chat_id=%s (ادمین /start بزند)",
            chat_id,
        )
        return old_mid
    except TelegramError as e:
        logger.warning(
            "deal_admin_sync: send failed offer=%s chat=%s: %s",
            log_offer_id,
            chat_id,
            e,
        )
        return old_mid


async def sync_deal_admin_notification(
    bot,
    offer_id: int,
    *,
    deal_complete: bool = False,
) -> None:
    """
    ارسال یا ویرایش پیام ادمین برای معامله.
    پس از تأیید دوطرفه پیام اول ساخته می‌شود؛ با هر حساب جدید همان پیام edit می‌شود.
    """
    from handlers.offers import (
        _deal_admin_recipient_ids,
        _post_acceptance_admin_message_html,
    )

    gate = deal_gate_get(offer_id)
    row = get_advert_offer_joined(offer_id)
    if not gate or not row:
        return
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"]))
    if not advert:
        return

    oid = int(offer_id)
    seq = int(row.get("seq_in_advert") or oid)
    aid = int(row["advert_rowid"])
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    buyer_acct = (gate.get("buyer_accounts_text") or "").strip()
    seller_acct = (gate.get("seller_accounts_text") or "").strip()
    all_photo_fids = _admin_account_photo_file_ids(gate)
    if len(all_photo_fids) >= 2:
        photo_fids = all_photo_fids
    else:
        primary = _primary_admin_photo_file_id(gate)
        photo_fids = [primary] if primary else []
    accounts_mode = not deal_complete

    admin_html = _post_acceptance_admin_message_html(
        advert,
        row,
        seq,
        aid,
        buyer_accounts_text=buyer_acct or None,
        seller_accounts_text=seller_acct or None,
        accounts_status_mode=accounts_mode,
        deal_complete=deal_complete,
        embed_account_photos=bool(photo_fids),
        gate=gate,
    )
    recipients = _deal_admin_recipient_ids()
    if not recipients:
        logger.warning("deal_admin_sync: no recipients offer=%s", oid)
        return

    await _purge_legacy_admin_photo_replies(bot, gate, recipients)

    stored = _parse_admin_notify_mids(gate)
    updated = dict(stored)
    plain = re.sub(r"<[^>]+>", "", admin_html or "")
    if deal_complete:
        reply_markup = deal_admin_payment_actions_keyboard(oid, gate)
    elif accounts_mode:
        reply_markup = deal_admin_payment_actions_keyboard(oid, gate)
    else:
        reply_markup = None

    for chat_id in recipients:
        old_mid = stored.get(chat_id)
        new_mid = await _edit_or_send_admin_notification(
            bot,
            chat_id=chat_id,
            old_mid=old_mid,
            admin_html=admin_html,
            photo_fids=photo_fids,
            reply_markup=reply_markup,
            plain=plain,
            log_offer_id=oid,
        )
        if new_mid:
            updated[chat_id] = int(new_mid)
            logger.info(
                "deal_admin_sync: synced offer=%s chat_id=%s mid=%s photos=%s",
                oid,
                chat_id,
                new_mid,
                len(photo_fids),
            )

    upsert_fields: dict = {}
    if updated != stored:
        upsert_fields["admin_notify_mids"] = _serialize_admin_notify_mids(updated)
    if _parse_admin_notify_photo_mids(gate):
        upsert_fields["admin_notify_photo_mids"] = "{}"
    if upsert_fields:
        deal_gate_upsert(
            offer_id=oid,
            advert_rowid=aid,
            buyer_telegram_id=buyer_id,
            seller_telegram_id=seller_id,
            **upsert_fields,
        )


def _first_unconfirmed_seller_euro_index(offer_id: int) -> int | None:
    for i, r in enumerate(deal_gate_seller_receipt_list(offer_id)):
        if not int(r.get("buyer_confirmed_at") or 0):
            return i
    return None


async def _show_user_main_menu(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    *,
    text: str | None = None,
    parse_mode: str | None = None,
) -> None:
    from config.settings import ADMIN_IDS
    from keyboards.admin_home import admin_home_inline_keyboard
    from models.enums import UserState
    from utils.telegram_utils import send_or_replace_main_menu

    uid = int(user_id)
    rm = None
    if uid in set(ADMIN_IDS or []):
        rm = admin_home_inline_keyboard()
    await send_or_replace_main_menu(
        context.bot,
        chat_id=uid,
        user_id=uid,
        store=user_data_store,
        text=text or f"{_RTL}🏠 منوی اصلی:",
        parse_mode=parse_mode,
        reply_markup=rm,
    )
    try:
        context.application.user_data[uid]["state"] = UserState.MAIN_MENU.name
    except Exception:
        pass


def deal_admin_payment_actions_keyboard(
    offer_id: int, gate: dict | None = None
) -> InlineKeyboardMarkup:
    """دکمه‌های هماهنگی ادمین — وابسته به مرحلهٔ واریز."""
    from handlers.offers import _seller_euro_fully_confirmed_gate

    oid = int(offer_id)
    if gate is None:
        gate = deal_gate_get(oid) or {}
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "💳 ارسال کارت واریز تومان به خریدار",
                callback_data=f"adm|pay|{oid}",
            )
        ],
    ]
    card_sent = int(gate.get("buyer_toman_card_sent_at") or 0) > 0
    toman_settled = int(gate.get("buyer_toman_settled_at") or 0) > 0
    if card_sent and not toman_settled:
        rows.append(
            [
                InlineKeyboardButton(
                    "✅ تومان نشست",
                    callback_data=f"adm|tomset|{oid}",
                )
            ]
        )
    eur_sent = int(gate.get("seller_eur_account_sent_at") or 0) > 0
    if eur_sent:
        uidx = _first_unconfirmed_seller_euro_index(oid)
        if uidx is not None:
            rows.append(
                [
                    InlineKeyboardButton(
                        "✅ یورو نشست (ادمین)",
                        callback_data=f"adm|eurcfm|{oid}|{uidx}",
                    )
                ]
            )
    if _seller_euro_fully_confirmed_gate(gate):
        rows.append(
            [
                InlineKeyboardButton(
                    "📎 ارسال فیش واریزی تومان به فروشنده",
                    callback_data=f"adm|stom|{oid}|go",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "📋 پیام‌های ربات به طرفین",
                callback_data=f"adm|outlog|{oid}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def deal_admin_completed_keyboard(
    offer_id: int, gate: dict | None = None
) -> InlineKeyboardMarkup:
    return deal_admin_payment_actions_keyboard(offer_id, gate)


def _buyer_toman_pay_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📎 ارسال فیش واریزی",
                    callback_data=f"deal|rcpt|{oid}|go",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ انصراف",
                    callback_data=f"deal|rcpt|{oid}|cancel",
                )
            ],
        ]
    )


def _buyer_receipt_prompt_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ انصراف",
                    callback_data=f"deal|rcpt|{oid}|cancel",
                )
            ],
        ]
    )


def _seller_euro_pay_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📎 ارسال فیش واریزی یورو",
                    callback_data=f"deal|srcpt|{oid}|go",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ انصراف",
                    callback_data=f"deal|srcpt|{oid}|cancel",
                )
            ],
        ]
    )


def _seller_euro_receipt_prompt_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ انصراف",
                    callback_data=f"deal|srcpt|{oid}|cancel",
                )
            ],
        ]
    )


def _buyer_euro_settled_keyboard(offer_id: int, receipt_index: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    idx = int(receipt_index)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ یورو نشست",
                    callback_data=f"deal|eurset|{oid}|{idx}",
                )
            ],
        ]
    )


def _clear_deal_receipt_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_DEAL_RCPT_KEY, None)


def _track_rcpt_prompt_msg(
    store: dict, user_id: int, offer_id: int, message_id: int | None
) -> None:
    if not message_id:
        return
    b = store.setdefault(int(user_id), {})
    key = f"rcpt_ui_{int(offer_id)}"
    b.setdefault(key, []).append(int(message_id))


def _track_pay_card_msg(
    store: dict, user_id: int, offer_id: int, message_id: int | None
) -> None:
    if message_id:
        store.setdefault(int(user_id), {})[f"pay_card_{int(offer_id)}"] = int(
            message_id
        )


async def _purge_rcpt_prompt_msgs(
    bot, store: dict, user_id: int, offer_id: int
) -> None:
    uid = int(user_id)
    oid = int(offer_id)
    for mid in list(store.setdefault(uid, {}).pop(f"rcpt_ui_{oid}", []) or []):
        try:
            await bot.delete_message(uid, int(mid))
        except Exception:
            pass


async def _purge_buyer_pay_on_cancel(
    bot,
    store: dict,
    user_id: int,
    offer_id: int,
    gate: dict | None = None,
) -> None:
    uid = int(user_id)
    oid = int(offer_id)
    b = store.setdefault(uid, {})
    await _purge_rcpt_prompt_msgs(bot, store, uid, oid)
    pay_mid = b.pop(f"pay_card_{oid}", None)
    if pay_mid:
        try:
            await bot.delete_message(uid, int(pay_mid))
        except Exception:
            pass
    await _purge_user_deal_chat(bot, store, uid, oid, gate)


async def _notify_admins_deal_line(
    bot, *, offer_id: int, gate: dict, line_html: str
) -> None:
    from handlers.offers import _deal_admin_recipient_ids

    row = get_advert_offer_joined(offer_id)
    seq = int((row or {}).get("seq_in_advert") or offer_id)
    body = f"{_RTL}{line_html}\nپیشنهاد <b>{seq}</b>"
    stored = _parse_admin_notify_mids(gate)
    for chat_id in _deal_admin_recipient_ids():
        reply_to = stored.get(int(chat_id))
        try:
            await bot.send_message(
                int(chat_id),
                body,
                parse_mode=ParseMode.HTML,
                reply_to_message_id=int(reply_to) if reply_to else None,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning(
                "deal_notify_admin: chat=%s offer=%s: %s",
                chat_id,
                offer_id,
                e,
            )


async def _forward_buyer_receipt_to_admins(
    bot,
    *,
    offer_id: int,
    gate: dict,
    entry_type: str,
    text: str = "",
    file_id: str = "",
) -> None:
    from handlers.offers import _deal_admin_recipient_ids

    row = get_advert_offer_joined(offer_id)
    if not row:
        return
    seq = int(row.get("seq_in_advert") or offer_id)
    aid = int(row["advert_rowid"])
    n = len(deal_gate_buyer_receipt_list(offer_id))
    cap = (
        f"{_RTL}📎 <b>فیش واریز تومان — خریدار</b>\n"
        f"پیشنهاد <b>{seq}</b> · آگهی <b>{aid}</b> · مورد <b>{n}</b>"
    )
    if entry_type == "text" and text.strip():
        cap += f"\n\n<pre>{html_module.escape(text.strip()[:3500])}</pre>"

    stored = _parse_admin_notify_mids(gate)
    for chat_id in _deal_admin_recipient_ids():
        reply_to = stored.get(int(chat_id))
        try:
            if entry_type == "photo" and file_id:
                await bot.send_photo(
                    int(chat_id),
                    file_id,
                    caption=_photo_caption_html(cap),
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=int(reply_to) if reply_to else None,
                )
            else:
                await bot.send_message(
                    int(chat_id),
                    cap,
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=int(reply_to) if reply_to else None,
                    disable_web_page_preview=True,
                )
        except Exception as e:
            logger.warning(
                "deal_rcpt: forward to admin chat=%s offer=%s: %s",
                chat_id,
                offer_id,
                e,
            )


async def _forward_seller_receipt_to_admins(
    bot,
    *,
    offer_id: int,
    gate: dict,
    entry_type: str,
    text: str = "",
    file_id: str = "",
) -> None:
    from handlers.offers import _deal_admin_recipient_ids

    row = get_advert_offer_joined(offer_id)
    if not row:
        return
    seq = int(row.get("seq_in_advert") or offer_id)
    aid = int(row["advert_rowid"])
    n = len(deal_gate_seller_receipt_list(offer_id))
    cap = (
        f"{_RTL}📎 <b>فیش واریز یورو — فروشنده</b>\n"
        f"پیشنهاد <b>{seq}</b> · آگهی <b>{aid}</b> · مورد <b>{n}</b>"
    )
    if entry_type == "text" and text.strip():
        cap += f"\n\n<pre>{html_module.escape(text.strip()[:3500])}</pre>"

    stored = _parse_admin_notify_mids(gate)
    for chat_id in _deal_admin_recipient_ids():
        reply_to = stored.get(int(chat_id))
        try:
            if entry_type == "photo" and file_id:
                await bot.send_photo(
                    int(chat_id),
                    file_id,
                    caption=_photo_caption_html(cap),
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=int(reply_to) if reply_to else None,
                )
            else:
                await bot.send_message(
                    int(chat_id),
                    cap,
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=int(reply_to) if reply_to else None,
                    disable_web_page_preview=True,
                )
        except Exception as e:
            logger.warning(
                "deal_srcpt: forward to admin chat=%s offer=%s: %s",
                chat_id,
                offer_id,
                e,
            )


async def _notify_buyer_euro_receipt_confirm(
    bot,
    *,
    offer_id: int,
    gate: dict,
    receipt_index: int,
    entry_type: str,
    text: str = "",
    file_id: str = "",
) -> None:
    """کپی مخفیانه برای خریدار — بدون لاگ outbound؛ دکمهٔ تأیید نشستن."""
    buyer_id = int(gate.get("buyer_telegram_id") or 0)
    if not buyer_id:
        return
    row = get_advert_offer_joined(offer_id)
    seq = int((row or {}).get("seq_in_advert") or offer_id)
    body = (
        f"{_RTL}📎 <b>رسید واریز یورو</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b>\n\n"
        f"{_RTL}لطفاً بررسی کنید مبلغ به <b>حساب شما</b> نشسته باشد.\n"
        f"{_RTL}در صورت تأیید، دکمهٔ زیر را بزنید."
    )
    kb = _buyer_euro_settled_keyboard(offer_id, receipt_index)
    try:
        if entry_type == "photo" and file_id:
            cap = body
            if text.strip():
                cap += f"\n\n<i>{html_module.escape(text.strip()[:400])}</i>"
            await bot.send_photo(
                buyer_id,
                file_id,
                caption=_photo_caption_html(cap),
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            if text.strip():
                body += (
                    f"\n\n<pre>{html_module.escape(text.strip()[:2000])}</pre>"
                )
            await bot.send_message(
                buyer_id,
                body,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
    except Exception as e:
        logger.warning(
            "deal_srcpt: notify buyer=%s offer=%s idx=%s: %s",
            buyer_id,
            offer_id,
            receipt_index,
            e,
        )


def _deal_payment_cards_keyboard(offer_id: int, cards) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    oid = int(offer_id)
    for c in cards:
        btn_title = (display_bank_title(c.title) or c.title)[:28]
        pair.append(
            InlineKeyboardButton(btn_title, callback_data=f"adm|pay|{oid}|{c.id}")
        )
        if len(pair) >= 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm|pay|{oid}|back")]
    )
    return InlineKeyboardMarkup(rows)


def _deal_gate_allows_admin_payment(gate: dict | None) -> bool:
    st = ((gate or {}).get("gate_status") or "").strip().lower()
    return st in ("accounts", "completed")


async def deal_admin_payment_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ادمین: انتخاب کارت و ارسال حساب واریز تومان به خریدار."""
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    if q.from_user.id not in set(ADMIN_IDS or []):
        try:
            await q.answer("فقط ادمین", show_alert=True)
        except Exception:
            pass
        return

    parts = (q.data or "").split("|")
    if len(parts) < 3 or parts[0] != "adm" or parts[1] != "pay":
        return

    try:
        oid = int(parts[2])
    except (TypeError, ValueError):
        return

    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_admin_payment(gate):
        try:
            await q.answer("معامله در مرحلهٔ واریز نیست", show_alert=True)
        except Exception:
            pass
        return

    if len(parts) == 3:
        cards = parse_bank_cards(BANK_CARDS)
        if not cards:
            try:
                await q.answer("کارت بانکی در تنظیمات نیست", show_alert=True)
            except Exception:
                pass
            return
        try:
            await q.answer()
        except Exception:
            pass
        try:
            await q.message.edit_reply_markup(
                reply_markup=_deal_payment_cards_keyboard(oid, cards)
            )
        except BadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.warning("deal_pay: edit markup failed offer=%s: %s", oid, e)
        return

    action = parts[3]
    if action == "back":
        try:
            await q.answer()
        except Exception:
            pass
        try:
            await q.message.edit_reply_markup(
                reply_markup=deal_admin_payment_actions_keyboard(
                    oid, deal_gate_get(oid)
                )
            )
        except Exception:
            pass
        return

    cards = parse_bank_cards(BANK_CARDS)
    picked = next((c for c in cards if c.id == action), None)
    if not picked:
        try:
            await q.answer("کارت پیدا نشد", show_alert=True)
        except Exception:
            pass
        return

    await _admin_send_toman_deposit_card(context, q, oid, gate, picked)


async def _admin_send_toman_deposit_card(
    context: ContextTypes.DEFAULT_TYPE,
    q,
    oid: int,
    gate: dict,
    picked,
) -> None:
    row = get_advert_offer_joined(oid)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    if not row or not advert:
        try:
            await q.answer("پیشنهاد پیدا نشد", show_alert=True)
        except Exception:
            pass
        return

    buyer_id = int(gate["buyer_telegram_id"])
    if not buyer_id:
        try:
            await q.answer("خریدار نامشخص", show_alert=True)
        except Exception:
            pass
        return

    from handlers.offers import (
        _copyable_toman_html,
        _offer_effective_euro_amount,
        buyer_deposit_toman_amount,
    )

    pe_raw = int(row.get("proposed_euro_amount") or 0)
    pe_kw = pe_raw if pe_raw > 0 else None
    eur_amt = _offer_effective_euro_amount(advert, pe_kw)
    amount = buyer_deposit_toman_amount(advert, row)
    if amount < 1:
        try:
            await q.answer("مبلغ واریز صفر است", show_alert=True)
        except Exception:
            pass
        return

    seq = int(row.get("seq_in_advert") or oid)
    aid = int(row["advert_rowid"])
    card_html = format_bank_card_html(picked)

    msg = (
        f"{_RTL}💳 <b>حساب واریز تومان (امانت)</b>\n\n"
        f"{_RTL}آگهی <b>{aid}</b> · پیشنهاد <b>{seq}</b>\n"
        f"{_RTL}💶 <b>{eur_amt:,}</b> یورو\n\n"
        f"{_RTL}لطفاً مبلغ {_copyable_toman_html(amount)} تومان را "
        f"به حساب زیر واریز کنید:\n\n"
        f"{card_html}\n\n"
        f"{_RTL}📝 <b>توضیحات:</b>\n"
        f"{_RTL}• این مبلغ به‌صورت <b>امانت</b> نزد ادمین می‌ماند تا "
        f"فروشنده یورو را به حساب شما واریز کند.\n"
        f"{_RTL}• پس از واریز، دکمهٔ <b>ارسال فیش واریزی</b> را بزنید.\n"
        f"{_RTL}• تا تأیید ادمین، مبلغ دیگری واریز نکنید.\n"
    )
    recipient_id = buyer_id
    party_fa = "خریدار"
    party = "buyer"
    tag = "کارت واریز تومان به خریدار"

    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=int(gate["seller_telegram_id"]),
        buyer_toman_card_sent_at=int(time.time()),
    )
    gate = deal_gate_get(oid) or gate

    try:
        from utils.deal_outbound import deal_bot_send_message

        sent = await deal_bot_send_message(
            context.bot,
            offer_id=oid,
            chat_id=recipient_id,
            party=party,
            tag=tag,
            text=msg,
            reply_markup=_buyer_toman_pay_keyboard(oid),
            disable_web_page_preview=True,
        )
        _track_pay_card_msg(user_data_store, recipient_id, oid, sent.message_id)
    except Forbidden:
        try:
            await q.answer(
                f"{party_fa} ربات را بلاک کرده یا /start نزده",
                show_alert=True,
            )
        except Exception:
            pass
        return
    except TelegramError as e:
        logger.warning(
            "deal_pay: send to buyer=%s offer=%s: %s",
            recipient_id,
            oid,
            e,
        )
        try:
            await q.answer(f"ارسال به {party_fa} ناموفق بود", show_alert=True)
        except Exception:
            pass
        return

    _log(
        oid,
        f"ادمین حساب واریز ({picked.title}) برای {party_fa} ارسال کرد — {amount:,} تومان",
        from_role="admin",
    )
    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    try:
        await q.answer(f"✅ برای {party_fa} ارسال شد", show_alert=True)
    except Exception:
        pass
    gate = deal_gate_get(oid) or gate
    try:
        await q.message.edit_reply_markup(
            reply_markup=deal_admin_payment_actions_keyboard(oid, gate)
        )
    except Exception:
        pass
    await _show_user_main_menu(
        context,
        q.from_user.id,
        text=f"{_RTL}✅ کارت برای خریدار ارسال شد.",
        parse_mode=ParseMode.HTML,
    )


def _seller_euro_transfer_rules_html() -> str:
    """نکات واریز یورو برای فروشنده (پس از دریافت حساب خریدار)."""
    return (
        f"{_RTL}📝 <b>نکات واریز یورو:</b>\n"
        f"{_RTL}• <b>IBAN:</b> حتماً به‌صورت <b>آنی</b> واریز کنید.\n"
        f"{_RTL}• <b>PayPal:</b> حتماً <b>دوستانه و خانوادگی</b> "
        f"(Friends & Family).\n"
        f"{_RTL}• در قسمت <b>توضیحات / شرح / Notes</b> برای هیچ روشی "
        f"چیزی ننویسید.\n\n"
    )


def _seller_buyer_euro_account_message_html(
    *,
    aid: int,
    seq: int,
    eur_amt: int,
    buyer_acct: str,
) -> str:
    """متن ارسال حساب یوروی خریدار به فروشنده (هم‌سبک پیام خریدار)."""
    acct_block = f"<pre>{html_module.escape(buyer_acct)}</pre>\n\n"
    return (
        f"{_RTL}📤 <b>حساب دریافت یورو — خریدار</b>\n\n"
        f"{_RTL}آگهی <b>{aid}</b> · پیشنهاد <b>{seq}</b>\n"
        f"{_RTL}💶 <b>{eur_amt:,}</b> یورو\n\n"
        f"{_RTL}لطفاً <b>همین مقدار</b> یورو را به حساب زیر واریز کنید:\n\n"
        f"{acct_block}"
        f"{_seller_euro_transfer_rules_html()}"
        f"{_RTL}پس از انتقال، دکمهٔ <b>ارسال فیش واریزی یورو</b> را بزنید.\n"
        f"{_RTL}تا تأیید ادمین، مبلغ دیگری ارسال نکنید."
    )


async def _send_buyer_eur_account_to_seller(
    context: ContextTypes.DEFAULT_TYPE,
    oid: int,
    gate: dict,
    *,
    q=None,
) -> bool:
    """ارسال حساب یوروی خریدار به فروشنده (پس از تأیید تومان نشست)."""
    seller_id = int(gate.get("seller_telegram_id") or 0)
    if not seller_id:
        if q:
            await q.answer("فروشنده نامشخص", show_alert=True)
        return False

    buyer_acct = (gate.get("buyer_accounts_text") or "").strip()
    buyer_photo_fid = (gate.get("buyer_accounts_photo_file_id") or "").strip()
    has_text = bool(buyer_acct) and not _account_text_is_photo_marker(buyer_acct)
    has_photo = bool(buyer_photo_fid)
    if not has_text and not has_photo:
        if q:
            await q.answer(
                "خریدار هنوز حساب یورو را برای ربات نفرستاده",
                show_alert=True,
            )
        return False

    row = get_advert_offer_joined(oid)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    if not row or not advert:
        if q:
            await q.answer("پیشنهاد پیدا نشد", show_alert=True)
        return False

    from handlers.offers import _offer_effective_euro_amount

    pe_raw = int(row.get("proposed_euro_amount") or 0)
    pe_kw = pe_raw if pe_raw > 0 else None
    eur_amt = _offer_effective_euro_amount(advert, pe_kw)
    seq = int(row.get("seq_in_advert") or oid)
    aid = int(row["advert_rowid"])

    if int(gate.get("seller_eur_account_sent_at") or 0) > 0:
        return True

    photo_intro = (
        f"{_RTL}📤 <b>حساب دریافت یورو — خریدار</b>\n\n"
        f"{_RTL}آگهی <b>{aid}</b> · پیشنهاد <b>{seq}</b>\n"
        f"{_RTL}💶 <b>{eur_amt:,}</b> یورو\n\n"
        f"{_RTL}لطفاً <b>همین مقدار</b> یورو را به حساب زیر (عکس) واریز کنید:\n\n"
        f"{_seller_euro_transfer_rules_html()}"
        f"{_RTL}پس از انتقال، دکمهٔ <b>ارسال فیش واریزی یورو</b> را بزنید.\n"
        f"{_RTL}تا تأیید ادمین، مبلغ دیگری ارسال نکنید."
    )

    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=int(gate["buyer_telegram_id"]),
        seller_telegram_id=seller_id,
        seller_eur_account_sent_at=int(time.time()),
    )
    pay_kb = _seller_euro_pay_keyboard(oid)
    from utils.deal_outbound import deal_bot_send_message, deal_bot_send_photo

    tag = "حساب یوروی خریدار به فروشنده"
    try:
        if has_photo:
            cap = photo_intro
            if has_text:
                cap = _seller_buyer_euro_account_message_html(
                    aid=aid,
                    seq=seq,
                    eur_amt=eur_amt,
                    buyer_acct=buyer_acct,
                )
            sent = await deal_bot_send_photo(
                context.bot,
                offer_id=oid,
                chat_id=seller_id,
                party="seller",
                tag=tag,
                photo_file_id=buyer_photo_fid,
                caption=_photo_caption_html(cap),
                reply_markup=pay_kb,
            )
        else:
            body = _seller_buyer_euro_account_message_html(
                aid=aid,
                seq=seq,
                eur_amt=eur_amt,
                buyer_acct=buyer_acct,
            )
            sent = await deal_bot_send_message(
                context.bot,
                offer_id=oid,
                chat_id=seller_id,
                party="seller",
                tag=tag,
                text=body,
                disable_web_page_preview=True,
                reply_markup=pay_kb,
            )
        _track_pay_card_msg(user_data_store, seller_id, oid, sent.message_id)
    except Forbidden:
        if q:
            await q.answer(
                "فروشنده ربات را بلاک کرده یا /start نزده",
                show_alert=True,
            )
        return False
    except TelegramError as e:
        logger.warning(
            "deal_buyeur: send to seller=%s offer=%s: %s",
            seller_id,
            oid,
            e,
        )
        if q:
            await q.answer("ارسال به فروشنده ناموفق بود", show_alert=True)
        return False

    _log(oid, "حساب یوروی خریدار برای فروشنده ارسال شد", from_role="admin")
    return True


async def deal_admin_toman_settled_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ادمین: تومان نشست — ارسال خودکار حساب یورو به فروشنده."""
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    if q.from_user.id not in set(ADMIN_IDS or []):
        await q.answer("فقط ادمین", show_alert=True)
        return
    parts = (q.data or "").split("|")
    if len(parts) != 3 or parts[0] != "adm" or parts[1] != "tomset":
        return
    try:
        oid = int(parts[2])
    except (TypeError, ValueError):
        return
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_admin_payment(gate):
        await q.answer("معامله در این مرحله نیست", show_alert=True)
        return
    if not gate.get("buyer_toman_card_sent_at"):
        await q.answer("ابتدا کارت واریز به خریدار ارسال شود.", show_alert=True)
        return
    now = int(time.time())
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=int(gate["buyer_telegram_id"]),
        seller_telegram_id=int(gate["seller_telegram_id"]),
        buyer_toman_settled_at=now,
    )
    gate = deal_gate_get(oid) or gate
    _log(oid, "ادمین تأیید کرد: تومان نشست", from_role="admin")
    await _notify_admins_deal_line(
        context.bot,
        offer_id=oid,
        gate=gate,
        line_html="💵 <b>تومان نشست</b> — تأیید ادمین",
    )
    ok = await _send_buyer_eur_account_to_seller(context, oid, gate, q=q)
    if not ok:
        return
    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    await q.answer("✅ تومان نشست — حساب یورو برای فروشنده ارسال شد", show_alert=True)
    try:
        await q.message.edit_reply_markup(
            reply_markup=deal_admin_payment_actions_keyboard(
                oid, deal_gate_get(oid)
            )
        )
    except Exception:
        pass
    await _show_user_main_menu(
        context,
        q.from_user.id,
        text=f"{_RTL}✅ <b>تومان نشست</b> ثبت شد.",
        parse_mode=ParseMode.HTML,
    )


async def deal_admin_send_buyer_eur_account_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """دکمهٔ قدیمی — هدایت به «تومان نشست»."""
    q = update.callback_query
    if not q or not q.from_user:
        return
    if q.from_user.id not in set(ADMIN_IDS or []):
        await q.answer("فقط ادمین", show_alert=True)
        return
    parts = (q.data or "").split("|")
    if len(parts) != 3 or parts[0] != "adm" or parts[1] != "buyeur":
        return
    try:
        oid = int(parts[2])
    except (TypeError, ValueError):
        return
    gate = deal_gate_get(oid)
    if not gate:
        await q.answer("معامله پیدا نشد", show_alert=True)
        return
    if int(gate.get("buyer_toman_settled_at") or 0) > 0:
        await _send_buyer_eur_account_to_seller(context, oid, gate, q=q)
        await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
        await q.answer("حساب یورو قبلاً یا همین الان برای فروشنده ارسال شد", show_alert=True)
        return
    await q.answer(
        "از دکمهٔ «تومان نشست» استفاده کنید (پس از فیش خریدار).",
        show_alert=True,
    )


async def _apply_euro_settled(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    offer_id: int,
    receipt_index: int,
    confirmed_by: str,
    answer_query=None,
) -> None:
    gate = deal_gate_get(offer_id)
    if not gate:
        if answer_query:
            await answer_query.answer("معامله پیدا نشد", show_alert=True)
        return
    items = deal_gate_seller_receipt_list(offer_id)
    if not items:
        if answer_query:
            await answer_query.answer("فیشی ثبت نشده", show_alert=True)
        return
    if not deal_gate_confirm_seller_receipt_buyer(
        offer_id, receipt_index, confirmed_by=confirmed_by
    ):
        if answer_query:
            await answer_query.answer("فیش نامعتبر", show_alert=True)
        return
    gate = deal_gate_get(offer_id) or gate
    role = "buyer" if confirmed_by == "buyer" else "admin"
    who = "خریدار" if confirmed_by == "buyer" else "ادمین"
    _log(offer_id, "تأیید شد: یورو نشست", from_role=role)
    seller_id = int(gate.get("seller_telegram_id") or 0)
    row = get_advert_offer_joined(offer_id)
    seq = int((row or {}).get("seq_in_advert") or offer_id)
    if seller_id:
        from utils.deal_outbound import deal_bot_send_message

        try:
            await deal_bot_send_message(
                context.bot,
                offer_id=offer_id,
                chat_id=seller_id,
                party="seller",
                tag="تأیید نشستن یورو",
                text=(
                    f"{_RTL}✅ <b>یورو نشست</b>\n\n"
                    f"{_RTL}پیشنهاد <b>{seq}</b>\n"
                    f"{_RTL}{who} تأیید کرد مبلغ یورو به حساب خریدار "
                    f"واریز شده است."
                ),
                disable_web_page_preview=True,
            )
            await _show_user_main_menu(
                context,
                seller_id,
                text=f"{_RTL}✅ یورو نشست — منتظر واریز تومان از ادمین باشید.",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning(
                "deal_eurset: notify seller=%s offer=%s: %s",
                seller_id,
                offer_id,
                e,
            )
    await _notify_admins_deal_line(
        context.bot,
        offer_id=offer_id,
        gate=gate,
        line_html=f"💶 <b>یورو نشست</b> — تأیید {who}",
    )
    await sync_deal_admin_notification(context.bot, offer_id, deal_complete=True)
    if answer_query:
        try:
            await answer_query.answer("✅ یورو نشست ثبت شد", show_alert=True)
        except Exception:
            pass
        try:
            if answer_query.message:
                await answer_query.message.edit_reply_markup(reply_markup=None)
                note = f"{_RTL}✅ <b>تأیید شد:</b> یورو به حساب نشست."
                if answer_query.message.caption is not None:
                    cap = (answer_query.message.caption or "") + f"\n\n{note}"
                    await answer_query.message.edit_caption(
                        caption=cap[:1024],
                        parse_mode=ParseMode.HTML,
                    )
                elif answer_query.message.text:
                    await answer_query.message.edit_text(
                        (answer_query.message.text or "") + f"\n\n{note}",
                        parse_mode=ParseMode.HTML,
                    )
        except Exception:
            pass
        await _show_user_main_menu(
            context,
            answer_query.from_user.id,
            text=f"{_RTL}✅ تأیید یورو ثبت شد.",
            parse_mode=ParseMode.HTML,
        )


async def deal_admin_euro_settled_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ادمین: تأیید یورو نشست به‌جای خریدار."""
    q = update.callback_query
    if not q or not q.from_user:
        return
    if q.from_user.id not in set(ADMIN_IDS or []):
        await q.answer("فقط ادمین", show_alert=True)
        return
    parts = (q.data or "").split("|")
    if len(parts) != 4 or parts[0] != "adm" or parts[1] != "eurcfm":
        return
    try:
        oid = int(parts[2])
        ridx = int(parts[3])
    except (TypeError, ValueError):
        return
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_admin_payment(gate):
        await q.answer("معامله در این مرحله نیست", show_alert=True)
        return
    await _apply_euro_settled(
        context,
        offer_id=oid,
        receipt_index=ridx,
        confirmed_by="admin",
        answer_query=q,
    )
    try:
        if q.message:
            await q.message.edit_reply_markup(
                reply_markup=deal_admin_payment_actions_keyboard(
                    oid, deal_gate_get(oid)
                )
            )
    except Exception:
        pass


def _admin_seller_toman_prompt_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ انصراف",
                    callback_data=f"adm|stom|{oid}|cancel",
                )
            ],
        ]
    )


def _clear_deal_admin_stom_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_DEAL_ADMIN_STOM_KEY, None)


async def deal_admin_seller_toman_receipt_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ادمین: شروع ارسال فیش تومان به فروشنده."""
    q = update.callback_query
    if not q or not q.from_user:
        return
    if q.from_user.id not in set(ADMIN_IDS or []):
        await q.answer("فقط ادمین", show_alert=True)
        return
    parts = (q.data or "").split("|")
    if len(parts) < 4 or parts[0] != "adm" or parts[1] != "stom":
        return
    try:
        oid = int(parts[2])
    except (TypeError, ValueError):
        return
    action = parts[3]
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_admin_payment(gate):
        await q.answer("معامله در این مرحله نیست", show_alert=True)
        return
    from handlers.offers import _seller_euro_fully_confirmed_gate

    if not _seller_euro_fully_confirmed_gate(gate):
        await q.answer("ابتدا یورو باید نشست تأیید شود.", show_alert=True)
        return
    uid = q.from_user.id
    if action == "cancel":
        await q.answer("انصراف")
        _clear_deal_admin_stom_pending(context)
        await _purge_rcpt_prompt_msgs(context.bot, user_data_store, uid, oid)
        await _show_user_main_menu(context, uid)
        return
    if action != "go":
        await q.answer()
        return
    await q.answer()
    context.user_data[_DEAL_ADMIN_STOM_KEY] = {"offer_id": oid}
    try:
        sent = await context.bot.send_message(
            uid,
            f"{_RTL}📎 <b>فیش واریز تومان به فروشنده</b>\n\n"
            f"{_RTL}عکس یا متن فیش را بفرستید (چند فیش مجاز است).",
            parse_mode=ParseMode.HTML,
            reply_markup=_admin_seller_toman_prompt_keyboard(oid),
        )
        _track_rcpt_prompt_msg(user_data_store, uid, oid, sent.message_id)
    except Exception:
        logger.exception("deal_stom: prompt failed offer=%s", oid)


async def _deal_admin_stom_try_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if not update.message or not update.effective_user:
        return False
    pending = context.user_data.get(_DEAL_ADMIN_STOM_KEY)
    if not isinstance(pending, dict):
        return False
    if update.effective_user.id not in set(ADMIN_IDS or []):
        return False
    oid = int(pending.get("offer_id") or 0)
    gate = deal_gate_get(oid)
    if not gate:
        _clear_deal_admin_stom_pending(context)
        return False
    text = (update.message.text or "").strip()
    if not text or len(text) < 2:
        await update.message.reply_text(f"{_RTL}متن فیش را کامل‌تر بفرستید.")
        return True
    seller_id = int(gate.get("seller_telegram_id") or 0)
    deal_gate_append_seller_toman_admin(oid, entry_type="text", text=text)
    gate = deal_gate_get(oid) or gate
    row = get_advert_offer_joined(oid)
    seq = int((row or {}).get("seq_in_advert") or oid)
    from utils.deal_outbound import deal_bot_send_message

    body = (
        f"{_RTL}💳 <b>فیش واریز تومان</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b>\n\n"
        f"{_RTL}ادمین فیش واریز تومان به شما را ارسال کرد:\n\n"
        f"<pre>{html_module.escape(text[:3500])}</pre>"
    )
    try:
        await deal_bot_send_message(
            context.bot,
            offer_id=oid,
            chat_id=seller_id,
            party="seller",
            tag="فیش تومان از ادمین",
            text=body,
            disable_web_page_preview=True,
        )
        await _show_user_main_menu(
            context,
            seller_id,
            text=f"{_RTL}✅ فیش واریز تومان از ادمین دریافت شد.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning("deal_stom: send seller=%s: %s", seller_id, e)
    _log(oid, "ادمین فیش تومان برای فروشنده فرستاد (متن)", from_role="admin")
    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    uid = update.effective_user.id
    _track_rcpt_prompt_msg(user_data_store, uid, oid, update.message.message_id)
    _clear_deal_admin_stom_pending(context)
    await _purge_rcpt_prompt_msgs(context.bot, user_data_store, uid, oid)
    await _show_user_main_menu(
        context,
        uid,
        text=f"{_RTL}✅ فیش برای فروشنده ارسال شد.",
        parse_mode=ParseMode.HTML,
    )
    return True


async def _deal_admin_stom_try_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if not update.message or not update.effective_user:
        return False
    pending = context.user_data.get(_DEAL_ADMIN_STOM_KEY)
    if not isinstance(pending, dict):
        return False
    if update.effective_user.id not in set(ADMIN_IDS or []):
        return False
    oid = int(pending.get("offer_id") or 0)
    gate = deal_gate_get(oid)
    if not gate:
        _clear_deal_admin_stom_pending(context)
        return False
    fid = _extract_account_image_file_id(update.message)
    if not fid:
        return False
    cap = (update.message.caption or "").strip()
    seller_id = int(gate.get("seller_telegram_id") or 0)
    deal_gate_append_seller_toman_admin(
        oid, entry_type="photo", text=cap, file_id=fid
    )
    row = get_advert_offer_joined(oid)
    seq = int((row or {}).get("seq_in_advert") or oid)
    from utils.deal_outbound import deal_bot_send_photo

    body = (
        f"{_RTL}💳 <b>فیش واریز تومان</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b>\n\n"
        f"{_RTL}ادمین فیش واریز تومان به شما را ارسال کرد."
    )
    if cap:
        body += f"\n\n<i>{html_module.escape(cap[:400])}</i>"
    try:
        await deal_bot_send_photo(
            context.bot,
            offer_id=oid,
            chat_id=seller_id,
            party="seller",
            tag="فیش تومان از ادمین",
            photo_file_id=fid,
            caption=_photo_caption_html(body),
        )
        await _show_user_main_menu(
            context,
            seller_id,
            text=f"{_RTL}✅ فیش واریز تومان از ادمین دریافت شد.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning("deal_stom: photo to seller=%s: %s", seller_id, e)
    _log(oid, "ادمین فیش تومان برای فروشنده فرستاد (عکس)", from_role="admin")
    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    uid = update.effective_user.id
    _track_rcpt_prompt_msg(user_data_store, uid, oid, update.message.message_id)
    _clear_deal_admin_stom_pending(context)
    await _purge_rcpt_prompt_msgs(context.bot, user_data_store, uid, oid)
    await _show_user_main_menu(
        context,
        uid,
        text=f"{_RTL}✅ فیش برای فروشنده ارسال شد.",
        parse_mode=ParseMode.HTML,
    )
    return True


async def deal_admin_view_outbound_logs_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ادمین: بازپخش پیام‌های ذخیره‌شدهٔ ربات به خریدار/فروشنده."""
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    if q.from_user.id not in set(ADMIN_IDS or []):
        try:
            await q.answer("فقط ادمین", show_alert=True)
        except Exception:
            pass
        return

    parts = (q.data or "").split("|")
    if len(parts) != 3 or parts[0] != "adm" or parts[1] != "outlog":
        return

    try:
        oid = int(parts[2])
    except (TypeError, ValueError):
        return

    gate = deal_gate_get(oid)
    if not gate:
        try:
            await q.answer("معامله پیدا نشد", show_alert=True)
        except Exception:
            pass
        return

    try:
        await q.answer()
    except Exception:
        pass

    from utils.deal_outbound import deal_admin_replay_outbound

    ok = await deal_admin_replay_outbound(
        context.bot,
        int(q.message.chat_id),
        oid,
    )
    if not ok:
        try:
            await q.answer(
                "هنوز پیامی برای این معامله در لاگ نیست "
                "(فقط پیام‌های بعد از به‌روزرسانی ربات ذخیره می‌شوند).",
                show_alert=True,
            )
        except Exception:
            pass


def _log(offer_id: int, text: str, *, from_role: str = "system") -> None:
    negotiation_transcript_append_line(int(offer_id), from_role, text, max_lines=None)


def _account_collection_hint(*, is_buyer: bool, advert: dict | None) -> str:
    if is_buyer:
        methods = (advert.get("methods") or "").strip() if advert else "—"
        return (
            "دریافت یورو (طبق روش‌های آگهی):\n"
            f"{methods}\n"
            "مثال: PayPal، IBAN، Wise…"
        )
    return (
        "دریافت تومان:\n"
        "• نام و نام خانوادگی صاحب حساب\n"
        "• شماره شبا (IR…) — ترجیحاً\n"
        "• شماره کارت (در صورت نیاز)\n\n"
        "اگر امکان ارسال متن را ندارید، می‌توانید عکس واضح از کارت بفرستید."
    )


def _seller_account_collection_message_html(hint: str) -> str:
    """پیام درخواست حساب تومان برای فروشنده یورو."""
    return (
        f"{_RTL}✅ <b>هر دو طرف تأیید نهایی کردند.</b>\n\n"
        f"{_RTL}لطفاً اطلاعات حساب <b>دریافت تومان</b> را "
        f"به‌صورت <b>متن کامل</b> بفرستید:\n\n"
        f"<pre>{html_module.escape(hint)}</pre>\n"
        f"{_RTL}ℹ️ اگر فقط <b>شماره کارت</b> بفرستید، سقف «کارت‌به‌کارت» "
        f"معمولاً بیش از <b>۱۵ میلیون تومان</b> در روز نیست.\n"
        f"{_RTL}لطفاً <b>اولویت را روی شماره شبا</b> بگذارید. "
        f"از همراهی شما سپاسگزاریم."
    )


async def _purge_user_deal_chat(
    bot,
    store: dict,
    user_id: int,
    offer_id: int,
    gate: dict | None = None,
) -> None:
    """پاک کردن پیام‌های UI این معامله برای یک کاربر."""
    uid = int(user_id)
    oid = int(offer_id)
    b = store.setdefault(uid, {})
    b.pop(f"negp_{oid}", None)
    for mid in list(b.pop(f"ot_{oid}", []) or []):
        try:
            await bot.delete_message(chat_id=uid, message_id=int(mid))
        except Exception:
            pass
    if not gate:
        gate = deal_gate_get(oid)
    if not gate:
        return
    buyer_id = int(gate.get("buyer_telegram_id") or 0)
    seller_id = int(gate.get("seller_telegram_id") or 0)
    if uid == buyer_id and gate.get("buyer_gate_mid"):
        try:
            await bot.delete_message(uid, int(gate["buyer_gate_mid"]))
        except Exception:
            pass
    if uid == seller_id and gate.get("seller_gate_mid"):
        try:
            await bot.delete_message(uid, int(gate["seller_gate_mid"]))
        except Exception:
            pass


async def _notify_user_account_wait(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    *,
    deal_complete: bool,
    by_admin: bool = False,
    advert: dict | None = None,
    row: dict | None = None,
    offer_id: int | None = None,
) -> None:
    from utils.telegram_utils import send_or_replace_main_menu

    if deal_complete and advert and row:
        from handlers.offers import (
            _deal_complete_party_message_html,
            _deal_complete_reply_markup,
        )

        text = _deal_complete_party_message_html(advert, row, int(user_id))
        reply_markup = _deal_complete_reply_markup(advert)
    elif deal_complete:
        text = (
            f"{_RTL}✅ <b>اطلاعات معامله برای ادمین ارسال شد</b>\n\n"
            f"{_RTL}لطفاً صبور باشید؛ مراحل بعدی را ادمین هماهنگ می‌کند.\n\n"
            f"{_RTL}⚠️ <b>بدون هماهنگی ادمین واریز نکنید.</b>"
        )
        reply_markup = None
    elif by_admin:
        text = (
            f"{_RTL}✅ <b>ادمین اطلاعات حساب شما را ثبت کرد</b>\n\n"
            f"{_RTL}نیازی به ارسال مجدد حساب نیست.\n"
            f"{_RTL}پس از ثبت حساب طرف مقابل، مراحل بعدی را ادمین هماهنگ می‌کند."
        )
        reply_markup = None
    else:
        text = (
            f"{_RTL}✅ <b>اطلاعات حساب شما ثبت شد</b>\n\n"
            f"{_RTL}پس از ثبت حساب طرف مقابل، جزئیات برای ادمین ارسال می‌شود.\n"
            f"{_RTL}لطفاً منتظر مراحل بعدی باشید."
        )
        reply_markup = None
    if deal_complete and advert and row:
        log_tag = "پیام تکمیل معامله + منوی اصلی"
    elif deal_complete:
        log_tag = "انتظار هماهنگی ادمین (تکمیل)"
    elif by_admin:
        log_tag = "ثبت حساب توسط ادمین"
    else:
        log_tag = "ثبت حساب شما — انتظار طرف مقابل"
    await send_or_replace_main_menu(
        context.bot,
        chat_id=int(user_id),
        user_id=int(user_id),
        store=user_data_store,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    if offer_id:
        gate = deal_gate_get(int(offer_id))
        from utils.deal_outbound import deal_bot_log_text, party_for_uid

        deal_bot_log_text(
            int(offer_id),
            int(user_id),
            party_for_uid(gate, int(user_id)),
            log_tag,
            text,
        )
    if deal_complete:
        from models.enums import UserState

        try:
            context.application.user_data[int(user_id)]["state"] = (
                UserState.MAIN_MENU.name
            )
        except Exception:
            pass


async def _notify_user_other_party_account_ready(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    *,
    other_party_fa: str,
    offer_id: int,
) -> None:
    """طرفی که هنوز حساب نفرستاده — طرف مقابل (یا ادمین) حسابش ثبت شد."""
    msg = (
        f"{_RTL}ℹ️ <b>حساب {other_party_fa} ثبت شد.</b>\n\n"
        f"{_RTL}لطفاً اگر هنوز حساب خود را نفرستاده‌اید، "
        f"اطلاعات دریافت را <b>متنی</b> یا <b>عکس کارت</b> بفرستید."
    )
    try:
        gate = deal_gate_get(int(offer_id))
        from utils.deal_outbound import deal_bot_send_message, party_for_uid

        await deal_bot_send_message(
            context.bot,
            offer_id=int(offer_id),
            chat_id=int(user_id),
            party=party_for_uid(gate, int(user_id)),
            tag="ثبت حساب طرف مقابل",
            text=msg,
        )
    except Exception:
        logger.warning(
            "deal_gate: could not notify uid=%s other_party=%s ready",
            user_id,
            other_party_fa,
        )


def _track_deal_msg(store: dict, user_id: int, offer_id: int, message_id: int | None) -> None:
    from handlers.offers import register_offer_thread_message

    register_offer_thread_message(store, user_id, offer_id, message_id)


def _job_names(offer_id: int) -> tuple[str, str, str]:
    oid = int(offer_id)
    return (
        f"deal_r1_{oid}",
        f"deal_r2_{oid}",
        f"deal_hr_{oid}",
    )


def _cancel_gate_jobs(context: ContextTypes.DEFAULT_TYPE, offer_id: int) -> None:
    jq = getattr(context.application, "job_queue", None)
    if not jq:
        return
    for name in _job_names(offer_id):
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()


def _cancel_gate_reminder_jobs(context: ContextTypes.DEFAULT_TYPE, offer_id: int) -> None:
    jq = getattr(context.application, "job_queue", None)
    if not jq:
        return
    r1, r2, _hr = _job_names(offer_id)
    for name in (r1, r2):
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()


def _gate_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ بله", callback_data=f"deal|yes|{oid}"),
                InlineKeyboardButton("❌ خیر", callback_data=f"deal|no|{oid}"),
            ]
        ]
    )


def _admin_gate_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⏸ صبر کردن", callback_data=f"adm|dg|wait|{oid}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔄 فعال‌سازی مجدد آگهی",
                    callback_data=f"adm|dg|react|{oid}",
                ),
                InlineKeyboardButton(
                    "⛔ بستن معامله", callback_data=f"adm|dg|close|{oid}"
                ),
            ],
        ]
    )


def _party_role_label(advert: dict, row: dict, telegram_id: int) -> str:
    from handlers.offers import _offer_buyer_seller_telegram_ids

    buyer_id, seller_id = _offer_buyer_seller_telegram_ids(advert, row)
    if int(telegram_id) == int(buyer_id):
        return "خریدار یورو"
    if int(telegram_id) == int(seller_id):
        return "فروشنده یورو"
    return "کاربر"


def _gate_intro_html(advert: dict, row: dict, *, party_label: str) -> str:
    from handlers.offers import (
        _financial_party_summary_html,
        _offer_amount_line_html,
        _offer_effective_euro_amount,
        advert_public_link_html,
    )

    aid = int(row["advert_rowid"])
    seq = int(row.get("seq_in_advert") or row["id"])
    rate = int(row["rate_toman"])
    try:
        pe_raw = int(row.get("proposed_euro_amount") or 0)
    except (TypeError, ValueError):
        pe_raw = 0
    pe_kw = pe_raw if pe_raw > 0 else None
    eur_amt = _offer_effective_euro_amount(advert, pe_kw)
    op = (advert.get("operation") or "").strip()
    party_key = "buyer" if "خریدار" in party_label else "seller"
    fin = _financial_party_summary_html(advert, rate, eur_amt, party=party_key)
    ad_link = advert_public_link_html(advert, aid)
    amt_line = _offer_amount_line_html(advert, pe_kw)
    role_short = "خریدار" if "خریدار" in party_label else "فروشنده"
    return (
        f"{_RTL}🔐 <b>مرحلهٔ تأیید نهایی معامله</b>\n\n"
        f"{_RTL}صاحب آگهی پیشنهاد را پذیرفته است. قبل از هماهنگی پرداخت، "
        f"لطفاً یک‌بار دیگر مقدار و شرایط را تأیید کنید.\n\n"
        f"{_RTL}اگر <b>به اشتباه</b> پیشنهاد داده‌اید، پیشنهاد دیگری فعال دارید، "
        f"یا دیگر تمایلی ندارید — <b>خیر</b> بزنید.\n\n"
        f"{_RTL}با <b>بله</b> یعنی موافق انجام معامله با همین مشخصات هستید.\n"
        f"{_RTL}⚠️ توجه: اگر پس از تأیید، معامله را لغو کنید، دسترسی شما به ربات "
        f"<b>محدود</b> می‌شود. در صورت تکرار این رفتار، بار سوم "
        f"<b>برای همیشه</b> از استفاده از ربات محروم خواهید شد.\n\n"
        f"{_RTL}✅ پیشنهاد <b>{seq}</b> برای {ad_link}\n\n"
        f"{amt_line}\n"
        f"{_RTL}👤 نقش شما: <b>{html_module.escape(party_label)}</b>\n\n"
        f"{fin}"
        f"{_RTL}❓ آیا <b>{html_module.escape(role_short)}</b> با این مقدار یورو "
        f"و این شرایط/نرخ موافق هستید؟"
    )


def _status_snapshot(gate: dict) -> str:
    br = (gate.get("buyer_response") or "").strip().lower()
    sr = (gate.get("seller_response") or "").strip().lower()
    st = (gate.get("gate_status") or "").strip().lower()

    def _fa(r: str) -> str:
        if r == "yes":
            return "✅ تأیید کرد"
        if r == "no":
            return "❌ رد کرد"
        return "⏳ در انتظار پاسخ"

    lines = [
        f"خریدار: {_fa(br)}",
        f"فروشنده: {_fa(sr)}",
    ]
    if st == "accounts":
        ba = bool((gate.get("buyer_accounts_text") or "").strip())
        sa = bool((gate.get("seller_accounts_text") or "").strip())
        lines.append(f"حساب خریدار: {'✅ ارسال شد' if ba else '⏳ در انتظار'}")
        lines.append(f"حساب فروشنده: {'✅ ارسال شد' if sa else '⏳ در انتظار'}")
    return "\n".join(lines)


_GATE_STATUS_FA = {
    "pending": "⏳ تأیید نهایی",
    "accounts": "📝 جمع‌آوری حساب",
    "completed": "✅ تکمیل",
    "rejected": "❌ رد شده",
    "closed": "⛔ بسته",
}


def _deal_gate_admin_list_keyboard(gates: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for g in gates[:12]:
        oid = int(g["offer_id"])
        aid = int(g["advert_rowid"])
        st = (g.get("gate_status") or "").strip()
        label = f"📋 آگهی {aid} · offer {oid} ({st})"
        rows.append(
            [InlineKeyboardButton(label[:60], callback_data=f"adm|dgs|{oid}")]
        )
    rows.append(
        [InlineKeyboardButton("🔄 بروزرسانی", callback_data="adm|dgs")]
    )
    rows.append(
        [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="adm|panel")]
    )
    return InlineKeyboardMarkup(rows)


def _deal_gate_admin_detail_keyboard(offer_id: int, gate: dict | None = None) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    rows: list[list[InlineKeyboardButton]] = []
    st = ((gate or {}).get("gate_status") or "").strip().lower()
    if st == "accounts":
        rows.append(
            [
                InlineKeyboardButton(
                    "✏️ حساب خریدار",
                    callback_data=f"adm|dgs|bacc|{oid}",
                ),
                InlineKeyboardButton(
                    "✏️ حساب فروشنده",
                    callback_data=f"adm|dgs|sacc|{oid}",
                ),
            ]
        )
    rows.extend(list(_admin_gate_keyboard(oid).inline_keyboard))
    rows.append(
        [InlineKeyboardButton("🔙 لیست معاملات", callback_data="adm|dgs")]
    )
    rows.append(
        [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="adm|panel")]
    )
    return InlineKeyboardMarkup(rows)


def _deal_gate_admin_completed_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    rows = list(
        deal_admin_payment_actions_keyboard(oid, gate).inline_keyboard
    )
    rows.append(
        [
            InlineKeyboardButton(
                "🔄 بروزرسانی پیام ادمین",
                callback_data=f"adm|dgs|resync|{oid}",
            )
        ]
    )
    rows.append([InlineKeyboardButton("🔙 لیست معاملات", callback_data="adm|dgs")])
    rows.append([InlineKeyboardButton("🔙 پنل مدیریت", callback_data="adm|panel")])
    return InlineKeyboardMarkup(rows)


def build_admin_deal_list_html(gates: list[dict]) -> str:
    if not gates:
        return (
            f"{_RTL}📊 <b>وضعیت معاملات</b>\n\n"
            f"{_RTL}معاملهٔ فعال یا اخیر یافت نشد.\n"
            f"{_RTL}شماره <b>آگهی</b> یا <b>offer</b> را بفرستید تا جستجو شود."
        )
    parts = [
        f"{_RTL}📊 <b>وضعیت معاملات</b> ({len(gates)})\n",
        f"{_RTL}یک معامله را انتخاب کنید، یا شماره <b>آگهی / offer</b> بفرستید.\n",
    ]
    for g in gates:
        oid = int(g["offer_id"])
        aid = int(g["advert_rowid"])
        st = (g.get("gate_status") or "").strip().lower()
        st_lbl = _GATE_STATUS_FA.get(st, st or "—")
        snap = _status_snapshot(g).replace("\n", " · ")
        parts.append(
            f"\n{_RTL}• آگهی <b>{aid}</b> · offer <code>{oid}</code>\n"
            f"  {_RTL}{st_lbl}\n"
            f"  <code>{html_module.escape(snap)}</code>"
        )
    return "".join(parts)


def build_admin_deal_detail_html(
    gate: dict,
    row: dict | None = None,
    advert: dict | None = None,
) -> str:
    from handlers.offers import (
        _format_deal_party_identity_html,
        _offer_amount_line_html,
        advert_public_link_html,
    )

    oid = int(gate["offer_id"])
    aid = int(gate["advert_rowid"])
    st = (gate.get("gate_status") or "").strip().lower()
    st_lbl = _GATE_STATUS_FA.get(st, st or "—")
    snap = _status_snapshot(gate)
    buyer_id = int(gate.get("buyer_telegram_id") or 0)
    seller_id = int(gate.get("seller_telegram_id") or 0)

    hdr = (
        f"{_RTL}📊 <b>جزئیات معامله</b>\n\n"
        f"{_RTL}offer <code>{oid}</code> · آگهی <b>{aid}</b>\n"
        f"{_RTL}وضعیت: <b>{html_module.escape(st_lbl)}</b>\n\n"
        f"<pre>{html_module.escape(snap)}</pre>\n"
    )
    if row and advert:
        seq = int(row.get("seq_in_advert") or oid)
        try:
            pe_raw = int(row.get("proposed_euro_amount") or 0)
        except (TypeError, ValueError):
            pe_raw = 0
        pe_kw = pe_raw if pe_raw > 0 else None
        ad_link = advert_public_link_html(advert, aid)
        amt_line = _offer_amount_line_html(advert, pe_kw)
        hdr += (
            f"\n{_RTL}✅ پیشنهاد <b>{seq}</b> برای {ad_link}\n"
            f"{amt_line}\n"
        )

    body = ""
    if buyer_id:
        body += _format_deal_party_identity_html(buyer_id, title="خریدار یورو")
    if seller_id:
        body += _format_deal_party_identity_html(seller_id, title="فروشنده یورو")

    acct_parts: list[str] = []
    for label, key in (("خریدار", "buyer_accounts_text"), ("فروشنده", "seller_accounts_text")):
        raw = (gate.get(key) or "").strip()
        if raw:
            acct_parts.append(
                f"\n{_RTL}🏦 <b>حساب {label}</b>\n"
                f"<pre>{html_module.escape(raw[:2000])}</pre>"
            )
        elif st == "accounts":
            acct_parts.append(f"\n{_RTL}🏦 <b>حساب {label}:</b> ⏳ هنوز ارسال نشده")
    return hdr + body + "".join(acct_parts)


async def _admin_edit_or_send(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    q = update.callback_query
    if q and q.message:
        try:
            await q.answer()
        except Exception:
            pass
        context.user_data["adm_dash_cid"] = q.message.chat_id
        context.user_data["adm_dash_mid"] = q.message.message_id

    from handlers.admin import _admin_edit_dashboard

    ok = await _admin_edit_dashboard(
        context,
        context.bot,
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )
    if ok:
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return
    try:
        sent = await context.bot.send_message(
            chat_id=int(chat_id),
            text=text[:4096],
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        context.user_data["adm_dash_cid"] = chat_id
        context.user_data["adm_dash_mid"] = sent.message_id
    except Exception:
        logger.exception("deal_gate admin panel send failed")


async def admin_show_deal_gate_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    context.user_data["admin_deal_gate_browse"] = True
    gates = deal_gate_list_for_admin()
    text = build_admin_deal_list_html(gates)
    kb = _deal_gate_admin_list_keyboard(gates)
    await _admin_edit_or_send(update, context, text, kb)


async def admin_deal_gate_search_by_number(
    update: Update, context: ContextTypes.DEFAULT_TYPE, number: int
) -> None:
    """جستجوی معامله با شماره آگهی یا offer."""
    from database.db import deal_gate_lookup_for_admin

    context.user_data["admin_deal_gate_browse"] = True
    gate = deal_gate_lookup_for_admin(offer_id=number)
    if not gate:
        gate = deal_gate_lookup_for_admin(advert_rowid=number)
    if not gate:
        await _admin_edit_or_send(
            update,
            context,
            f"{_RTL}📊 <b>وضعیت معاملات</b>\n\n"
            f"{_RTL}معامله‌ای با شماره <code>{number}</code> پیدا نشد.\n"
            f"{_RTL}شماره <b>آگهی</b> یا <b>offer</b> را دوباره بفرستید.",
            _deal_gate_admin_list_keyboard(deal_gate_list_for_admin()),
        )
        return
    await admin_show_deal_gate_detail(update, context, int(gate["offer_id"]))


async def admin_show_deal_gate_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, offer_id: int
) -> None:
    context.user_data["admin_deal_gate_browse"] = True
    gate = deal_gate_get(offer_id)
    if not gate:
        q = update.callback_query
        if q:
            await q.answer("معامله پیدا نشد.", show_alert=True)
        return
    row = get_advert_offer_joined(offer_id)
    advert = (
        get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    )
    text = build_admin_deal_detail_html(gate, row, advert)
    st = (gate.get("gate_status") or "").strip().lower()
    if st in ("pending", "accounts"):
        kb = _deal_gate_admin_detail_keyboard(offer_id, gate)
    elif st == "completed":
        kb = _deal_gate_admin_completed_keyboard(offer_id)
    else:
        kb = _deal_gate_admin_list_keyboard([gate])
    await _admin_edit_or_send(update, context, text, kb)


async def admin_save_party_account(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    party: str,
    text: str,
    *,
    photo_file_id: str | None = None,
) -> str | None:
    """
    ثبت/ویرایش حساب توسط ادمین. None = موفق؛ در غیر این صورت پیام خطا.
    """
    raw = (text or "").strip()
    if len(raw) < 3:
        return "متن حساب خیلی کوتاه است (حداقل ۳ کاراکتر)."
    gate = deal_gate_get(offer_id)
    if not gate:
        return "معامله پیدا نشد."
    st = (gate.get("gate_status") or "").strip().lower()
    if st not in ("accounts", "pending"):
        return "این معامله دیگر در مرحلهٔ ثبت حساب نیست."
    if party not in ("buyer", "seller"):
        return "نقش نامعتبر."
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    key = "buyer_accounts_text" if party == "buyer" else "seller_accounts_text"
    photo_key = (
        "buyer_accounts_photo_file_id"
        if party == "buyer"
        else "seller_accounts_photo_file_id"
    )
    party_fa = "خریدار" if party == "buyer" else "فروشنده"
    oid = int(offer_id)
    upsert_fields: dict = {key: raw[:2000]}
    if photo_file_id:
        upsert_fields[photo_key] = photo_file_id
    elif not _account_text_is_photo_marker(raw):
        upsert_fields[photo_key] = None
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="accounts",
        **upsert_fields,
    )
    _log(
        oid,
        f"ادمین — حساب {party_fa}: {raw[:500]}",
        from_role="admin",
    )
    await sync_deal_admin_notification(context.bot, oid)
    gate = deal_gate_get(oid)
    both_done = bool(
        (gate.get("buyer_accounts_text") or "").strip()
        and (gate.get("seller_accounts_text") or "").strip()
    )
    party_uid = buyer_id if party == "buyer" else seller_id
    other_uid = seller_id if party == "buyer" else buyer_id
    other_party_fa = "فروشنده" if party == "buyer" else "خریدار"
    other_key = "seller_accounts_text" if party == "buyer" else "buyer_accounts_text"
    try:
        from handlers.offers import clear_offer_flow_user_data

        ud = context.application.user_data[party_uid]
        clear_offer_flow_user_data(ud)
    except Exception:
        pass
    if both_done:
        await _complete_deal(context, oid)
    else:
        await _notify_user_account_wait(
            context,
            party_uid,
            deal_complete=False,
            by_admin=True,
            offer_id=oid,
        )
        if other_uid and not (gate.get(other_key) or "").strip():
            await _notify_user_other_party_account_ready(
                context,
                other_uid,
                other_party_fa=other_party_fa,
                offer_id=oid,
            )
    return None


async def _send_gate_messages(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    offer_id: int,
    advert: dict,
    row: dict,
    buyer_id: int,
    seller_id: int,
) -> None:
    from handlers.offers import register_offer_thread_message

    bot = context.bot
    kb = _gate_keyboard(offer_id)
    from utils.deal_outbound import deal_bot_send_message

    buyer_mid = seller_mid = None
    if buyer_id:
        try:
            sent = await deal_bot_send_message(
                bot,
                offer_id=offer_id,
                chat_id=buyer_id,
                party="buyer",
                tag="درخواست تأیید نهایی",
                text=_gate_intro_html(advert, row, party_label="خریدار یورو"),
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            buyer_mid = sent.message_id
            register_offer_thread_message(
                user_data_store, buyer_id, offer_id, buyer_mid
            )
        except Exception:
            logger.exception("deal_gate: buyer msg offer=%s", offer_id)
    if seller_id:
        try:
            sent = await deal_bot_send_message(
                bot,
                offer_id=offer_id,
                chat_id=seller_id,
                party="seller",
                tag="درخواست تأیید نهایی",
                text=_gate_intro_html(advert, row, party_label="فروشنده یورو"),
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            seller_mid = sent.message_id
            register_offer_thread_message(
                user_data_store, seller_id, offer_id, seller_mid
            )
        except Exception:
            logger.exception("deal_gate: seller msg offer=%s", offer_id)
    deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=int(row["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        buyer_gate_mid=buyer_mid,
        seller_gate_mid=seller_mid,
    )


def _schedule_gate_jobs(context: ContextTypes.DEFAULT_TYPE, offer_id: int) -> None:
    jq = getattr(context.application, "job_queue", None)
    if not jq:
        logger.warning("deal_gate: JobQueue missing; reminders disabled")
        return
    oid = int(offer_id)
    r1, r2, hr = _job_names(oid)
    _cancel_gate_jobs(context, oid)
    jq.run_once(
        _job_reminder1,
        when=_REMINDER1_SEC,
        data={"offer_id": oid},
        name=r1,
    )
    jq.run_once(
        _job_reminder2_admin,
        when=_REMINDER2_SEC,
        data={"offer_id": oid},
        name=r2,
    )
    # یادآوری ساعتی به کاربر حذف شد — ادمین از پنل «وضعیت معاملات» می‌بیند.


async def start_deal_final_gate(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    offer_id: int,
    row: dict,
    advert: dict,
) -> None:
    from handlers.offers import _offer_buyer_seller_telegram_ids

    buyer_id, seller_id = _offer_buyer_seller_telegram_ids(advert, row)
    oid = int(offer_id)
    aid = int(row["advert_rowid"])
    seq = int(row.get("seq_in_advert") or oid)
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=aid,
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="pending",
    )
    _log(
        oid,
        f"شروع تأیید نهایی معامله — پیشنهاد #{seq} آگهی #{aid}",
    )
    await _send_gate_messages(
        context,
        offer_id=oid,
        advert=advert,
        row=row,
        buyer_id=buyer_id,
        seller_id=seller_id,
    )
    _schedule_gate_jobs(context, oid)


async def _job_reminder1(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    oid = int(data.get("offer_id") or 0)
    await _send_pending_reminder(context, oid, which=1)


async def _job_reminder2_admin(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    oid = int(data.get("offer_id") or 0)
    await _send_pending_reminder(context, oid, which=2)
    await _notify_admin_escalation(context, oid)


async def _job_hourly_status(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    oid = int(data.get("offer_id") or 0)
    gate = deal_gate_get(oid)
    if not gate:
        _cancel_gate_jobs(context, oid)
        return
    st = (gate.get("gate_status") or "").strip().lower()
    if st not in ("pending", "accounts"):
        _cancel_gate_jobs(context, oid)
        return
    snap = _status_snapshot(gate)
    parties = (
        (
            int(gate["buyer_telegram_id"]),
            "buyer_response",
            "seller_response",
            "buyer_accounts_text",
        ),
        (
            int(gate["seller_telegram_id"]),
            "seller_response",
            "buyer_response",
            "seller_accounts_text",
        ),
    )
    for uid, my_key, other_key, acct_key in parties:
        if not uid:
            continue
        my_resp = (gate.get(my_key) or "").strip().lower()
        other_resp = (gate.get(other_key) or "").strip().lower()
        if st == "accounts":
            if my_resp != "yes":
                continue
            if (gate.get(acct_key) or "").strip():
                continue
            footer = (
                f"{_RTL}لطفاً <b>اطلاعات حساب</b> را در یک پیام متنی ارسال کنید "
                f"(طبق راهنمای بالاتر)."
            )
        elif my_resp == "yes" and other_resp == "yes":
            continue
        elif my_resp == "yes":
            footer = f"{_RTL}منتظر تأیید طرف مقابل هستیم."
        elif my_resp in ("yes", "no"):
            continue
        else:
            footer = (
                f"{_RTL}لطفاً در پیام «تأیید نهایی» بالا "
                f"<b>بله</b> یا <b>خیر</b> بزنید."
            )
        try:
            await context.bot.send_message(
                uid,
                f"{_RTL}📊 <b>به‌روزرسانی وضعیت تأیید نهایی</b>\n\n"
                f"<pre>{html_module.escape(snap)}</pre>\n\n"
                f"{footer}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


async def _send_pending_reminder(
    context: ContextTypes.DEFAULT_TYPE, offer_id: int, *, which: int
) -> None:
    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "") != "pending":
        return
    oid = int(offer_id)
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=int(gate["buyer_telegram_id"]),
        seller_telegram_id=int(gate["seller_telegram_id"]),
        reminder_count=which,
    )
    _log(oid, f"یادآوری تأیید نهایی شماره {which} ارسال شد")
    for uid, key in (
        (int(gate["buyer_telegram_id"]), "buyer_response"),
        (int(gate["seller_telegram_id"]), "seller_response"),
    ):
        if not uid:
            continue
        r = (gate.get(key) or "").strip().lower()
        if r in ("yes", "no"):
            continue
        try:
            await context.bot.send_message(
                uid,
                f"{_RTL}⏰ <b>یادآوری {which}</b>\n\n"
                f"{_RTL}هنوز تأیید نهایی معامله را نزده‌اید.\n"
                f"{_RTL}لطفاً در پیام «تأیید نهایی» بالا <b>بله</b> یا <b>خیر</b> بزنید.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


async def _notify_admin_escalation(
    context: ContextTypes.DEFAULT_TYPE, offer_id: int
) -> None:
    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "") != "pending":
        return
    if int(gate.get("admin_escalated_at") or 0) > 0:
        return
    row = get_advert_offer_joined(offer_id)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    if not row or not advert:
        return
    from handlers.offers import (
        _format_deal_party_identity_html,
        _offer_buyer_seller_telegram_ids,
        _send_deal_admin_notifications,
    )

    buyer_id, seller_id = _offer_buyer_seller_telegram_ids(advert, row)
    oid = int(offer_id)
    aid = int(row["advert_rowid"])
    seq = int(row.get("seq_in_advert") or oid)
    snap = _status_snapshot(gate)
    now = int(time.time())
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=aid,
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        admin_escalated_at=now,
    )
    _log(oid, "اعلان به ادمین — گذشت ۲ ساعت از تأیید نهایی", from_role="admin")
    body = (
        f"{_RTL}⚠️ <b>تأیید نهایی معامله — نیاز به تصمیم ادمین</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b> · آگهی <b>{aid}</b>\n"
        f"{_RTL}بیش از ۲ ساعت از شروع تأیید نهایی گذشته.\n\n"
        f"<pre>{html_module.escape(snap)}</pre>\n\n"
        f"{_format_deal_party_identity_html(buyer_id, title='خریدار یورو')}\n"
        f"{_format_deal_party_identity_html(seller_id, title='فروشنده یورو')}\n"
        f"{_RTL}یکی از گزینه‌ها را انتخاب کنید:"
    )
    await _send_deal_admin_notifications(
        context.bot,
        body,
        log_tag=f"deal_gate_escalate|{oid}",
        reply_markup=_admin_gate_keyboard(oid),
    )


async def _finish_buyer_receipt_flow(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    uid: int,
    offer_id: int,
    gate: dict,
) -> None:
    _clear_deal_receipt_pending(context)
    await _purge_rcpt_prompt_msgs(context.bot, user_data_store, uid, offer_id)
    await _show_user_main_menu(
        context,
        uid,
        text=(
            f"{_RTL}✅ <b>فیش به ادمین رسید.</b>\n\n"
            f"{_RTL}برای فیش دیگر، دوباره «ارسال فیش واریزی» را بزنید."
        ),
        parse_mode=ParseMode.HTML,
    )


async def _finish_seller_receipt_flow(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    uid: int,
    offer_id: int,
) -> None:
    _clear_deal_receipt_pending(context)
    await _purge_rcpt_prompt_msgs(context.bot, user_data_store, uid, offer_id)
    await _show_user_main_menu(
        context,
        uid,
        text=(
            f"{_RTL}✅ <b>فیش یورو به ادمین رسید.</b>\n\n"
            f"{_RTL}برای فیش بعدی، «ارسال فیش واریزی یورو» را بزنید."
        ),
        parse_mode=ParseMode.HTML,
    )


async def _handle_deal_receipt_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    offer_id: int,
) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    uid = q.from_user.id
    gate = deal_gate_get(offer_id)
    if not gate or int(gate.get("buyer_telegram_id") or 0) != uid:
        await q.answer("فقط خریدار این معامله", show_alert=True)
        return
    if not gate.get("buyer_toman_card_sent_at"):
        await q.answer("ابتدا ادمین کارت واریز را ارسال کند.", show_alert=True)
        return

    if action == "cancel":
        await q.answer("انصراف")
        _clear_deal_receipt_pending(context)
        await _purge_buyer_pay_on_cancel(
            context.bot, user_data_store, uid, offer_id, gate
        )
        await _show_user_main_menu(context, uid)
        return

    if action != "go":
        await q.answer()
        return

    await q.answer()
    try:
        if q.message:
            await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    context.user_data[_DEAL_RCPT_KEY] = {
        "offer_id": int(offer_id),
        "party": "buyer",
    }
    try:
        sent = await context.bot.send_message(
            uid,
            f"{_RTL}📎 <b>ارسال فیش واریزی</b>\n\n"
            f"{_RTL}عکس یا متن فیش را بفرستید.\n"
            f"{_RTL}می‌توانید چند فیش جدا بفرستید؛ هر کدام برای ادمین می‌رود.",
            parse_mode=ParseMode.HTML,
            reply_markup=_buyer_receipt_prompt_keyboard(offer_id),
        )
        _track_rcpt_prompt_msg(user_data_store, uid, offer_id, sent.message_id)
    except Exception:
        logger.exception("deal_rcpt: prompt failed offer=%s uid=%s", offer_id, uid)


async def _handle_deal_seller_receipt_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    offer_id: int,
) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    uid = q.from_user.id
    gate = deal_gate_get(offer_id)
    if not gate or int(gate.get("seller_telegram_id") or 0) != uid:
        await q.answer("فقط فروشنده این معامله", show_alert=True)
        return
    if not gate.get("seller_eur_account_sent_at"):
        await q.answer(
            "ادمین هنوز «تومان نشست» را تأیید نکرده.",
            show_alert=True,
        )
        return

    if action == "cancel":
        await q.answer("انصراف")
        _clear_deal_receipt_pending(context)
        await _purge_buyer_pay_on_cancel(
            context.bot, user_data_store, uid, offer_id, gate
        )
        await _show_user_main_menu(context, uid)
        return

    if action != "go":
        await q.answer()
        return

    await q.answer()
    try:
        if q.message:
            await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    context.user_data[_DEAL_RCPT_KEY] = {
        "offer_id": int(offer_id),
        "party": "seller",
    }
    try:
        sent = await context.bot.send_message(
            uid,
            f"{_RTL}📎 <b>ارسال فیش واریزی یورو</b>\n\n"
            f"{_RTL}عکس یا متن فیش را بفرستید.\n"
            f"{_RTL}می‌توانید چند فیش جدا بفرستید؛ هر کدام برای ادمین "
            f"و تأیید خریدار می‌رود.",
            parse_mode=ParseMode.HTML,
            reply_markup=_seller_euro_receipt_prompt_keyboard(offer_id),
        )
        _track_rcpt_prompt_msg(user_data_store, uid, offer_id, sent.message_id)
    except Exception:
        logger.exception("deal_srcpt: prompt failed offer=%s uid=%s", offer_id, uid)


async def _handle_buyer_euro_settled_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    receipt_index: int,
) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    uid = q.from_user.id
    gate = deal_gate_get(offer_id)
    if not gate or int(gate.get("buyer_telegram_id") or 0) != uid:
        await q.answer("فقط خریدار این معامله", show_alert=True)
        return
    await _apply_euro_settled(
        context,
        offer_id=offer_id,
        receipt_index=receipt_index,
        confirmed_by="buyer",
        answer_query=q,
    )


async def _deal_receipt_try_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if not update.message or not update.effective_user:
        return False
    pending = context.user_data.get(_DEAL_RCPT_KEY)
    if not isinstance(pending, dict):
        return False
    uid = update.effective_user.id
    oid = int(pending.get("offer_id") or 0)
    party = (pending.get("party") or "buyer").strip().lower()
    gate = deal_gate_get(oid)
    if not gate:
        _clear_deal_receipt_pending(context)
        return False
    if party == "seller":
        if int(gate.get("seller_telegram_id") or 0) != uid:
            _clear_deal_receipt_pending(context)
            return False
    elif int(gate.get("buyer_telegram_id") or 0) != uid:
        _clear_deal_receipt_pending(context)
        return False
    text = (update.message.text or "").strip()
    if not text or len(text) < 2:
        await update.message.reply_text(f"{_RTL}متن فیش را کامل‌تر بفرستید.")
        return True
    if party == "seller":
        items = deal_gate_append_seller_receipt(oid, entry_type="text", text=text)
        gate = deal_gate_get(oid) or gate
        idx = len(items) - 1
        _log(oid, f"فیش یورو متنی فروشنده ({len(text)} کاراکتر)", from_role="seller")
        await _forward_seller_receipt_to_admins(
            context.bot,
            offer_id=oid,
            gate=gate,
            entry_type="text",
            text=text,
        )
        await _notify_buyer_euro_receipt_confirm(
            context.bot,
            offer_id=oid,
            gate=gate,
            receipt_index=idx,
            entry_type="text",
            text=text,
        )
        await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
        _track_rcpt_prompt_msg(
            user_data_store, uid, oid, update.message.message_id
        )
        await _finish_seller_receipt_flow(context, uid=uid, offer_id=oid)
        return True
    deal_gate_append_buyer_receipt(oid, entry_type="text", text=text)
    gate = deal_gate_get(oid) or gate
    _log(oid, f"فیش واریز متنی خریدار ({len(text)} کاراکتر)", from_role="buyer")
    await _forward_buyer_receipt_to_admins(
        context.bot,
        offer_id=oid,
        gate=gate,
        entry_type="text",
        text=text,
    )
    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    _track_rcpt_prompt_msg(
        user_data_store, uid, oid, update.message.message_id
    )
    await _finish_buyer_receipt_flow(
        context, uid=uid, offer_id=oid, gate=gate
    )
    return True


async def _deal_receipt_try_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if not update.message or not update.effective_user:
        return False
    pending = context.user_data.get(_DEAL_RCPT_KEY)
    if not isinstance(pending, dict):
        return False
    uid = update.effective_user.id
    oid = int(pending.get("offer_id") or 0)
    party = (pending.get("party") or "buyer").strip().lower()
    gate = deal_gate_get(oid)
    if not gate:
        _clear_deal_receipt_pending(context)
        return False
    if party == "seller":
        if int(gate.get("seller_telegram_id") or 0) != uid:
            _clear_deal_receipt_pending(context)
            return False
    elif int(gate.get("buyer_telegram_id") or 0) != uid:
        _clear_deal_receipt_pending(context)
        return False
    fid = _extract_account_image_file_id(update.message)
    if not fid:
        return False
    cap = (update.message.caption or "").strip()
    if party == "seller":
        items = deal_gate_append_seller_receipt(
            oid, entry_type="photo", text=cap, file_id=fid
        )
        gate = deal_gate_get(oid) or gate
        idx = len(items) - 1
        _log(oid, "فیش یورو عکس فروشنده", from_role="seller")
        await _forward_seller_receipt_to_admins(
            context.bot,
            offer_id=oid,
            gate=gate,
            entry_type="photo",
            text=cap,
            file_id=fid,
        )
        await _notify_buyer_euro_receipt_confirm(
            context.bot,
            offer_id=oid,
            gate=gate,
            receipt_index=idx,
            entry_type="photo",
            text=cap,
            file_id=fid,
        )
        await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
        _track_rcpt_prompt_msg(
            user_data_store, uid, oid, update.message.message_id
        )
        await _finish_seller_receipt_flow(context, uid=uid, offer_id=oid)
        return True
    deal_gate_append_buyer_receipt(
        oid, entry_type="photo", text=cap, file_id=fid
    )
    gate = deal_gate_get(oid) or gate
    _log(oid, "فیش واریز عکس خریدار", from_role="buyer")
    await _forward_buyer_receipt_to_admins(
        context.bot,
        offer_id=oid,
        gate=gate,
        entry_type="photo",
        text=cap,
        file_id=fid,
    )
    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    _track_rcpt_prompt_msg(
        user_data_store, uid, oid, update.message.message_id
    )
    await _finish_buyer_receipt_flow(
        context, uid=uid, offer_id=oid, gate=gate
    )
    return True


async def deal_gate_group0_text_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if await _deal_admin_stom_try_message(update, context):
        raise ApplicationHandlerStop
    if await _deal_receipt_try_message(update, context):
        raise ApplicationHandlerStop
    await deal_gate_accounts_router(update, context)


async def deal_gate_group0_photo_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if await _deal_admin_stom_try_photo(update, context):
        raise ApplicationHandlerStop
    if await _deal_receipt_try_photo(update, context):
        raise ApplicationHandlerStop
    await deal_gate_accounts_photo_router(update, context)


async def deal_gate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    parts = (q.data or "").strip().split("|")
    if len(parts) < 3:
        return
    if parts[0] == "deal" and parts[1] == "rcpt" and len(parts) >= 4:
        await _handle_deal_receipt_callback(
            update, context, parts[3], int(parts[2])
        )
    elif parts[0] == "deal" and parts[1] == "srcpt" and len(parts) >= 4:
        await _handle_deal_seller_receipt_callback(
            update, context, parts[3], int(parts[2])
        )
    elif parts[0] == "deal" and parts[1] == "eurset" and len(parts) >= 4:
        try:
            ridx = int(parts[3])
        except (TypeError, ValueError):
            return
        await _handle_buyer_euro_settled_callback(
            update, context, int(parts[2]), ridx
        )
    elif parts[0] == "deal" and parts[1] == "acc" and len(parts) >= 4:
        await _handle_account_confirm_callback(update, context, parts[2], int(parts[3]))
    elif parts[0] == "deal":
        await _handle_party_response(update, context, parts[1], int(parts[2]))
    elif parts[0] == "adm" and parts[1] == "dg" and len(parts) >= 4:
        await _handle_admin_decision(update, context, parts[2], int(parts[3]))


async def _handle_party_response(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    offer_id: int,
) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    uid = q.from_user.id
    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "") != "pending":
        await q.answer("این مرحله دیگر فعال نیست.", show_alert=True)
        return
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    if uid not in (buyer_id, seller_id):
        await q.answer()
        return
    is_buyer = uid == buyer_id
    resp = "yes" if action == "yes" else "no" if action == "no" else ""
    if resp not in ("yes", "no"):
        await q.answer()
        return
    row = get_advert_offer_joined(offer_id)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    party = "خریدار" if is_buyer else "فروشنده"
    role_key = "buyer_response" if is_buyer else "seller_response"
    ts_key = "buyer_confirmed_at" if is_buyer else "seller_confirmed_at"
    now = int(time.time())
    fields = {
        role_key: resp,
        ts_key: now,
    }
    deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        **fields,
    )
    _log(
        offer_id,
        f"{party} یورو: {'تأیید نهایی (بله)' if resp == 'yes' else 'رد نهایی (خیر)'}",
        from_role="buyer" if is_buyer else "seller",
    )
    await q.answer("ثبت شد.")
    try:
        if q.message:
            await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if resp == "no":
        await _on_gate_rejected(context, offer_id, rejector_id=uid, party=party)
        return

    gate = deal_gate_get(offer_id)
    br = (gate.get("buyer_response") or "").strip().lower()
    sr = (gate.get("seller_response") or "").strip().lower()
    if br == "yes" and sr == "yes":
        await _on_both_yes(context, offer_id, row, advert)
        return
    other_id = seller_id if is_buyer else buyer_id
    other_party = "فروشنده" if is_buyer else "خریدار"
    other_r = sr if is_buyer else br
    try:
        sent = await context.bot.send_message(
            uid,
            f"{_RTL}✅ تأیید شما ثبت شد.\n"
            f"{_RTL}منتظر تأیید <b>{other_party} یورو</b> هستیم.",
            parse_mode=ParseMode.HTML,
        )
        _track_deal_msg(user_data_store, uid, offer_id, sent.message_id)
    except Exception:
        pass
    if other_r == "yes":
        await _on_both_yes(context, offer_id, row, advert)
    elif other_r != "no":
        try:
            sent_o = await context.bot.send_message(
                other_id,
                f"{_RTL}ℹ️ {party} یورو تأیید نهایی را زد.\n"
                f"{_RTL}لطفاً شما هم در پیام «تأیید نهایی» بله یا خیر بزنید.",
                parse_mode=ParseMode.HTML,
            )
            _track_deal_msg(user_data_store, other_id, offer_id, sent_o.message_id)
        except Exception:
            pass


async def _on_both_yes(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    row: dict | None,
    advert: dict | None,
) -> None:
    gate = deal_gate_get(offer_id)
    if not gate:
        return
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="accounts",
    )
    _log(offer_id, "هر دو طرف تأیید نهایی (بله) زدند — جمع‌آوری حساب")
    _cancel_gate_reminder_jobs(context, offer_id)
    try:
        from handlers.offers import clear_offer_flow_user_data
        from utils.telegram_utils import reset_flow_user_bucket

        for party_uid in (buyer_id, seller_id):
            if not party_uid:
                continue
            try:
                ud = context.application.user_data[party_uid]
                clear_offer_flow_user_data(ud)
            except Exception:
                logger.exception(
                    "deal_gate: clear offer flow failed uid=%s offer=%s",
                    party_uid,
                    offer_id,
                )
            reset_flow_user_bucket(user_data_store, int(party_uid))
    except Exception:
        logger.exception("deal_gate: clear offer flow failed offer=%s", offer_id)
    for uid, is_buyer in (
        (buyer_id, True),
        (seller_id, False),
    ):
        if not uid:
            continue
        hint = _account_collection_hint(is_buyer=is_buyer, advert=advert)
        if is_buyer:
            body = (
                f"{_RTL}✅ <b>هر دو طرف تأیید نهایی کردند.</b>\n\n"
                f"{_RTL}لطفاً اطلاعات حساب دریافت یورو را "
                f"<b>متنی</b> یا <b>عکس کارت/حساب</b> بفرستید:\n\n"
                f"<pre>{html_module.escape(hint)}</pre>"
            )
        else:
            body = _seller_account_collection_message_html(hint)
        try:
            from utils.deal_outbound import deal_bot_send_message

            tag = "درخواست ارسال حساب (پس از تأیید نهایی)"
            party = "buyer" if is_buyer else "seller"
            sent = await deal_bot_send_message(
                context.bot,
                offer_id=offer_id,
                chat_id=uid,
                party=party,
                tag=tag,
                text=body,
            )
            _track_deal_msg(user_data_store, uid, offer_id, sent.message_id)
        except Exception:
            pass
    await sync_deal_admin_notification(context.bot, offer_id)
    _log(offer_id, "اعلان اولیه معامله برای ادمین (پس از تأیید دوطرفه)")


async def _on_gate_rejected(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    *,
    rejector_id: int,
    party: str,
) -> None:
    gate = deal_gate_get(offer_id)
    if not gate:
        return
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="rejected",
    )
    update_advert_offer_status(offer_id, "gate_rejected")
    _cancel_gate_jobs(context, offer_id)
    _log(offer_id, f"معامله متوقف شد — {party} «خیر» زد")
    msg = (
        f"{_RTL}❌ <b>تأیید نهایی لغو شد</b>\n\n"
        f"{_RTL}<b>{party} یورو</b> «خیر» زد.\n"
        f"{_RTL}معامله متوقف شد؛ ادمین مطلع می‌شود."
    )
    from utils.deal_outbound import deal_bot_send_message, party_for_uid

    for uid in (buyer_id, seller_id):
        if not uid:
            continue
        try:
            await deal_bot_send_message(
                context.bot,
                offer_id=offer_id,
                chat_id=uid,
                party=party_for_uid(gate, uid),
                tag="رد تأیید نهایی",
                text=msg,
            )
        except Exception:
            pass
    row = get_advert_offer_joined(offer_id)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    if row and advert:
        from handlers.offers import (
            _format_deal_party_identity_html,
            _send_deal_admin_notifications,
        )

        aid = int(row["advert_rowid"])
        body = (
            f"{_RTL}❌ <b>رد تأیید نهایی</b> · آگهی <b>{aid}</b>\n\n"
            f"{_RTL}{party} یورو «خیر» زد.\n\n"
            f"{_format_deal_party_identity_html(buyer_id, title='خریدار')}\n"
            f"{_format_deal_party_identity_html(seller_id, title='فروشنده')}"
        )
        await _send_deal_admin_notifications(
            context.bot, body, log_tag=f"deal_gate_no|{offer_id}"
        )


async def _handle_admin_decision(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    offer_id: int,
) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    if q.from_user.id not in set(ADMIN_IDS or []):
        await q.answer("فقط ادمین.", show_alert=True)
        return
    gate = deal_gate_get(offer_id)
    if not gate:
        await q.answer("معامله پیدا نشد.", show_alert=True)
        return
    row = get_advert_offer_joined(offer_id)
    if not row:
        await q.answer("پیشنهاد پیدا نشد.", show_alert=True)
        return
    aid = int(row["advert_rowid"])
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])

    if action == "wait":
        deal_gate_upsert(
            offer_id=offer_id,
            advert_rowid=aid,
            buyer_telegram_id=buyer_id,
            seller_telegram_id=seller_id,
            admin_decision="wait",
        )
        _log(offer_id, "ادمین: صبر کردن", from_role="admin")
        await q.answer("منتظر می‌مانیم.")
        try:
            if q.message:
                await q.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if action == "react":
        await _reactivate_advert(context, offer_id, gate, row, q)
        return

    if action == "close":
        await _close_deal(context, offer_id, gate, row, q)
        return

    await q.answer()


async def _purge_gate_ui(
    context: ContextTypes.DEFAULT_TYPE, gate: dict, offer_id: int
) -> None:
    from handlers.offers import purge_offer_thread_messages

    buyer_id = int(gate.get("buyer_telegram_id") or 0)
    seller_id = int(gate.get("seller_telegram_id") or 0)
    row = get_advert_offer_joined(offer_id)
    owner = int(row["owner_id"]) if row else buyer_id
    proposer = int(row["proposer_telegram_id"]) if row else seller_id
    await purge_offer_thread_messages(
        context.bot,
        user_data_store,
        owner,
        proposer,
        offer_id,
    )


async def _reactivate_advert(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    gate: dict,
    row: dict,
    q,
) -> None:
    aid = int(row["advert_rowid"])
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    update_advert_offer_status(offer_id, "gate_aborted")
    update_euro_advert_status(aid, "فعال")
    _log(offer_id, "ادمین: فعال‌سازی مجدد آگهی", from_role="admin")
    await _purge_gate_ui(context, gate, offer_id)
    _cancel_gate_jobs(context, offer_id)
    deal_gate_delete(offer_id)
    from handlers.offers import refresh_advert_channel_post

    await refresh_advert_channel_post(context.bot, aid)
    note = (
        f"{_RTL}🔄 ادمین آگهی <b>{aid}</b> را دوباره فعال کرد.\n"
        f"{_RTL}تأیید نهایی قبلی لغو شد؛ می‌توانید پیشنهاد جدید دهید."
    )
    for uid in (buyer_id, seller_id):
        if uid:
            try:
                await context.bot.send_message(uid, note, parse_mode=ParseMode.HTML)
            except Exception:
                pass
    await q.answer("آگهی فعال شد.")
    try:
        if q.message:
            await q.message.edit_text(
                f"{_RTL}✅ آگهی {aid} دوباره فعال شد.",
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        pass


async def _close_deal(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    gate: dict,
    row: dict,
    q,
) -> None:
    aid = int(row["advert_rowid"])
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    update_advert_offer_status(offer_id, "gate_closed")
    update_euro_advert_status(aid, "بسته")
    deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=aid,
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="closed",
    )
    _log(offer_id, "ادمین: بستن معامله و آگهی", from_role="admin")
    await _purge_gate_ui(context, gate, offer_id)
    _cancel_gate_jobs(context, offer_id)
    note = f"{_RTL}⛔ معامله و آگهی <b>{aid}</b> توسط ادمین بسته شد."
    for uid in (buyer_id, seller_id):
        if uid:
            try:
                await context.bot.send_message(uid, note, parse_mode=ParseMode.HTML)
            except Exception:
                pass
    await q.answer("بسته شد.")
    try:
        if q.message:
            await q.message.edit_text(
                f"{_RTL}⛔ معامله بسته شد.",
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        pass


def _account_confirm_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ درست است — ثبت",
                    callback_data=f"deal|acc|ok|{oid}",
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ ویرایش دستی (متن)",
                    callback_data=f"deal|acc|edit|{oid}",
                ),
                InlineKeyboardButton(
                    "❌ انصراف",
                    callback_data=f"deal|acc|cancel|{oid}",
                ),
            ],
        ]
    )


def _clear_account_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data is not None:
        context.user_data.pop(_ACC_PENDING_KEY, None)


async def _download_account_image(bot, message) -> str | None:
    if not message:
        return None
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        mt = (message.document.mime_type or "").lower()
        if mt.startswith("image/"):
            file_id = message.document.file_id
    if not file_id:
        return None
    f = await bot.get_file(file_id)
    tmp_dir = tempfile.gettempdir()
    path = os.path.join(tmp_dir, f"deal_acc_{file_id}.jpg")
    try:
        await f.download_to_drive(custom_path=path)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    except Exception as e:
        logger.warning("deal_gate: image download failed: %s", e)
    try:
        path2 = await f.download_to_drive()
        if path2 and os.path.isfile(path2) and os.path.getsize(path2) > 0:
            return str(path2)
    except Exception as e:
        logger.warning("deal_gate: image download fallback failed: %s", e)
    return None


def _account_photo_saved_text(*, extra_caption: str = "") -> str:
    base = f"{_ACCOUNT_PHOTO_MARKER} (ثبت‌شده)"
    extra = (extra_caption or "").strip()
    if extra:
        return f"{extra}\n\n{base}"[:2000]
    return base[:2000]


def _extract_account_image_file_id(message) -> str | None:
    if message.photo:
        return str(message.photo[-1].file_id)
    doc = message.document
    if doc and doc.mime_type and str(doc.mime_type).startswith("image/"):
        return str(doc.file_id)
    return None


def _message_has_account_image(message) -> bool:
    if message.photo:
        return True
    doc = message.document
    if doc and doc.mime_type and str(doc.mime_type).startswith("image/"):
        return True
    return False


async def _commit_party_account(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    gate: dict,
    uid: int,
    text: str,
    user_message_id: int | None = None,
    photo_file_id: str | None = None,
) -> None:
    """ثبت حساب کاربر و به‌روزرسانی پیام ادمین."""
    oid = int(gate["offer_id"])
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    is_buyer = uid == buyer_id
    key = "buyer_accounts_text" if is_buyer else "seller_accounts_text"
    photo_key = (
        "buyer_accounts_photo_file_id"
        if is_buyer
        else "seller_accounts_photo_file_id"
    )
    party = "خریدار" if is_buyer else "فروشنده"

    upsert_fields: dict = {key: text[:2000]}
    if photo_file_id:
        upsert_fields[photo_key] = photo_file_id
    elif not _account_text_is_photo_marker(text):
        upsert_fields[photo_key] = None
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="accounts",
        **upsert_fields,
    )
    _log(oid, f"حساب {party}: {text[:500]}", from_role="buyer" if is_buyer else "seller")
    logger.info(
        "deal_gate: account saved offer=%s uid=%s party=%s",
        oid,
        uid,
        party,
    )

    gate = deal_gate_get(oid) or gate
    both_done = bool(
        (gate.get("buyer_accounts_text") or "").strip()
        and (gate.get("seller_accounts_text") or "").strip()
    )

    await _purge_user_deal_chat(context.bot, user_data_store, uid, oid, gate)
    if user_message_id:
        try:
            await context.bot.delete_message(uid, user_message_id)
        except Exception:
            pass

    await sync_deal_admin_notification(context.bot, oid)

    if both_done:
        await _complete_deal(context, oid)
    else:
        await _notify_user_account_wait(
            context, uid, deal_complete=False, offer_id=oid
        )


async def admin_deal_gate_account_photo_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ادمین: ثبت حساب خریدار/فروشنده — عکس در خلاصهٔ معامله برای ادمین."""
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    if uid not in set(ADMIN_IDS or []):
        return
    from models.enums import UserState

    state = (context.user_data.get("state") or "").strip()
    if state != UserState.ADMIN_DEAL_GATE_ACCOUNT.name:
        return
    party = (context.user_data.get("admin_deal_acc_party") or "").strip()
    if party not in ("buyer", "seller"):
        return
    try:
        oid = int(context.user_data.get("admin_deal_acc_offer_id"))
    except (TypeError, ValueError):
        return

    if not _message_has_account_image(update.message):
        await update.message.reply_text(
            f"{_RTL}❌ فقط عکس (JPG/PNG) بفرستید، یا اطلاعات را به‌صورت متن بنویسید."
        )
        raise ApplicationHandlerStop

    gate = deal_gate_get(oid)
    if not gate:
        await update.message.reply_text(f"{_RTL}❌ معامله پیدا نشد.")
        raise ApplicationHandlerStop

    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    caption = (update.message.caption or "").strip()
    saved_text = _account_photo_saved_text(extra_caption=caption)
    photo_file_id = _extract_account_image_file_id(update.message)

    err = await admin_save_party_account(
        context, oid, party, saved_text, photo_file_id=photo_file_id
    )
    if err:
        await update.message.reply_text(
            f"{_RTL}❌ {err}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 جزئیات معامله",
                            callback_data=f"adm|dgs|{oid}",
                        )
                    ]
                ]
            ),
        )
        raise ApplicationHandlerStop

    from handlers.admin import _persist_admin_wizard_state

    context.user_data["state"] = UserState.ADMIN_MENU.name
    context.user_data.pop("admin_deal_acc_offer_id", None)
    context.user_data.pop("admin_deal_acc_party", None)
    _persist_admin_wizard_state(uid, context)

    gate = deal_gate_get(oid)
    both = bool(
        gate
        and (gate.get("buyer_accounts_text") or "").strip()
        and (gate.get("seller_accounts_text") or "").strip()
    )
    ok_msg = (
        f"{_RTL}✅ عکس حساب ثبت شد و در خلاصهٔ معامله برای ادمین قرار گرفت."
        + (
            f"\n{_RTL}هر دو حساب کامل شد — معامله تکمیل شد."
            if both
            else f"\n{_RTL}پس از ثبت حساب طرف دیگر، معامله تکمیل می‌شود."
        )
    )
    logger.info(
        "deal_gate: admin photo account offer=%s party=%s uid=%s",
        oid,
        party,
        uid,
    )
    await update.message.reply_text(
        ok_msg,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📊 جزئیات معامله",
                        callback_data=f"adm|dgs|{oid}",
                    )
                ],
                [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="adm|panel")],
            ]
        ),
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


async def deal_gate_accounts_photo_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """عکس کارت/حساب — بدون OCR؛ عکس در خلاصهٔ معامله برای ادمین."""
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    gate = deal_gate_active_for_user(uid)
    if not gate or (gate.get("gate_status") or "").strip().lower() != "accounts":
        return
    from utils.flow_guards import user_offer_wizard_text_step

    if user_offer_wizard_text_step(context):
        return
    from handlers.iran_panel_sync import is_iran_tx_flow_active

    if is_iran_tx_flow_active(context) or context.user_data.get("admin_iran_txn_mode") in (
        "in",
        "out",
    ):
        return

    from handlers.offers import _clear_offer_flow

    _clear_offer_flow(context)

    oid = int(gate["offer_id"])
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    is_buyer = uid == buyer_id
    key = "buyer_accounts_text" if is_buyer else "seller_accounts_text"
    if (gate.get(key) or "").strip():
        await update.message.reply_text(f"{_RTL}اطلاعات حساب شما قبلاً ثبت شده.")
        raise ApplicationHandlerStop

    if not _message_has_account_image(update.message):
        await update.message.reply_text(
            f"{_RTL}❌ فقط عکس (JPG/PNG) بفرستید، یا اطلاعات را به‌صورت متن بنویسید."
        )
        raise ApplicationHandlerStop

    caption = (update.message.caption or "").strip()
    saved_text = _account_photo_saved_text(extra_caption=caption)
    photo_file_id = _extract_account_image_file_id(update.message)

    logger.info(
        "deal_gate: photo account uid=%s offer=%s party=%s",
        uid,
        oid,
        "buyer" if is_buyer else "seller",
    )
    await _commit_party_account(
        context,
        gate=gate,
        uid=uid,
        text=saved_text,
        user_message_id=update.message.message_id,
        photo_file_id=photo_file_id,
    )
    raise ApplicationHandlerStop


async def _handle_account_confirm_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    offer_id: int,
) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    uid = q.from_user.id
    pending = context.user_data.get(_ACC_PENDING_KEY)
    if not isinstance(pending, dict) or int(pending.get("offer_id") or 0) != offer_id:
        await q.answer("این پیش‌نمایش منقضی شده — دوباره عکس بفرستید.", show_alert=True)
        return

    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "").strip().lower() != "accounts":
        _clear_account_pending(context)
        await q.answer("این مرحله دیگر فعال نیست.", show_alert=True)
        return

    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    if uid not in (buyer_id, seller_id):
        await q.answer()
        return

    is_buyer = uid == buyer_id
    key = "buyer_accounts_text" if is_buyer else "seller_accounts_text"
    if (gate.get(key) or "").strip():
        _clear_account_pending(context)
        await q.answer("حساب شما قبلاً ثبت شده.", show_alert=True)
        return

    if action == "cancel":
        _clear_account_pending(context)
        await q.answer("لغو شد.")
        try:
            if q.message:
                await q.message.edit_text(
                    f"{_RTL}❌ ثبت حساب لغو شد.\n"
                    f"{_RTL}می‌توانید دوباره عکس یا متن بفرستید.",
                    parse_mode=ParseMode.HTML,
                )
        except Exception:
            pass
        return

    if action == "edit":
        _clear_account_pending(context)
        await q.answer()
        try:
            if q.message:
                await q.message.edit_text(
                    f"{_RTL}✏️ لطفاً اطلاعات حساب را "
                    f"<b>به‌صورت متن</b> بفرستید (نام، شبا، کارت…).",
                    parse_mode=ParseMode.HTML,
                )
        except Exception:
            pass
        return

    if action != "ok":
        await q.answer()
        return

    text = (pending.get("text") or "").strip()
    if len(text) < 3:
        await q.answer("متن حساب نامعتبر است.", show_alert=True)
        return

    await q.answer("ثبت شد ✅")
    try:
        if q.message:
            await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    photo_mid = pending.get("photo_mid")
    _clear_account_pending(context)
    await _commit_party_account(
        context,
        gate=gate,
        uid=uid,
        text=text,
        user_message_id=int(photo_mid) if photo_mid else None,
    )


async def deal_gate_accounts_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """پیام متنی حساب‌ها پس از تأیید دوطرفه — اولویت بالاتر از wizard آگهی/پیشنهاد."""
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    gate = deal_gate_active_for_user(uid)
    if not gate or (gate.get("gate_status") or "").strip().lower() != "accounts":
        logger.debug(
            "deal_gate: skip text uid=%s — no accounts gate (gate=%s)",
            uid,
            gate.get("gate_status") if gate else None,
        )
        return
    from utils.flow_guards import user_offer_wizard_text_step

    if user_offer_wizard_text_step(context):
        logger.info(
            "deal_gate: skip text uid=%s offer=%s — offer wizard text step",
            uid,
            gate.get("offer_id"),
        )
        return
    ud = context.user_data or {}
    if ud.get(_ACC_PENDING_KEY):
        await update.message.reply_text(
            f"{_RTL}ℹ️ ابتدا پیش‌نمایش عکس قبلی را تأیید یا لغو کنید، "
            f"یا دکمهٔ «✏️ ویرایش دستی» را بزنید."
        )
        raise ApplicationHandlerStop
    from handlers.offers import _clear_offer_flow

    _clear_offer_flow(context)
    oid = int(gate["offer_id"])
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text or len(text) < 3:
        await update.message.reply_text(f"{_RTL}❌ متن حساب را کامل‌تر بفرستید.")
        raise ApplicationHandlerStop

    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    is_buyer = uid == buyer_id
    key = "buyer_accounts_text" if is_buyer else "seller_accounts_text"
    party = "خریدار" if is_buyer else "فروشنده"
    if gate.get(key):
        await update.message.reply_text(f"{_RTL}اطلاعات حساب شما قبلاً ثبت شده.")
        raise ApplicationHandlerStop

    logger.info(
        "deal_gate: text account uid=%s offer=%s party=%s state=%r len=%s",
        uid,
        oid,
        party,
        context.user_data.get("state"),
        len(text),
    )
    await _commit_party_account(
        context,
        gate=gate,
        uid=uid,
        text=text,
        user_message_id=update.message.message_id,
    )
    raise ApplicationHandlerStop


async def _complete_deal(context: ContextTypes.DEFAULT_TYPE, offer_id: int) -> None:
    gate = deal_gate_get(offer_id)
    row = get_advert_offer_joined(offer_id)
    if not gate or not row:
        return
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"]))
    if not advert:
        return

    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    aid = int(row["advert_rowid"])
    deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=aid,
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="completed",
        completed_at=int(time.time()),
    )
    _log(offer_id, "معامله تکمیل — به‌روزرسانی پیام ادمین")
    _cancel_gate_jobs(context, offer_id)
    await sync_deal_admin_notification(
        context.bot, offer_id, deal_complete=True
    )
    gate = deal_gate_get(offer_id) or gate
    for uid in (buyer_id, seller_id):
        if not uid:
            continue
        await _purge_user_deal_chat(
            context.bot, user_data_store, int(uid), offer_id, gate
        )
        await _notify_user_account_wait(
            context,
            int(uid),
            deal_complete=True,
            advert=advert,
            row=row,
            offer_id=offer_id,
        )
