"""
handlers/deal_gate.py — Deal Gate / دروازه معامله

پس از پذیرش پیشنهاد: تأیید نهایی دوطرفه → جمع حساب یورو → هماهنگی واریز تومان/یورو با ادمین.

EN:
  Final yes/no gate, account collection, staged admin payments (toman card, toman settled,
  euro account to seller, euro receipts, buyer/admin euro confirm, admin toman receipt to seller).

FA:
  تأیید نهایی، ثبت حساب، کارت/فیش تومان خریدار، تومان نشست، حساب یورو به فروشنده،
  فیش یورو، تأیید نشستن (خریدار یا ادمین), فیش تومان ادمین به فروشنده.

راهنمای کامل: docs/DEAL_GATE.md

File sections (search: "Section" or "بخش"):
  1  EN: admin_notify JSON | FA: شناسه پیام ادمین
  2  EN: sync admin message | FA: همگام پیام ادمین
  3  EN: keyboards / main menu | FA: کیبورد و منو
  4  EN: receipt forward | FA: فوروارد فیش
  5  EN: toman card to buyer | FA: کارت تومان خریدار
  6  EN: tomset / eurcfm / stom | FA: نشست و فیش ادمین
  7  EN: outbound replay | FA: بازپخش outbound
  8  EN: account collection | FA: جمع حساب
  9  EN: gate start / reminders | FA: شروع gate
 10  EN: party receipts routers | FA: فیش طرفین
 11  EN: admin panel deals | FA: پنل معاملات
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import html as html_module
import json
import logging
import os
import re
import tempfile
import time

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config.settings import ADMIN_IDS, BANK_CARDS, DEAL_SUPPORT_ADMIN_IDS
from database.db import (
    bot_outbound_log_insert,
    bot_outbound_log_list,
    deal_delivery_claim,
    deal_delivery_list_for_offer,
    deal_delivery_due,
    deal_delivery_defer_rate_limit,
    deal_delivery_enqueue,
    deal_delivery_mark_failed,
    deal_delivery_mark_sent,
    deal_gate_accounts_for_user,
    deal_gate_archive_and_reactivate,
    deal_gate_audit,
    deal_gate_append_buyer_receipt,
    deal_gate_append_seller_receipt,
    deal_gate_buyer_receipt_list,
    deal_gate_confirm_seller_receipt_buyer,
    deal_gate_seller_receipt_list,
    deal_gate_seller_toman_admin_list,
    deal_gate_get,
    deal_gate_list_awaiting_admin_toman_receipt,
    deal_gate_list_for_admin,
    deal_operational_health,
    deal_gate_record_seller_toman_delivery,
    deal_gate_repair_safe,
    deal_gate_close_atomic,
    deal_gate_settle_and_close_atomic,
    deal_gate_upsert,
    get_advert_offer_joined,
    get_euro_advert_by_rowid,
    get_user,
    negotiation_transcript_append_line,
    negotiation_transcript_list,
    update_advert_offer_status,
    update_euro_advert_status,
)
from state import user_data_store
from utils.bank_cards import display_bank_title, format_bank_card_html, parse_bank_cards

logger = logging.getLogger(__name__)

_BUYER_EUR_ACCOUNT_TO_SELLER_TAG = "حساب یوروی خریدار به فروشنده"
_BUYER_TOMAN_CARD_TAG = "کارت واریز تومان به خریدار"


def _buyer_toman_card_delivered(offer_id: int, buyer_telegram_id: int) -> bool:
    """کارت تومان واقعاً به چت خریدار رسیده (لاگ outbound)."""
    bid = int(buyer_telegram_id)
    if bid <= 0:
        return False
    for row in bot_outbound_log_list(int(offer_id)):
        if int(row.get("recipient_telegram_id") or 0) != bid:
            continue
        if (row.get("tag") or "").strip() == _BUYER_TOMAN_CARD_TAG:
            return True
    return False


def _seller_buyer_eur_account_delivered(offer_id: int, seller_telegram_id: int) -> bool:
    """آیا پیام حساب خریدار واقعاً به چت فروشنده در لاگ outbound ثبت شده؟"""
    sid = int(seller_telegram_id)
    if sid <= 0:
        return False
    for row in bot_outbound_log_list(int(offer_id)):
        if int(row.get("recipient_telegram_id") or 0) != sid:
            continue
        if (row.get("tag") or "").strip() == _BUYER_EUR_ACCOUNT_TO_SELLER_TAG:
            return True
    return False

_RTL = "\u200f"
_ACC_PENDING_KEY = "deal_acc_pending"
_DEAL_ACC_OFFER_KEY = "deal_gate_accounts_offer_id"
_DEAL_ACC_REQUIRE_PICK_KEY = "deal_gate_accounts_require_pick"
_DEAL_RCPT_KEY = "deal_rcpt_pending"
_DEAL_ADMIN_STOM_KEY = "deal_admin_stom_pending"
_DEAL_ADMIN_PXY_KEY = "deal_admin_pxy_pending"
_ACCOUNT_PHOTO_MARKER = "📷 عکس حساب"
_REMINDER1_SEC = 3600
_REMINDER2_SEC = 7200
_SELLER_STOM_REMINDER_SEC = 8 * 3600
_ADMIN_TOMAN_REMINDER_SEC = 3600
_ADMIN_TOMAN_REMINDER_TAG = "یادآوری ساعتی ادمین: فیش تومان فروشنده"
_HOURLY_SEC = 3600
_admin_sync_locks: dict[int, asyncio.Lock] = {}
_ADMIN_DEAL_CONFIRM_KEY = "admin_deal_sensitive_confirm"


def _is_full_deal_admin(user_id: int) -> bool:
    uid = int(user_id)
    return uid in set(ADMIN_IDS or []) and uid not in set(
        DEAL_SUPPORT_ADMIN_IDS or []
    )


async def _require_full_deal_admin(query) -> bool:
    if _is_full_deal_admin(int(query.from_user.id)):
        return True
    try:
        await query.answer(
            "این حساب دسترسی مشاهده دارد؛ اقدام مالی فقط برای مدیر کامل مجاز است.",
            show_alert=True,
        )
    except Exception:
        pass
    return False


async def _admin_sensitive_confirmation(
    context: ContextTypes.DEFAULT_TYPE,
    query,
    *,
    action: str,
    offer_id: int,
    confirm_data: str,
    prompt: str,
    is_confirmation: bool,
) -> bool:
    """Require a fresh per-admin confirmation before a financial mutation."""
    key = (action, int(offer_id))
    now = int(time.time())
    user_data = getattr(context, "user_data", None)
    if not isinstance(user_data, dict):
        user_data = {}
        context.user_data = user_data
    pending = user_data.get(_ADMIN_DEAL_CONFIRM_KEY)
    if is_confirmation:
        valid = (
            isinstance(pending, dict)
            and (pending.get("action"), int(pending.get("offer_id") or 0)) == key
            and now - int(pending.get("created_at") or 0) <= 120
        )
        user_data.pop(_ADMIN_DEAL_CONFIRM_KEY, None)
        if valid:
            _log(
                int(offer_id),
                f"admin_id={int(query.from_user.id)} confirmed sensitive action: {action}",
                from_role="admin",
            )
            return True
        await _expire_stale_deal_button(
            query, "تأیید منقضی شده است؛ اقدام را دوباره از پیام جدید شروع کنید."
        )
        return False
    user_data[_ADMIN_DEAL_CONFIRM_KEY] = {
        "action": action,
        "offer_id": int(offer_id),
        "created_at": now,
    }
    try:
        await query.answer(
            "برای انجام نهایی یک‌بار دیگر تأیید کنید.", show_alert=True
        )
    except Exception:
        pass
    try:
        await query.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(prompt, callback_data=confirm_data),
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ انصراف",
                            callback_data=f"adm|dgs|resync|{int(offer_id)}",
                        )
                    ],
                ]
            )
        )
    except Exception:
        pass
    return False


def is_deal_receipt_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """فلو ثبت فیش معامله (ادمین یا طرفین) — iran_panel نباید رهگیری کند."""
    ud = context.user_data or {}
    return bool(
        ud.get(_DEAL_ADMIN_PXY_KEY)
        or ud.get(_DEAL_ADMIN_STOM_KEY)
        or ud.get(_DEAL_RCPT_KEY)
    )

# =============================================================================
# Section 1 | بخش ۱ — Admin message IDs (admin_notify_mids)
# EN: Map admin chat_id → message_id for edit-in-place and reply threading.
# FA: نگاشت chat ادمین به message_id برای ویرایش همان پیام و reply فیش‌ها.
# =============================================================================


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


def _parse_admin_escalation_mids(gate: dict) -> dict[int, int]:
    raw = (gate.get("admin_escalation_mids") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {int(k): int(v) for k, v in data.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _serialize_admin_escalation_mids(mids: dict[int, int]) -> str:
    return json.dumps({str(k): int(v) for k, v in mids.items()})


def _parse_admin_notify_photo_mids(
    gate: dict,
) -> dict[int, dict[str, int | list[int] | list[str] | dict[str, int]]]:
    raw = (gate.get("admin_notify_photo_mids") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        out: dict[int, dict[str, int | list[int] | list[str] | dict[str, int]]] = {}
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            entry: dict[str, int | list[int] | list[str] | dict[str, int]] = {}
            for pk, pv in v.items():
                if isinstance(pv, dict):
                    by_fid = {
                        str(fk): int(fm)
                        for fk, fm in pv.items()
                        if str(fk).strip() and int(fm) > 0
                    }
                    if by_fid:
                        entry[str(pk)] = by_fid
                elif isinstance(pv, list):
                    if pv and isinstance(pv[0], str):
                        fids = [str(x) for x in pv if str(x).strip()]
                        if fids:
                            entry[str(pk)] = fids
                    else:
                        mids = [int(x) for x in pv if int(x) > 0]
                        if mids:
                            entry[str(pk)] = mids
                elif isinstance(pv, str):
                    if str(pk).strip() == "mode" and pv.strip():
                        entry[str(pk)] = pv.strip()
                else:
                    try:
                        entry[str(pk)] = int(pv)
                    except (TypeError, ValueError):
                        continue
            if entry:
                out[int(k)] = entry
        return out
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _serialize_admin_notify_photo_mids(
    mids: dict[int, dict[str, int | list[int] | list[str] | dict[str, int]]],
) -> str:
    def _encode_val(
        v: int | str | list[int] | list[str] | dict[str, int],
    ) -> int | str | list[int] | list[str] | dict[str, int]:
        if isinstance(v, dict):
            return {str(fk): int(fm) for fk, fm in v.items() if str(fk).strip() and int(fm) > 0}
        if isinstance(v, list):
            if not v:
                return []
            if isinstance(v[0], str):
                return [str(x) for x in v if str(x).strip()]
            return [int(x) for x in v]
        if isinstance(v, str):
            return v
        return int(v)

    return json.dumps(
        {
            str(k): {str(pk): _encode_val(pv) for pk, pv in v.items()}
            for k, v in mids.items()
        }
    )


def _parse_admin_album_mids(gate: dict) -> dict[int, list[int]]:
    """لیست message_id عکس‌های reply (album، by_fid، buyer/seller قدیمی)."""
    raw = _parse_admin_notify_photo_mids(gate)
    out: dict[int, list[int]] = {}
    for chat_id, v in raw.items():
        mids: list[int] = []
        seen: set[int] = set()

        def add(mid) -> None:
            try:
                m_id = int(mid)
            except (TypeError, ValueError):
                return
            if m_id > 0 and m_id not in seen:
                seen.add(m_id)
                mids.append(m_id)

        by_fid = v.get("by_fid")
        if isinstance(by_fid, dict):
            for mid in by_fid.values():
                add(mid)
        album = v.get("album")
        if isinstance(album, list):
            for mid in album:
                add(mid)
        for key in ("buyer", "seller"):
            add(v.get(key))
        if mids:
            out[int(chat_id)] = mids
    return out


def _admin_stored_text_mid(
    gate: dict, chat_id: int, raw_mid: int | None
) -> int | None:
    """message_id پیام متنی ادمین — اگر روی عکس/آلبوم قدیمی باشد None."""
    if not raw_mid:
        return None
    try:
        mid = int(raw_mid)
    except (TypeError, ValueError):
        return None
    if mid <= 0:
        return None
    if mid in _all_stored_admin_photo_mids(gate, int(chat_id), text_mid=None):
        return None
    return mid


def _parse_admin_photo_reply_by_fid(gate: dict) -> dict[int, dict[str, int]]:
    """chat_id -> {telegram_file_id: message_id} (قالب قدیمی reply تکی)."""
    raw = _parse_admin_notify_photo_mids(gate)
    out: dict[int, dict[str, int]] = {}
    for chat_id, v in raw.items():
        by_fid = v.get("by_fid")
        if not isinstance(by_fid, dict):
            continue
        m: dict[str, int] = {}
        for fid, mid in by_fid.items():
            f = str(fid or "").strip()
            try:
                m_id = int(mid)
            except (TypeError, ValueError):
                continue
            if f and m_id > 0:
                m[f] = m_id
        if m:
            out[int(chat_id)] = m
    return out


def _parse_admin_album_fids(gate: dict) -> dict[int, list[str]]:
    """chat_id -> ترتیب file_idهای آلبوم reply."""
    raw = _parse_admin_notify_photo_mids(gate)
    out: dict[int, list[str]] = {}
    for chat_id, v in raw.items():
        fids = v.get("fids")
        if isinstance(fids, list):
            lst = [str(f).strip() for f in fids if str(f).strip()]
            if lst:
                out[int(chat_id)] = lst
    return out


def _account_text_is_photo_marker(text: str | None) -> bool:
    if not text:
        return False
    return any(
        line.strip().startswith(_ACCOUNT_PHOTO_MARKER)
        for line in str(text).splitlines()
    )


def _photo_caption_html(html: str, *, limit: int = 1024) -> str:
    if len(html) <= limit:
        return html
    # برش خام HTML تگ باز می‌گذارد و Telegram خطا می‌دهد — خلاصهٔ متنی امن
    plain = html_module.unescape(re.sub(r"<[^>]+>", " ", html or ""))
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) <= limit - 1:
        return f"{_RTL}{html_module.escape(plain)}"
    return f"{_RTL}{html_module.escape(plain[: limit - 2])}…"


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
    """حذف replyهای تکی قدیمی (buyer/seller/by_fid) — قبل از media group جدید."""
    stored = _parse_admin_notify_photo_mids(gate)
    if not stored:
        return
    for chat_id in recipients:
        mids = stored.get(int(chat_id)) or {}
        for key in ("seller", "buyer"):
            mid = mids.get(key)
            if isinstance(mid, int) and mid:
                await _delete_message_safe(bot, chat_id, int(mid))
        mode = str(mids.get("mode") or "").strip().lower()
        by_fid = mids.get("by_fid")
        album = mids.get("album")
        if mode == "reply" and isinstance(by_fid, dict):
            for mid in by_fid.values():
                try:
                    m_id = int(mid)
                except (TypeError, ValueError):
                    continue
                if m_id > 0:
                    await _delete_message_safe(bot, chat_id, m_id)
        elif isinstance(by_fid, dict) and isinstance(album, list) and album:
            for mid in by_fid.values():
                try:
                    m_id = int(mid)
                except (TypeError, ValueError):
                    continue
                if m_id > 0:
                    await _delete_message_safe(bot, chat_id, m_id)


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


_TELEGRAM_ALBUM_MAX = 10


def _admin_party_account_photo(gate: dict, party: str) -> str | None:
    """file_id عکس حساب یک طرف."""
    if party == "buyer":
        acct = (gate.get("buyer_accounts_text") or "").strip()
        fid = (gate.get("buyer_accounts_photo_file_id") or "").strip()
    else:
        acct = (gate.get("seller_accounts_text") or "").strip()
        fid = (gate.get("seller_accounts_photo_file_id") or "").strip()
    if fid and _account_text_is_photo_marker(acct):
        return fid
    return None


def _admin_receipt_slides_plan(
    gate: dict,
    offer_id: int,
    *,
    seq: int,
    aid: int,
) -> list[tuple[str, str]]:
    """فیش‌ها در یک آلبوم — هر caption برچسب آگهی دارد."""
    from handlers.offers import (
        buyer_toman_receipt_slide_caption_html,
        seller_euro_receipt_slide_caption_html,
        seller_toman_receipt_slide_caption_html,
    )

    oid = int(offer_id)
    tag = _deal_admin_album_tag_html(seq, aid, oid)
    slides: list[tuple[str, str]] = []

    def add(fid: str | None, cap: str, *, kind: str = "photo") -> None:
        f = (fid or "").strip()
        if f and cap:
            slides.append((f, cap, kind))

    for r in deal_gate_buyer_receipt_list(oid):
        rt = (r.get("type") or "").strip().lower()
        if rt in ("photo", "document") and (r.get("file_id") or "").strip():
            body = buyer_toman_receipt_slide_caption_html(gate)
            add(
                r.get("file_id"),
                f"{tag}\n{body}",
                kind="document" if rt == "document" else "photo",
            )

    for r in deal_gate_seller_receipt_list(oid):
        rt = (r.get("type") or "").strip().lower()
        if rt in ("photo", "document") and (r.get("file_id") or "").strip():
            body = seller_euro_receipt_slide_caption_html(gate, r)
            add(
                r.get("file_id"),
                f"{tag}\n{body}",
                kind="document" if rt == "document" else "photo",
            )

    for r in deal_gate_seller_toman_admin_list(oid):
        rt = (r.get("type") or "").strip().lower()
        if rt in ("photo", "document") and (r.get("file_id") or "").strip():
            body = seller_toman_receipt_slide_caption_html(gate)
            add(
                r.get("file_id"),
                f"{tag}\n{body}",
                kind="document" if rt == "document" else "photo",
            )

    return slides[:_TELEGRAM_ALBUM_MAX]


def _admin_account_slides_plan(
    gate: dict,
    offer_id: int,
    *,
    seq: int,
    aid: int,
) -> list[tuple[str, str]]:
    """عکس‌های حساب — آلبوم reply جدا تا caption ۱۰۲۴ بخش فروشنده را نبرد."""
    tag = _deal_admin_album_tag_html(seq, aid, offer_id)
    slides: list[tuple[str, str, str]] = []
    for party, label in (("buyer", "حساب خریدار"), ("seller", "حساب فروشنده")):
        fid = _admin_party_account_photo(gate, party)
        if fid:
            slides.append((fid, f"{tag}\n{_RTL}📷 <b>{label}</b>", "photo"))
    return slides[:_TELEGRAM_ALBUM_MAX]


def _admin_deal_slides_plan(
    gate: dict,
    offer_id: int,
    *,
    seq: int,
    aid: int,
    include_receipts: bool = True,
) -> list[tuple]:
    """حساب‌ها + فیش‌ها — media group عکس‌ها + سند PDF زیر پیام متنی."""
    merged: list[tuple] = []
    seen: set[str] = set()
    for slide in _admin_account_slides_plan(gate, offer_id, seq=seq, aid=aid):
        f = (slide[0] or "").strip()
        if f and f not in seen:
            seen.add(f)
            merged.append(slide)
    if include_receipts:
        for slide in _admin_receipt_slides_plan(gate, offer_id, seq=seq, aid=aid):
            f = (slide[0] or "").strip()
            if f and f not in seen:
                seen.add(f)
                merged.append(slide)
    return merged[:_TELEGRAM_ALBUM_MAX]


def _deal_admin_album_tag_html(seq: int, aid: int, offer_id: int) -> str:
    """برچسب کوتاه روی هر عکس — تشخیص آگهی وقتی چند معامله باز است."""
    return (
        f"{_RTL}📌 <b>آگهی {int(aid)}</b> · پیشنهاد <b>{int(seq)}</b> "
        f"· #{int(offer_id)}"
    )


def _slide_kind(slide: tuple) -> str:
    return slide[2] if len(slide) > 2 else "photo"


def _build_receipt_only_album_media(
    slides: list[tuple],
) -> list[InputMediaPhoto]:
    """آلبوم عکس — PDF/سند جدا ارسال می‌شود."""
    media: list[InputMediaPhoto] = []
    for slide in slides:
        if _slide_kind(slide) == "document":
            continue
        fid, slide_cap = slide[0], slide[1]
        cap = _photo_caption_html(slide_cap)
        try:
            media.append(
                InputMediaPhoto(
                    media=fid,
                    caption=cap,
                    parse_mode=ParseMode.HTML,
                    show_caption_above_media=True,
                )
            )
        except TypeError:
            media.append(
                InputMediaPhoto(
                    media=fid,
                    caption=cap,
                    parse_mode=ParseMode.HTML,
                )
            )
    return media


async def _sync_admin_album_captions(
    bot,
    *,
    chat_id: int,
    album_mids: list[int],
    slides: list[tuple],
) -> bool:
    """به‌روز caption عکس/سند آلبوم وقتی file_idها عوض نشده."""
    if len(album_mids) != len(slides):
        return False
    ok = True
    for mid, slide in zip(album_mids, slides):
        cap = _photo_caption_html(slide[1])
        try:
            await bot.edit_message_caption(
                chat_id=int(chat_id),
                message_id=int(mid),
                caption=cap,
                parse_mode=ParseMode.HTML,
            )
        except BadRequest as e:
            if "message is not modified" not in str(e).lower():
                ok = False
        except TelegramError:
            ok = False
    return ok


async def _send_admin_slides_media_group(
    bot,
    *,
    chat_id: int,
    text_mid: int,
    slides: list[tuple],
    log_offer_id: int,
) -> tuple[list[int], dict[str, int]]:
    """media group برای عکس‌ها + send_document برای PDF (reply زیر پیام متنی)."""
    if not slides:
        return [], {}
    photo_slides = [s for s in slides if _slide_kind(s) != "document"]
    doc_slides = [s for s in slides if _slide_kind(s) == "document"]
    album_mids: list[int] = []
    by_fid: dict[str, int] = {}

    if photo_slides:
        media = _build_receipt_only_album_media(photo_slides)
        if media:
            try:
                try:
                    msgs = await bot.send_media_group(
                        chat_id=int(chat_id),
                        media=media,
                        reply_to_message_id=int(text_mid),
                    )
                except TypeError:
                    msgs = await bot.send_media_group(
                        chat_id=int(chat_id),
                        media=media,
                    )
            except TelegramError as e:
                logger.warning(
                    "deal_admin_sync: media_group offer=%s chat=%s: %s",
                    log_offer_id,
                    chat_id,
                    e,
                )
                msgs = []
            for slide, msg in zip(photo_slides, msgs or []):
                fid = (slide[0] or "").strip()
                if fid and msg:
                    album_mids.append(int(msg.message_id))
                    by_fid[fid] = int(msg.message_id)

    for slide in doc_slides:
        fid = (slide[0] or "").strip()
        if not fid:
            continue
        cap = _photo_caption_html(slide[1])
        try:
            sent = await bot.send_document(
                int(chat_id),
                document=fid,
                caption=cap,
                parse_mode=ParseMode.HTML,
                reply_to_message_id=int(text_mid),
            )
            album_mids.append(int(sent.message_id))
            by_fid[fid] = int(sent.message_id)
        except TelegramError as e:
            logger.warning(
                "deal_admin_sync: document offer=%s chat=%s: %s",
                log_offer_id,
                chat_id,
                e,
            )
    return album_mids, by_fid


def _rebuild_by_fid_from_stored(
    *,
    stored_fids: list[str],
    old_album_mids: list[int],
    text_mid: int,
    stored_by_fid: dict[str, int],
) -> dict[str, int]:
    if stored_by_fid:
        return dict(stored_by_fid)
    by_fid: dict[str, int] = {}
    if stored_fids and old_album_mids and len(stored_fids) == len(old_album_mids):
        for fid, mid in zip(stored_fids, old_album_mids):
            if int(mid) != int(text_mid):
                by_fid[str(fid)] = int(mid)
    return by_fid


async def _sync_admin_text_and_album_reply(
    bot,
    *,
    chat_id: int,
    old_mid: int | None,
    old_album_mids: list[int],
    stored_fids: list[str],
    stored_by_fid: dict[str, int],
    admin_html: str,
    slides: list[tuple],
    reply_markup,
    plain: str,
    log_offer_id: int,
    supersede_text_mids: list[int] | None = None,
    force_rebuild: bool = False,
) -> tuple[int | None, list[int], list[str], dict[str, int]]:
    """
    پیام متنی اصلی (خلاصه + دکمه) + یک media group برای همه عکس‌های معامله.
    با اضافه/تغییر عکس، آلبوم قبلی حذف و یک آلبوم کامل بازسازی می‌شود.
    """
    desired_fids = [(s[0] or "").strip() for s in slides if (s[0] or "").strip()]
    slides_ordered = [s for s in slides if (s[0] or "").strip()]
    was_glued = bool(
        old_album_mids
        and old_mid
        and int(old_mid) == int(old_album_mids[0])
    )
    text_old_mid = None if was_glued else old_mid

    text_mid = await _edit_or_send_admin_notification(
        bot,
        chat_id=chat_id,
        old_mid=text_old_mid,
        admin_html=admin_html,
        photo_fids=[],
        reply_markup=reply_markup,
        plain=plain,
        log_offer_id=log_offer_id,
    )
    if not text_mid:
        return None, [], [], {}

    all_old_mids = set(int(x) for x in old_album_mids if int(x) > 0)

    if not slides:
        for mid in all_old_mids | set(stored_by_fid.values()):
            if int(mid) != int(text_mid):
                await _delete_message_safe(bot, chat_id, int(mid))
        return int(text_mid), [], [], {}

    if was_glued:
        for mid in old_album_mids:
            await _delete_message_safe(bot, chat_id, int(mid))
        stored_by_fid = {}
        all_old_mids = set()

    by_fid = _rebuild_by_fid_from_stored(
        stored_fids=stored_fids,
        old_album_mids=old_album_mids,
        text_mid=int(text_mid),
        stored_by_fid=stored_by_fid,
    )

    can_update_captions = (
        not force_rebuild
        and bool(
            desired_fids
            and stored_fids
            and desired_fids == stored_fids
            and len(old_album_mids) == len(desired_fids)
            and all(fid in by_fid for fid in desired_fids)
        )
    )
    if can_update_captions:
        album_mids = [by_fid[fid] for fid in desired_fids]
        await _sync_admin_album_captions(
            bot,
            chat_id=int(chat_id),
            album_mids=album_mids,
            slides=slides_ordered,
        )
        return int(text_mid), album_mids, desired_fids, by_fid

    gate_fresh = deal_gate_get(log_offer_id) or {}
    purge_mids = set(all_old_mids | set(by_fid.values()))
    purge_mids.update(
        _all_stored_admin_photo_mids(
            gate_fresh, int(chat_id), text_mid=int(text_mid)
        )
    )
    for mid in purge_mids:
        if int(mid) != int(text_mid):
            await _delete_message_safe(bot, chat_id, int(mid))
    await _delete_all_admin_album_messages_for_chat(
        bot,
        chat_id=int(chat_id),
        gate=gate_fresh,
        text_mid=int(text_mid),
        extra_mids=list(purge_mids),
    )

    for sm in set(supersede_text_mids or []):
        try:
            s_mid = int(sm)
        except (TypeError, ValueError):
            continue
        if s_mid > 0 and s_mid != int(text_mid):
            await _delete_message_safe(bot, chat_id, s_mid)

    album_mids, by_fid = await _send_admin_slides_media_group(
        bot,
        chat_id=int(chat_id),
        text_mid=int(text_mid),
        slides=slides_ordered,
        log_offer_id=log_offer_id,
    )
    return int(text_mid), album_mids, desired_fids, by_fid


def _raw_admin_photo_message_ids(gate: dict, chat_id: int) -> set[int]:
    """استخراج همه message_id از JSON خام (fallback اگر پارس ساخت‌یافته چیزی از دست بدهد)."""
    raw_json = (gate.get("admin_notify_photo_mids") or "").strip()
    if not raw_json:
        return set()

    def _walk(obj) -> set[int]:
        found: set[int] = set()
        if isinstance(obj, int):
            if int(obj) > 0:
                found.add(int(obj))
        elif isinstance(obj, dict):
            for val in obj.values():
                found.update(_walk(val))
        elif isinstance(obj, list):
            for val in obj:
                found.update(_walk(val))
        return found

    try:
        data = json.loads(raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    entry = data.get(str(int(chat_id))) or data.get(int(chat_id))
    if not isinstance(entry, dict):
        return set()
    return _walk(entry)


def _all_stored_admin_photo_mids(
    gate: dict,
    chat_id: int,
    *,
    text_mid: int | None = None,
) -> set[int]:
    """همه message_id عکس/آلبوم ذخیره‌شده برای یک چت ادمین."""
    out: set[int] = set()
    out.update(_raw_admin_photo_message_ids(gate, int(chat_id)))
    raw = _parse_admin_notify_photo_mids(gate)
    entry = raw.get(int(chat_id)) or {}
    for key in ("buyer", "seller"):
        mid = entry.get(key)
        if isinstance(mid, int) and int(mid) > 0:
            out.add(int(mid))
    album = entry.get("album")
    if isinstance(album, list):
        for mid in album:
            try:
                m_id = int(mid)
            except (TypeError, ValueError):
                continue
            if m_id > 0:
                out.add(m_id)
    by_fid = entry.get("by_fid")
    if isinstance(by_fid, dict):
        for mid in by_fid.values():
            try:
                m_id = int(mid)
            except (TypeError, ValueError):
                continue
            if m_id > 0:
                out.add(m_id)
    if text_mid and int(text_mid) > 0:
        out.discard(int(text_mid))
    return out


async def _delete_admin_album_messages(
    bot, chat_id: int, mids: list[int]
) -> None:
    for mid in mids:
        await _delete_message_safe(bot, chat_id, int(mid))


async def _purge_admin_deal_messages_for_chat(
    bot,
    *,
    chat_id: int,
    gate: dict,
) -> None:
    """حذف پیام متنی اصلی + همهٔ عکس‌های آلبوم قبلی ادمین برای یک چت."""
    cid = int(chat_id)
    to_delete: set[int] = set(
        _all_stored_admin_photo_mids(gate, cid, text_mid=None)
    )
    raw_text = _parse_admin_notify_mids(gate).get(cid)
    if raw_text:
        try:
            to_delete.add(int(raw_text))
        except (TypeError, ValueError):
            pass
    for mid in to_delete:
        if int(mid) > 0:
            await _delete_message_safe(bot, cid, int(mid))


async def _delete_all_admin_album_messages_for_chat(
    bot,
    *,
    chat_id: int,
    gate: dict,
    text_mid: int | None,
    extra_mids: list[int] | None = None,
) -> None:
    mids = _all_stored_admin_photo_mids(gate, chat_id, text_mid=text_mid)
    if extra_mids:
        for mid in extra_mids:
            try:
                m_id = int(mid)
            except (TypeError, ValueError):
                continue
            if m_id > 0 and m_id != int(text_mid or 0):
                mids.add(m_id)
    for mid in mids:
        await _delete_message_safe(bot, int(chat_id), int(mid))


def _admin_embedded_photos_plan(
    gate: dict, offer_id: int, *, include_receipts: bool
) -> tuple[list[str], list[str]]:
    """
    عکس‌های پیام ادمین به ترتیب خواندن:
    خریدار (حساب + فیش تومان) → فروشنده (حساب + فیش یورو + فیش تومان).
    """
    oid = int(offer_id)
    seen: set[str] = set()
    fids: list[str] = []
    labels: list[str] = []

    def add(fid: str | None, label: str) -> None:
        f = (fid or "").strip()
        if not f or f in seen:
            return
        seen.add(f)
        fids.append(f)
        labels.append(label)

    add(_admin_party_account_photo(gate, "buyer"), "حساب خریدار")
    if include_receipts:
        n = 0
        for r in deal_gate_buyer_receipt_list(oid):
            if (r.get("type") or "") == "photo":
                n += 1
                suffix = f" {n}" if n > 1 else ""
                add(r.get("file_id"), f"فیش تومان خریدار{suffix}")

    add(_admin_party_account_photo(gate, "seller"), "حساب فروشنده")
    if include_receipts:
        n = 0
        for r in deal_gate_seller_receipt_list(oid):
            if (r.get("type") or "") == "photo":
                n += 1
                suffix = f" {n}" if n > 1 else ""
                add(r.get("file_id"), f"فیش یورو فروشنده{suffix}")
        n = 0
        for r in deal_gate_seller_toman_admin_list(oid):
            if (r.get("type") or "") == "photo":
                n += 1
                suffix = f" {n}" if n > 1 else ""
                add(r.get("file_id"), f"فیش تومان به فروشنده{suffix}")

    cap = _TELEGRAM_ALBUM_MAX
    return fids[:cap], labels[:cap]


def _admin_embedded_photo_file_ids(
    gate: dict, offer_id: int, *, include_receipts: bool
) -> list[str]:
    fids, _ = _admin_embedded_photos_plan(
        gate, offer_id, include_receipts=include_receipts
    )
    return fids


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
            if msgs and reply_markup is not None:
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=chat_id,
                        message_id=int(msgs[0].message_id),
                        reply_markup=reply_markup,
                    )
                except Exception:
                    pass
            return msgs[0].message_id if msgs else old_mid
        except TelegramError as e:
            logger.warning(
                "deal_admin_sync: album send offer=%s chat=%s: %s",
                log_offer_id,
                chat_id,
                e,
            )
            try:
                sent = await bot.send_message(
                    chat_id,
                    admin_html,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=reply_markup,
                )
                return sent.message_id
            except TelegramError as e2:
                logger.warning(
                    "deal_admin_sync: text fallback failed offer=%s chat=%s: %s",
                    log_offer_id,
                    chat_id,
                    e2,
                )
            return old_mid

    if old_mid:
        mid = int(old_mid)

        async def _try_edit_reply_markup() -> None:
            if reply_markup is None:
                return
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=mid,
                    reply_markup=reply_markup,
                )
            except Exception:
                pass

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=mid,
                text=admin_html,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            return mid
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                await _try_edit_reply_markup()
                return mid
            logger.warning(
                "deal_admin_sync: html edit failed offer=%s chat=%s mid=%s: %s",
                log_offer_id,
                chat_id,
                mid,
                e,
            )
        except TelegramError as e:
            logger.warning(
                "deal_admin_sync: text edit failed offer=%s chat=%s mid=%s: %s",
                log_offer_id,
                chat_id,
                mid,
                e,
            )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=mid,
                text=plain,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            return mid
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                await _try_edit_reply_markup()
                return mid
        except TelegramError:
            pass
        await _try_edit_reply_markup()
        await _delete_message_safe(bot, chat_id, mid)

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


# =============================================================================
# Section 2 | بخش ۲ — sync_deal_admin_notification
# EN: Build HTML via offers; resend fresh admin message + album (delete old).
# FA: هر به‌روزرسانی — حذف پیام قبلی و ارسال مجدد پایین چت + آلبوم.
# =============================================================================


def _admin_album_unchanged(
    *,
    old_album_mids: list[int],
    stored_fids: list[str],
    desired_fids: list[str],
) -> bool:
    if not desired_fids:
        return not old_album_mids
    if not old_album_mids or len(old_album_mids) != len(desired_fids):
        return False
    if stored_fids:
        return stored_fids == desired_fids
    return False


async def refresh_admin_deal_markup(bot, offer_id: int) -> None:
    """به‌روزرسانی دکمه‌های پیام اصلی ادمین (مثلاً بعد از ارسال کارت)."""
    gate = deal_gate_get(int(offer_id))
    if not gate:
        return
    oid = int(offer_id)
    kb = deal_admin_main_keyboard(oid, gate, include_payment=True)
    for chat_id, mid in _parse_admin_notify_mids(gate).items():
        if not mid:
            continue
        try:
            await bot.edit_message_reply_markup(
                chat_id=int(chat_id),
                message_id=int(mid),
                reply_markup=kb,
            )
        except Exception:
            pass


async def _refresh_admin_deal_after_payment_step(
    bot, offer_id: int, *, update_text: bool = True
) -> None:
    """همگام متن چک‌لیست + دکمه‌ها پس از مرحلهٔ واریز."""
    oid = int(offer_id)
    if update_text:
        await sync_deal_admin_notification(bot, oid, deal_complete=True)
    await refresh_admin_deal_markup(bot, oid)


async def sync_deal_admin_notification(
    bot,
    offer_id: int,
    *,
    deal_complete: bool = False,
    text_only: bool = False,
    force_album_rebuild: bool = False,
    resend_fresh: bool = True,
) -> None:
    """
    ارسال یا به‌روزرسانی پیام ادمین برای معامله.
    resend_fresh=True (پیش‌فرض): پیام قبلی + آلبوم حذف و نسخهٔ جدید پایین چت ارسال می‌شود.
    text_only / force_album_rebuild: سازگاری قدیمی — نادیده گرفته می‌شود.
    """
    oid = int(offer_id)
    lock = _admin_sync_locks.setdefault(oid, asyncio.Lock())
    async with lock:
        await _sync_deal_admin_notification_locked(
            bot,
            oid,
            deal_complete=deal_complete,
            resend_fresh=resend_fresh,
        )


async def _sync_deal_admin_notification_locked(
    bot,
    offer_id: int,
    *,
    deal_complete: bool = False,
    resend_fresh: bool = True,
) -> None:
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
    st = (gate.get("gate_status") or "").strip().lower()
    # A newly accepted offer is still waiting for the parties' final answer.
    # Do not render that first admin copy as if account collection had started.
    accounts_mode = st == "accounts"
    if deal_complete:
        album_slides = _admin_deal_slides_plan(
            gate, oid, seq=seq, aid=aid, include_receipts=True
        )
        receipt_slides_mode = True
    elif accounts_mode:
        album_slides = _admin_account_slides_plan(gate, oid, seq=seq, aid=aid)
        receipt_slides_mode = False
    else:
        album_slides = []
        receipt_slides_mode = False
    photo_fids: list[str] = []
    # عکس حساب در آلبوم reply — نه داخل caption (حد ۱۰۲۴ کاراکتر بخش فروشنده را حذف می‌کرد)
    embed_photos = False

    admin_html = _post_acceptance_admin_message_html(
        advert,
        row,
        seq,
        aid,
        buyer_accounts_text=buyer_acct or None,
        seller_accounts_text=seller_acct or None,
        accounts_status_mode=accounts_mode,
        deal_complete=deal_complete,
        embed_account_photos=embed_photos,
        embed_receipt_photos=False,
        receipt_slides_mode=receipt_slides_mode,
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
    if st == "closed":
        reply_markup = None
    elif st == "rejected":
        reply_markup = _admin_gate_rejected_keyboard(oid)
    elif deal_complete:
        reply_markup = deal_admin_main_keyboard(oid, gate, include_payment=True)
    elif st in ("pending", "accounts"):
        reply_markup = deal_admin_main_keyboard(oid, gate, include_payment=False)
    else:
        reply_markup = None

    album_stored = _parse_admin_album_mids(gate)
    by_fid_stored = _parse_admin_photo_reply_by_fid(gate)
    album_payload_updated: dict[int, dict[str, list]] = {}

    for chat_id in recipients:
        cid = int(chat_id)
        had_prior = bool(
            stored.get(chat_id)
            or album_stored.get(cid)
            or by_fid_stored.get(cid)
        )
        if resend_fresh and had_prior:
            await _purge_admin_deal_messages_for_chat(bot, chat_id=cid, gate=gate)

        if album_slides:
            new_mid, new_album, new_fids, new_by_fid = (
                await _sync_admin_text_and_album_reply(
                    bot,
                    chat_id=cid,
                    old_mid=None,
                    old_album_mids=[],
                    stored_fids=[],
                    stored_by_fid={},
                    admin_html=admin_html,
                    slides=album_slides,
                    reply_markup=reply_markup,
                    plain=plain,
                    log_offer_id=oid,
                    supersede_text_mids=[],
                    force_rebuild=True,
                )
            )
            album_payload_updated[cid] = {
                "album": new_album,
                "fids": new_fids,
                "by_fid": new_by_fid,
                "mode": "media_group",
            }
        else:
            new_mid = await _edit_or_send_admin_notification(
                bot,
                chat_id=cid,
                old_mid=None,
                admin_html=admin_html,
                photo_fids=photo_fids,
                reply_markup=reply_markup,
                plain=plain,
                log_offer_id=oid,
            )
            new_album = []
            new_fids = []
            new_by_fid = {}
            album_payload_updated[cid] = {
                "album": [],
                "fids": [],
                "mode": "media_group",
            }
        if new_mid:
            updated[chat_id] = int(new_mid)
            logger.info(
                "deal_admin_sync: %s offer=%s chat_id=%s mid=%s album=%s slides=%s",
                "resent" if had_prior and resend_fresh else "sent",
                oid,
                chat_id,
                new_mid,
                len(new_album),
                len(album_slides),
            )

    upsert_fields: dict = {}
    if updated != stored:
        upsert_fields["admin_notify_mids"] = _serialize_admin_notify_mids(updated)
    stored_photos = _parse_admin_notify_photo_mids(gate)
    photo_payload: dict[int, dict[str, int | list[int] | dict[str, int]]] = dict(
        stored_photos
    )
    for cid, payload in album_payload_updated.items():
        photo_payload[cid] = payload
    if photo_payload != stored_photos:
        upsert_fields["admin_notify_photo_mids"] = (
            _serialize_admin_notify_photo_mids(photo_payload)
            if photo_payload
            else "{}"
        )
    if upsert_fields:
        deal_gate_upsert(
            offer_id=oid,
            advert_rowid=aid,
            buyer_telegram_id=buyer_id,
            seller_telegram_id=seller_id,
            **upsert_fields,
        )


# =============================================================================
# Section 3 | بخش ۳ — Inline keyboards and main menu
# EN: Party keyboards; admins stay on deal message (no admin_home after deal buttons).
# FA: دکمه‌های طرفین؛ ادمین پس از دکمه‌های معامله منوی پنل نمی‌گیرد.
# =============================================================================


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


def _clear_deal_admin_proxy_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_DEAL_ADMIN_PXY_KEY, None)


def _admin_receipt_pending_offer_id(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """offer_id فعال در فلو فیش ادمین (proxy یا stom)."""
    for key in (_DEAL_ADMIN_PXY_KEY, _DEAL_ADMIN_STOM_KEY):
        pending = context.user_data.get(key)
        if not isinstance(pending, dict):
            continue
        try:
            oid = int(pending.get("offer_id") or 0)
        except (TypeError, ValueError):
            continue
        if oid > 0:
            return oid
    return None


def _clear_all_admin_receipt_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_deal_admin_proxy_pending(context)
    _clear_deal_admin_stom_pending(context)


def _admin_receipt_pending_switch(
    context: ContextTypes.DEFAULT_TYPE, offer_id: int
) -> bool:
    """
    True = همان offer فعال است (فیش بعدی همان‌جا).
    False = pending قبلی پاک شد یا نبود — می‌توان فلو جدید شروع کرد.
    """
    oid = int(offer_id)
    active = _admin_receipt_pending_offer_id(context)
    if active is None:
        return False
    if int(active) == oid:
        return True
    _clear_all_admin_receipt_pending(context)
    return False


def deal_admin_party_proxy_rows(
    offer_id: int, gate: dict | None = None
) -> list[list[InlineKeyboardButton]]:
    """دکمه‌های انجام کار به‌جای خریدار/فروشنده."""
    oid = int(offer_id)
    if gate is None:
        gate = deal_gate_get(oid) or {}
    st = (gate.get("gate_status") or "").strip().lower()
    rows: list[list[InlineKeyboardButton]] = []

    if st == "pending":
        br = (gate.get("buyer_response") or "").strip().lower()
        sr = (gate.get("seller_response") or "").strip().lower()
        if br != "yes":
            rows.append(
                [
                    InlineKeyboardButton(
                        "✅ تأیید خریدار",
                        callback_data=f"adm|pxy|{oid}|byes",
                    ),
                    InlineKeyboardButton(
                        "❌ رد خریدار",
                        callback_data=f"adm|pxy|{oid}|bno",
                    ),
                ]
            )
        if sr != "yes":
            rows.append(
                [
                    InlineKeyboardButton(
                        "✅ تأیید فروشنده",
                        callback_data=f"adm|pxy|{oid}|syes",
                    ),
                    InlineKeyboardButton(
                        "❌ رد فروشنده",
                        callback_data=f"adm|pxy|{oid}|sno",
                    ),
                ]
            )

    if st == "accounts":
        row = [
            InlineKeyboardButton(
                "📝 حساب خریدار",
                callback_data=f"adm|pxy|{oid}|bacc",
            ),
            InlineKeyboardButton(
                "📝 حساب فروشنده",
                callback_data=f"adm|pxy|{oid}|sacc",
            ),
        ]
        rows.append(row)

    if st == "completed":
        buyer_id = int(gate.get("buyer_telegram_id") or 0)
        card_sent = int(gate.get("buyer_toman_card_sent_at") or 0) > 0 or (
            _buyer_toman_card_delivered(oid, buyer_id) if buyer_id else False
        )
        buyer_rcpts = deal_gate_buyer_receipt_list(oid)
        seller_id = int(gate.get("seller_telegram_id") or 0)
        seller_rcpts = deal_gate_seller_receipt_list(oid)
        eur_sent = int(gate.get("seller_eur_account_sent_at") or 0) > 0
        eur_delivered = (
            _seller_buyer_eur_account_delivered(oid, seller_id) if seller_id else False
        )
        row = []
        if card_sent:
            row.append(
                InlineKeyboardButton(
                    "📎 فیش تومان خریدار",
                    callback_data=f"adm|pxy|{oid}|brcpt",
                )
            )
        if eur_sent or eur_delivered:
            row.append(
                InlineKeyboardButton(
                    "📎 فیش یورو فروشنده",
                    callback_data=f"adm|pxy|{oid}|srcpt",
                )
            )
        if row:
            rows.append(row)
        acct_row = [
            InlineKeyboardButton(
                "📝 ویرایش حساب خریدار",
                callback_data=f"adm|pxy|{oid}|bacc",
            ),
            InlineKeyboardButton(
                "📝 ویرایش حساب فروشنده",
                callback_data=f"adm|pxy|{oid}|sacc",
            ),
        ]
        rows.append(acct_row)

    return rows


def deal_admin_payment_only_rows(
    offer_id: int, gate: dict | None = None
) -> list[list[InlineKeyboardButton]]:
    """دکمه‌های هماهنگی واریز (بدون proxy)."""
    from handlers.offers import _seller_euro_fully_confirmed_gate

    oid = int(offer_id)
    if gate is None:
        gate = deal_gate_get(oid) or {}
    buyer_id = int(gate.get("buyer_telegram_id") or 0)
    card_sent = int(gate.get("buyer_toman_card_sent_at") or 0) > 0 or (
        _buyer_toman_card_delivered(oid, buyer_id) if buyer_id else False
    )
    pay_label = (
        "💳 ارسال مجدد کارت واریز به خریدار"
        if card_sent
        else "💳 ارسال کارت واریز تومان به خریدار"
    )
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(pay_label, callback_data=f"adm|pay|{oid}")],
    ]
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
    seller_id = int(gate.get("seller_telegram_id") or 0)
    eur_delivered = (
        _seller_buyer_eur_account_delivered(oid, seller_id) if seller_id else False
    )
    eur_sent = int(gate.get("seller_eur_account_sent_at") or 0) > 0
    if toman_settled and seller_id:
        if eur_sent or eur_delivered:
            eur_btn = "📤 ارسال مجدد حساب یورو به فروشنده"
        else:
            eur_btn = "📤 ارسال حساب یورو به فروشنده"
        rows.append(
            [
                InlineKeyboardButton(
                    eur_btn,
                    callback_data=f"adm|buyeur|{oid}",
                )
            ]
        )
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
    gate_status = (gate.get("gate_status") or "").strip().lower()
    seller_toman_settled = int(gate.get("seller_toman_settled_at") or 0) > 0
    seller_received_button_visible = _gate_awaiting_seller_toman_close(gate) or (
        gate_status == "completed"
        and not seller_toman_settled
        and _seller_euro_fully_confirmed_gate(gate)
    )
    if seller_received_button_visible:
        rows.append(
            [
                InlineKeyboardButton(
                    "✅ فروشنده تومان را دریافت کرد",
                    callback_data=f"adm|stomset|{oid}",
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
    return rows


def deal_admin_main_keyboard(
    offer_id: int,
    gate: dict | None = None,
    *,
    include_payment: bool = True,
) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    if gate is None:
        gate = deal_gate_get(oid) or {}
    rows = deal_admin_party_proxy_rows(oid, gate)
    if include_payment:
        rows.extend(deal_admin_payment_only_rows(oid, gate))
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    "📋 پیام‌های ربات به طرفین",
                    callback_data=f"adm|outlog|{oid}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def deal_admin_payment_actions_keyboard(
    offer_id: int, gate: dict | None = None
) -> InlineKeyboardMarkup:
    return deal_admin_main_keyboard(offer_id, gate, include_payment=True)


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


def seller_toman_settled_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    """فروشنده پس از دریافت فیش تومان از ادمین — پایان معامله."""
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ تومان نشست — پایان معامله",
                    callback_data=f"deal|stomcfm|{oid}",
                )
            ],
        ]
    )


def _clear_deal_receipt_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_DEAL_RCPT_KEY, None)


def _party_receipt_pending_offer(
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[int | None, str | None]:
    pending = context.user_data.get(_DEAL_RCPT_KEY)
    if not isinstance(pending, dict):
        return None, None
    try:
        oid = int(pending.get("offer_id") or 0)
    except (TypeError, ValueError):
        return None, None
    party = (pending.get("party") or "").strip().lower() or None
    return (oid if oid > 0 else None), party


async def _party_receipt_prepare_switch(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    offer_id: int,
    party: str,
) -> bool:
    """Select one receipt flow and remove a different deal's stale prompt."""
    active_oid, active_party = _party_receipt_pending_offer(context)
    if active_oid is None:
        return False
    if int(active_oid) == int(offer_id) and active_party == party:
        return True
    _clear_deal_receipt_pending(context)
    await _purge_rcpt_prompt_msgs(
        context.bot,
        user_data_store,
        int(user_id),
        int(active_oid),
    )
    return False


def _deal_gate_allows_party_receipts(gate: dict | None) -> bool:
    """فیش واریز فقط در مرحلهٔ پرداخت (gate_status=completed پس از ثبت هر دو حساب)."""
    if not gate:
        return False
    st = (gate.get("gate_status") or "").strip().lower()
    return st == "completed"


def _receipt_consistency_warnings(
    gate: dict, text: str, *, receipt_kind: str
) -> list[str]:
    """Conservative admin warnings only; never approve or reject money."""
    raw = (text or "").strip()
    warnings: list[str] = []
    if not raw:
        warnings.append("تصویر فیش توضیح متنی ندارد؛ مبلغ و گیرنده دستی بررسی شود")
        return warnings
    digit_text = re.sub(r"\D", "", raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    if len(digit_text) < 6:
        warnings.append("مبلغ یا شماره پیگیری قابل تشخیص نیست")
    account_key = {
        "seller_toman": "seller_accounts_text",
        "seller_euro": "buyer_accounts_text",
    }.get(receipt_kind)
    account = (gate.get(account_key) or "").translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    ) if account_key else ""
    account_digits = re.sub(r"\D", "", account)
    if len(account_digits) >= 10 and len(digit_text) >= 6:
        suffix = account_digits[-4:]
        if suffix not in digit_text:
            warnings.append(f"چهار رقم آخر حساب مقصد ({suffix}) در متن فیش دیده نشد")
    return warnings


def _log_receipt_consistency(
    offer_id: int, gate: dict, text: str, *, receipt_kind: str
) -> list[str]:
    warnings = _receipt_consistency_warnings(
        gate, text, receipt_kind=receipt_kind
    )
    for warning in warnings:
        _log(int(offer_id), f"هشدار بررسی فیش: {warning}", from_role="system")
    return warnings


async def _expire_stale_deal_button(query, message: str) -> None:
    """Reject an old callback and remove only the keyboard that produced it."""
    try:
        await query.answer(message, show_alert=True)
    except Exception:
        pass
    try:
        if query.message:
            await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


def _party_callback_authorized(
    gate: dict | None,
    user_id: int,
    *,
    party: str,
    allowed_statuses: set[str],
) -> tuple[bool, str]:
    """Authorize a financial callback against role and current persisted stage."""
    if not gate:
        return False, "missing"
    role_key = "buyer_telegram_id" if party == "buyer" else "seller_telegram_id"
    if int(gate.get(role_key) or 0) != int(user_id):
        return False, "role"
    status = (gate.get("gate_status") or "").strip().lower()
    if status not in allowed_statuses:
        return False, "stage"
    return True, "ok"


async def _admin_receipt_upload_done(
    bot,
    update: Update,
    offer_id: int,
) -> None:
    """فیش ادمین: حذف آپلود از چت + به‌روزرسانی همان پیام اصلی معامله."""
    oid = int(offer_id)
    if update.message:
        chat_id = int(update.message.chat_id)
        await _delete_message_safe(bot, chat_id, int(update.message.message_id))
    await sync_deal_admin_notification(bot, oid, deal_complete=True)


async def _party_receipt_ack(
    update: Update,
    *,
    party: str,
    advert_rowid: int,
) -> None:
    """ثبت فیش — برای طرفین عادی؛ ادمین پیام جدا نمی‌گیرد."""
    if not update.message or not update.effective_user:
        return
    if update.effective_user.id in set(ADMIN_IDS or []):
        return
    kind = "تومان" if party == "buyer" else "یورو"
    await update.message.reply_text(
        f"{_RTL}✅ فیش {kind} ثبت شد · <b>آگهی {int(advert_rowid)}</b>\n"
        f"{_RTL}فیش بعدی همین‌جا بفرستید یا «انصراف».",
        parse_mode=ParseMode.HTML,
    )


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


# =============================================================================
# Section 4 | بخش ۴ — Receipt notify (buyer euro confirm)
# EN: Euro receipt copy to buyer for confirm; admin receipts live in sync_deal_admin_notification album.
# FA: کپی فیش یورو برای خریدار؛ فیش‌ها در آلبوم پیام اصلی ادمین (بدون پیام جدا).
# =============================================================================


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
        if entry_type in ("photo", "document") and file_id:
            cap = body
            if text.strip():
                cap += f"\n\n<i>{html_module.escape(text.strip()[:400])}</i>"
            cap_html = _photo_caption_html(cap)
            if entry_type == "document":
                await bot.send_document(
                    buyer_id,
                    file_id,
                    caption=cap_html,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            else:
                await bot.send_photo(
                    buyer_id,
                    file_id,
                    caption=cap_html,
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
    """واریز/فیش ادمین فقط پس از ثبت هر دو حساب (gate_status=completed)."""
    st = ((gate or {}).get("gate_status") or "").strip().lower()
    return st == "completed"


# =============================================================================
# Section 4b | بخش ۴ب — Admin proxy party actions (adm|pxy|)
# EN: Admin confirms, accounts, receipts on behalf of buyer/seller.
# FA: تأیید نهایی، حساب، فیش — از طرف کاربر توسط ادمین.
# =============================================================================


def _admin_proxy_receipt_prompt_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ انصراف",
                    callback_data=f"adm|pxy|{oid}|rcptcancel",
                )
            ],
        ]
    )


def _admin_proxy_account_prompt_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ انصراف",
                    callback_data=f"adm|pxy|{oid}|acccancel",
                )
            ],
        ]
    )


def _clear_admin_account_wizard(
    context: ContextTypes.DEFAULT_TYPE, admin_uid: int
) -> None:
    from models.enums import UserState

    try:
        context.application.user_data[admin_uid]["state"] = UserState.ADMIN_MENU.name
    except Exception:
        pass
    context.user_data.pop("admin_deal_acc_offer_id", None)
    context.user_data.pop("admin_deal_acc_party", None)
    try:
        from handlers.admin import _persist_admin_wizard_state

        _persist_admin_wizard_state(admin_uid, context)
    except Exception:
        pass


async def _admin_prompt_party_account(
    context: ContextTypes.DEFAULT_TYPE,
    q,
    offer_id: int,
    party: str,
) -> None:
    from models.enums import UserState
    from handlers.admin import _persist_admin_wizard_state

    admin_uid = int(q.from_user.id)
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not gate:
        await q.answer("معامله پیدا نشد", show_alert=True)
        return
    st = (gate.get("gate_status") or "").strip().lower()
    if st not in ("accounts", "completed"):
        await q.answer("این مرحله دیگر فعال نیست", show_alert=True)
        return
    row = get_advert_offer_joined(oid)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    is_buyer = party == "buyer"
    party_fa = "خریدار یورو" if is_buyer else "فروشنده یورو"
    hint = _account_collection_hint(is_buyer=is_buyer, advert=advert)
    context.user_data["state"] = UserState.ADMIN_DEAL_GATE_ACCOUNT.name
    context.user_data["admin_deal_acc_offer_id"] = oid
    context.user_data["admin_deal_acc_party"] = party
    _persist_admin_wizard_state(admin_uid, context)
    await q.answer()
    if is_buyer:
        prompt = (
            f"{_RTL}✏️ <b>ثبت حساب {party_fa}</b> — offer <code>{oid}</code>\n\n"
            f"{_RTL}متن حساب <b>دریافت یورو</b> (IBAN، PayPal…) یا <b>عکس کارت</b> بفرستید:\n\n"
            f"<pre>{html_module.escape(hint)}</pre>"
        )
    else:
        prompt = (
            f"{_RTL}✏️ <b>ثبت حساب {party_fa}</b> — offer <code>{oid}</code>\n\n"
            f"{_RTL}اطلاعات حساب <b>دریافت تومان</b> (شبا/کارت) یا <b>عکس کارت</b> بفرستید:\n\n"
            f"<pre>{html_module.escape(hint)}</pre>"
        )
    await context.bot.send_message(
        int(q.message.chat_id),
        prompt,
        parse_mode=ParseMode.HTML,
        reply_markup=_admin_proxy_account_prompt_keyboard(oid),
    )


async def _admin_proxy_party_final_yes(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    party: str,
    q,
) -> None:
    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "").strip().lower() != "pending":
        await q.answer("این مرحله دیگر فعال نیست", show_alert=True)
        return
    if party not in ("buyer", "seller"):
        return
    role_key = f"{party}_response"
    if (gate.get(role_key) or "").strip().lower() == "yes":
        await q.answer("قبلاً تأیید شده", show_alert=True)
        return
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    ts_key = "buyer_confirmed_at" if party == "buyer" else "seller_confirmed_at"
    party_fa = "خریدار" if party == "buyer" else "فروشنده"
    now = int(time.time())
    deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        **{role_key: "yes", ts_key: now},
    )
    _log(
        offer_id,
        f"ادمین به‌جای {party_fa}: تأیید نهایی (بله)",
        from_role="admin",
    )
    await q.answer(f"✅ تأیید {party_fa} ثبت شد")
    row = get_advert_offer_joined(offer_id)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    gate = deal_gate_get(offer_id) or gate
    br = (gate.get("buyer_response") or "").strip().lower()
    sr = (gate.get("seller_response") or "").strip().lower()
    if br == "yes" and sr == "yes":
        await _on_both_yes(context, offer_id, row, advert)
        return
    await sync_deal_admin_notification(
        context.bot, offer_id, deal_complete=False, text_only=True
    )


async def _admin_proxy_party_final_no(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    party: str,
    q,
) -> None:
    """Register a final refusal on behalf of one party and stop the deal."""
    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "").strip().lower() != "pending":
        await q.answer("این مرحله دیگر فعال نیست", show_alert=True)
        return
    if party not in ("buyer", "seller"):
        await q.answer()
        return
    role_key = f"{party}_response"
    current = (gate.get(role_key) or "").strip().lower()
    if current == "yes":
        await q.answer("این طرف قبلاً تأیید کرده است", show_alert=True)
        return
    if current == "no":
        await q.answer("قبلاً رد شده است", show_alert=True)
        return
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    ts_key = "buyer_confirmed_at" if party == "buyer" else "seller_confirmed_at"
    party_id = buyer_id if party == "buyer" else seller_id
    party_fa = "خریدار" if party == "buyer" else "فروشنده"
    deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        **{role_key: "no", ts_key: int(time.time())},
    )
    _log(
        offer_id,
        f"ادمین به‌جای {party_fa}: رد نهایی (خیر)",
        from_role="admin",
    )
    await q.answer(f"❌ رد {party_fa} ثبت شد")
    await _on_gate_rejected(
        context,
        offer_id,
        rejector_id=party_id,
        party=party_fa,
        acted_by_admin=True,
    )


async def _admin_begin_proxy_receipt(
    context: ContextTypes.DEFAULT_TYPE,
    q,
    offer_id: int,
    party: str,
) -> None:
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_party_receipts(gate):
        await q.answer("این معامله بسته شده", show_alert=True)
        return
    if party == "buyer":
        buyer_id = int(gate.get("buyer_telegram_id") or 0)
        card_sent = int(gate.get("buyer_toman_card_sent_at") or 0) > 0 or (
            _buyer_toman_card_delivered(oid, buyer_id) if buyer_id else False
        )
        if not card_sent:
            await q.answer("ابتدا کارت واریز به خریدار ارسال شود", show_alert=True)
            return
        kind_fa = "تومان"
    elif party == "seller":
        if not int(gate.get("seller_eur_account_sent_at") or 0) and not (
            _seller_buyer_eur_account_delivered(
                oid, int(gate.get("seller_telegram_id") or 0)
            )
        ):
            await q.answer("ابتدا حساب یورو به فروشنده ارسال شود", show_alert=True)
            return
        kind_fa = "یورو"
    else:
        return
    if _admin_receipt_pending_switch(context, oid):
        await q.answer("همین‌جا فیش بعدی را بفرستید یا انصراف", show_alert=True)
        return
    await q.answer()
    aid = int(gate["advert_rowid"])
    context.user_data[_DEAL_ADMIN_PXY_KEY] = {
        "offer_id": oid,
        "advert_rowid": aid,
        "party": party,
    }
    await context.bot.send_message(
        int(q.message.chat_id),
        f"{_RTL}📎 <b>ثبت فیش {kind_fa} (ادمین)</b>\n\n"
        f"{_RTL}آگهی <b>{aid}</b> · offer <code>{oid}</code>\n"
        f"{_RTL}عکس، PDF یا متن فیش را بفرستید. چند فیش هم می‌توانید بفرستید.\n\n"
        f"{_RTL}⚠️ قبل از ارسال شمارهٔ <b>آگهی</b> را چک کنید.",
        parse_mode=ParseMode.HTML,
        reply_markup=_admin_proxy_receipt_prompt_keyboard(oid),
    )


async def deal_admin_party_proxy_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ادمین: انجام مراحل طرفین از پیام اصلی معامله."""
    q = update.callback_query
    if not q or not q.from_user:
        return
    if not await _require_full_deal_admin(q):
        return
    parts = (q.data or "").split("|")
    if len(parts) != 4 or parts[0] != "adm" or parts[1] != "pxy":
        return
    try:
        oid = int(parts[2])
    except (TypeError, ValueError):
        return
    action = parts[3]
    if action == "acccancel":
        oid_raw = context.user_data.get("admin_deal_acc_offer_id")
        _clear_admin_account_wizard(context, q.from_user.id)
        _clear_deal_admin_proxy_pending(context)
        try:
            await q.answer("انصراف")
        except Exception:
            pass
        try:
            oid = int(oid_raw) if oid_raw is not None else 0
        except (TypeError, ValueError):
            oid = 0
        if oid > 0:
            from handlers.deal_gate import admin_show_deal_gate_detail

            await admin_show_deal_gate_detail(update, context, oid)
        else:
            try:
                if q.message:
                    await q.message.delete()
            except Exception:
                pass
        return
    if action == "rcptcancel":
        _clear_deal_admin_proxy_pending(context)
        try:
            await q.answer("انصراف")
        except Exception:
            pass
        try:
            if q.message:
                await q.message.delete()
        except Exception:
            pass
        return
    if action == "byes":
        await _admin_proxy_party_final_yes(context, oid, "buyer", q)
        return
    if action == "syes":
        await _admin_proxy_party_final_yes(context, oid, "seller", q)
        return
    if action == "bno":
        await _admin_proxy_party_final_no(context, oid, "buyer", q)
        return
    if action == "sno":
        await _admin_proxy_party_final_no(context, oid, "seller", q)
        return
    if action == "bacc":
        await _admin_prompt_party_account(context, q, oid, "buyer")
        return
    if action == "sacc":
        await _admin_prompt_party_account(context, q, oid, "seller")
        return
    if action == "brcpt":
        await _admin_begin_proxy_receipt(context, q, oid, "buyer")
        return
    if action == "srcpt":
        await _admin_begin_proxy_receipt(context, q, oid, "seller")
        return


async def _deal_admin_proxy_receipt_try_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if not update.message or not update.effective_user:
        return False
    pending = context.user_data.get(_DEAL_ADMIN_PXY_KEY)
    if not isinstance(pending, dict):
        return False
    if update.effective_user.id not in set(ADMIN_IDS or []):
        return False
    oid = int(pending.get("offer_id") or 0)
    party = (pending.get("party") or "").strip().lower()
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_party_receipts(gate):
        _clear_deal_admin_proxy_pending(context)
        return False
    pending_aid = int(pending.get("advert_rowid") or 0)
    gate_aid = int(gate.get("advert_rowid") or 0)
    if pending_aid and gate_aid and pending_aid != gate_aid:
        _clear_deal_admin_proxy_pending(context)
        await update.message.reply_text(
            f"{_RTL}❌ فلو فیش با آگهی <b>{pending_aid}</b> شروع شده بود ولی دادهٔ معامله "
            f"مغایر است — دوباره از دکمهٔ فیش همان آگهی شروع کنید.",
            parse_mode=ParseMode.HTML,
        )
        return True
    text = (update.message.text or "").strip()
    if not text or len(text) < 2:
        await update.message.reply_text(f"{_RTL}متن فیش را کامل‌تر بفرستید.")
        return True
    if party == "seller":
        items = deal_gate_append_seller_receipt(
            oid,
            entry_type="text",
            text=text,
            source_message_id=update.message.message_id,
        )
        gate = deal_gate_get(oid) or gate
        _log_receipt_consistency(oid, gate, text, receipt_kind="seller_euro")
        idx = len(items) - 1
        _log(oid, f"ادمین — فیش یورو متنی فروشنده", from_role="admin")
        await _notify_buyer_euro_receipt_confirm(
            context.bot,
            offer_id=oid,
            gate=gate,
            receipt_index=idx,
            entry_type="text",
            text=text,
        )
    else:
        deal_gate_append_buyer_receipt(
            oid,
            entry_type="text",
            text=text,
            source_message_id=update.message.message_id,
        )
        gate = deal_gate_get(oid) or gate
        _log(oid, f"ادمین — فیش تومان متنی خریدار", from_role="admin")
    await _admin_receipt_upload_done(context.bot, update, oid)
    return True


async def _deal_admin_proxy_receipt_try_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if not update.message or not update.effective_user:
        return False
    pending = context.user_data.get(_DEAL_ADMIN_PXY_KEY)
    if not isinstance(pending, dict):
        return False
    if update.effective_user.id not in set(ADMIN_IDS or []):
        return False
    oid = int(pending.get("offer_id") or 0)
    party = (pending.get("party") or "").strip().lower()
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_party_receipts(gate):
        _clear_deal_admin_proxy_pending(context)
        return False
    pending_aid = int(pending.get("advert_rowid") or 0)
    gate_aid = int(gate.get("advert_rowid") or 0)
    if pending_aid and gate_aid and pending_aid != gate_aid:
        _clear_deal_admin_proxy_pending(context)
        await update.message.reply_text(
            f"{_RTL}❌ فلو فیش با آگهی <b>{pending_aid}</b> شروع شده بود ولی دادهٔ معامله "
            f"مغایر است — دوباره از دکمهٔ فیش همان آگهی شروع کنید.",
            parse_mode=ParseMode.HTML,
        )
        return True
    extracted = _extract_receipt_file_id(update.message)
    if not extracted:
        return False
    fid, media_kind = extracted
    cap = (update.message.caption or "").strip()
    entry_type = "document" if media_kind == "document" else "photo"
    if party == "seller":
        items = deal_gate_append_seller_receipt(
            oid,
            entry_type=entry_type,
            text=cap,
            file_id=fid,
            source_message_id=update.message.message_id,
        )
        gate = deal_gate_get(oid) or gate
        idx = len(items) - 1
        _log(oid, f"ادمین — فیش یورو {entry_type} فروشنده", from_role="admin")
        await _notify_buyer_euro_receipt_confirm(
            context.bot,
            offer_id=oid,
            gate=gate,
            receipt_index=idx,
            entry_type=entry_type,
            text=cap,
            file_id=fid,
        )
    else:
        deal_gate_append_buyer_receipt(
            oid,
            entry_type=entry_type,
            text=cap,
            file_id=fid,
            source_message_id=update.message.message_id,
        )
        gate = deal_gate_get(oid) or gate
        _log(oid, f"ادمین — فیش تومان {entry_type} خریدار", from_role="admin")
    await _admin_receipt_upload_done(context.bot, update, oid)
    return True


# =============================================================================
# Section 5 | بخش ۵ — Admin: Toman deposit card to buyer (adm|pay|)
# EN: Pick bank card from BANK_CARDS; send with receipt upload buttons.
# FA: انتخاب کارت از تنظیمات؛ ارسال به خریدار با دکمه ارسال فیش.
# =============================================================================


def _buyer_toman_deposit_message_html(
    *,
    advert_id: int,
    offer_sequence: int,
    euro_amount: int,
    toman_amount: int,
    card_html: str,
) -> str:
    """Build the buyer deposit instruction with one copyable amount+unit."""
    from handlers.offers import _copyable_toman_html

    return (
        f"{_RTL}💳 <b>حساب واریز تومان (امانت)</b>\n\n"
        f"{_RTL}آگهی <b>{int(advert_id)}</b> · پیشنهاد <b>{int(offer_sequence)}</b>\n"
        f"{_RTL}💶 <b>{int(euro_amount):,}</b> یورو\n\n"
        f"{_RTL}لطفاً مبلغ {_copyable_toman_html(int(toman_amount))} را "
        f"به حساب زیر واریز کنید:\n\n"
        f"{card_html}\n\n"
        f"{_RTL}📝 <b>توضیحات:</b>\n"
        f"{_RTL}• این مبلغ به‌صورت <b>امانت</b> نزد ادمین می‌ماند تا "
        f"فروشنده یورو را به حساب شما واریز کند.\n"
        f"{_RTL}• پس از واریز، دکمهٔ <b>ارسال فیش واریزی</b> را بزنید.\n"
        f"{_RTL}• تا تأیید ادمین، مبلغ دیگری واریز نکنید.\n"
    )


async def deal_admin_payment_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ادمین: انتخاب کارت و ارسال حساب واریز تومان به خریدار."""
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    if not await _require_full_deal_admin(q):
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
        await _expire_stale_deal_button(q, "معامله در مرحلهٔ واریز نیست")
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

    msg = _buyer_toman_deposit_message_html(
        advert_id=aid,
        offer_sequence=seq,
        euro_amount=eur_amt,
        toman_amount=amount,
        card_html=card_html,
    )
    recipient_id = buyer_id
    party_fa = "خریدار"
    party = "buyer"
    tag = "کارت واریز تومان به خریدار"

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

    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=int(gate["seller_telegram_id"]),
        buyer_toman_card_sent_at=int(time.time()),
    )
    gate = deal_gate_get(oid) or gate

    _log(
        oid,
        f"ادمین حساب واریز ({picked.title}) برای {party_fa} ارسال کرد — {amount:,} تومان",
        from_role="admin",
    )
    await sync_deal_admin_notification(
        context.bot, oid, deal_complete=True, text_only=True
    )
    await refresh_admin_deal_markup(context.bot, oid)
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


# =============================================================================
# Section 6 | بخش ۶ — Admin: tomset, eurcfm, stom
# EN: Toman settled → EUR account to seller; admin euro confirm; toman receipt to seller.
# FA: تومان نشست → حساب یورو به فروشنده؛ یورو نشست ادمین؛ فیش تومان به فروشنده.
# Callbacks: adm|tomset|, adm|eurcfm|, adm|stom|, adm|buyeur| (legacy)
# =============================================================================


async def _send_buyer_eur_account_to_seller(
    context: ContextTypes.DEFAULT_TYPE,
    oid: int,
    gate: dict,
    *,
    q=None,
    force_resend: bool = False,
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

    if (
        not force_resend
        and int(gate.get("seller_eur_account_sent_at") or 0) > 0
        and _seller_buyer_eur_account_delivered(oid, seller_id)
    ):
        return True
    if int(gate.get("seller_eur_account_sent_at") or 0) > 0 and not force_resend:
        if not _seller_buyer_eur_account_delivered(oid, seller_id):
            deal_gate_upsert(
                offer_id=oid,
                advert_rowid=int(gate["advert_rowid"]),
                buyer_telegram_id=int(gate["buyer_telegram_id"]),
                seller_telegram_id=seller_id,
                seller_eur_account_sent_at=0,
            )

    photo_intro = (
        f"{_RTL}📤 <b>حساب دریافت یورو — خریدار</b>\n\n"
        f"{_RTL}آگهی <b>{aid}</b> · پیشنهاد <b>{seq}</b>\n"
        f"{_RTL}💶 <b>{eur_amt:,}</b> یورو\n\n"
        f"{_RTL}لطفاً <b>همین مقدار</b> یورو را به حساب زیر (عکس) واریز کنید:\n\n"
        f"{_seller_euro_transfer_rules_html()}"
        f"{_RTL}پس از انتقال، دکمهٔ <b>ارسال فیش واریزی یورو</b> را بزنید.\n"
        f"{_RTL}تا تأیید ادمین، مبلغ دیگری ارسال نکنید."
    )

    pay_kb = _seller_euro_pay_keyboard(oid)
    from utils.deal_outbound import deal_bot_send_message, deal_bot_send_photo

    tag = _BUYER_EUR_ACCOUNT_TO_SELLER_TAG
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
        deal_gate_upsert(
            offer_id=oid,
            advert_rowid=int(gate["advert_rowid"]),
            buyer_telegram_id=int(gate["buyer_telegram_id"]),
            seller_telegram_id=seller_id,
            seller_eur_account_sent_at=int(time.time()),
        )
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
    if not await _require_full_deal_admin(q):
        return
    parts = (q.data or "").split("|")
    if len(parts) not in (3, 4) or parts[0] != "adm" or parts[1] != "tomset":
        return
    try:
        oid = int(parts[2])
    except (TypeError, ValueError):
        return
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_admin_payment(gate):
        await q.answer("معامله در این مرحله نیست", show_alert=True)
        await refresh_admin_deal_markup(context.bot, oid)
        return
    buyer_id = int(gate.get("buyer_telegram_id") or 0)
    card_ok = int(gate.get("buyer_toman_card_sent_at") or 0) > 0 or (
        _buyer_toman_card_delivered(oid, buyer_id) if buyer_id else False
    )
    if not card_ok:
        await q.answer("ابتدا کارت واریز به خریدار ارسال شود.", show_alert=True)
        await refresh_admin_deal_markup(context.bot, oid)
        return
    if not await _admin_sensitive_confirmation(
        context,
        q,
        action="buyer_toman_settled",
        offer_id=oid,
        confirm_data=f"adm|tomset|{oid}|yes",
        prompt="✅ تأیید نهایی دریافت تومان خریدار",
        is_confirmation=len(parts) == 4 and parts[3] == "yes",
    ):
        return
    now = int(time.time())
    upsert_kw: dict = {
        "offer_id": oid,
        "advert_rowid": int(gate["advert_rowid"]),
        "buyer_telegram_id": int(gate["buyer_telegram_id"]),
        "seller_telegram_id": int(gate["seller_telegram_id"]),
        "buyer_toman_settled_at": now,
    }
    if not int(gate.get("buyer_toman_card_sent_at") or 0):
        upsert_kw["buyer_toman_card_sent_at"] = now
    deal_gate_upsert(**upsert_kw)
    gate = deal_gate_get(oid) or gate
    _log(oid, "ادمین تأیید کرد: تومان نشست", from_role="admin")
    ok = await _send_buyer_eur_account_to_seller(context, oid, gate, q=q)
    if not ok:
        deal_gate_upsert(
            offer_id=oid,
            advert_rowid=int(gate["advert_rowid"]),
            buyer_telegram_id=int(gate["buyer_telegram_id"]),
            seller_telegram_id=int(gate["seller_telegram_id"]),
            buyer_toman_settled_at=None,
        )
        await refresh_admin_deal_markup(context.bot, oid)
        return
    gate = deal_gate_get(oid) or gate
    from utils.deal_milestones import notify_toman_settled_buyer

    await notify_toman_settled_buyer(context.bot, offer_id=oid, gate=gate)
    await _refresh_admin_deal_after_payment_step(context.bot, oid, update_text=True)
    await q.answer("✅ تومان نشست — حساب یورو برای فروشنده ارسال شد", show_alert=True)
    try:
        await q.message.edit_reply_markup(
            reply_markup=deal_admin_payment_actions_keyboard(
                oid, deal_gate_get(oid)
            )
        )
    except Exception:
        pass


async def deal_admin_send_buyer_eur_account_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ارسال (یا ارسال مجدد) حساب یوروی خریدار به فروشنده."""
    q = update.callback_query
    if not q or not q.from_user:
        return
    if not await _require_full_deal_admin(q):
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
        seller_id = int(gate.get("seller_telegram_id") or 0)
        was_delivered = _seller_buyer_eur_account_delivered(oid, seller_id)
        ok = await _send_buyer_eur_account_to_seller(
            context, oid, gate, q=q, force_resend=True
        )
        if ok:
            await sync_deal_admin_notification(
                context.bot, oid, deal_complete=True, text_only=True
            )
            await refresh_admin_deal_markup(context.bot, oid)
            try:
                if was_delivered:
                    await q.answer(
                        "✅ حساب یورو دوباره برای فروشنده ارسال شد",
                        show_alert=True,
                    )
                else:
                    await q.answer(
                        "✅ حساب یورو برای فروشنده ارسال شد",
                        show_alert=True,
                    )
            except Exception:
                pass
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
            await _expire_stale_deal_button(answer_query, "معامله پیدا نشد")
        return
    items = deal_gate_seller_receipt_list(offer_id)
    if not items:
        if answer_query:
            await _expire_stale_deal_button(answer_query, "فیشی ثبت نشده")
        return
    if not deal_gate_confirm_seller_receipt_buyer(
        offer_id, receipt_index, confirmed_by=confirmed_by
    ):
        if answer_query:
            await _expire_stale_deal_button(
                answer_query, "این تأیید قبلاً استفاده شده یا دیگر معتبر نیست."
            )
        return
    gate = deal_gate_get(offer_id) or gate
    role = "buyer" if confirmed_by == "buyer" else "admin"
    who = "خریدار" if confirmed_by == "buyer" else "ادمین"
    _log(offer_id, "تأیید شد: یورو نشست", from_role=role)
    seller_id = int(gate.get("seller_telegram_id") or 0)
    if seller_id:
        from utils.deal_milestones import notify_euro_settled_seller

        try:
            await notify_euro_settled_seller(
                context.bot,
                offer_id=offer_id,
                gate=gate,
                who_fa=who,
            )
            await _show_user_main_menu(context, seller_id)
        except Exception as e:
            logger.warning(
                "deal_eurset: notify seller=%s offer=%s: %s",
                seller_id,
                offer_id,
                e,
            )
    await sync_deal_admin_notification(
        context.bot, offer_id, deal_complete=True, text_only=True
    )
    if answer_query:
        try:
            await answer_query.answer("✅ یورو نشست ثبت شد", show_alert=True)
        except Exception:
            pass
        try:
            if answer_query.message:
                await answer_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        if answer_query.from_user.id not in set(ADMIN_IDS or []):
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
    if not await _require_full_deal_admin(q):
        return
    parts = (q.data or "").split("|")
    if len(parts) not in (4, 5) or parts[0] != "adm" or parts[1] != "eurcfm":
        return
    try:
        oid = int(parts[2])
        ridx = int(parts[3])
    except (TypeError, ValueError):
        return
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_admin_payment(gate):
        await _expire_stale_deal_button(q, "معامله در این مرحله نیست")
        return
    if not await _admin_sensitive_confirmation(
        context,
        q,
        action=f"euro_settled:{ridx}",
        offer_id=oid,
        confirm_data=f"adm|eurcfm|{oid}|{ridx}|yes",
        prompt="✅ تأیید نهایی دریافت یورو",
        is_confirmation=len(parts) == 5 and parts[4] == "yes",
    ):
        return
    await _apply_euro_settled(
        context,
        offer_id=oid,
        receipt_index=ridx,
        confirmed_by="admin",
        answer_query=q,
    )


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
    if not await _require_full_deal_admin(q):
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
        await _expire_stale_deal_button(q, "معامله در این مرحله نیست")
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
        return
    if action != "go":
        await q.answer()
        return
    if _admin_receipt_pending_switch(context, oid):
        await q.answer("همین‌جا فیش بعدی را بفرستید یا انصراف.", show_alert=True)
        return
    await q.answer()
    aid = int(gate["advert_rowid"])
    context.user_data[_DEAL_ADMIN_STOM_KEY] = {
        "offer_id": oid,
        "advert_rowid": aid,
    }
    try:
        sent = await context.bot.send_message(
            uid,
            f"{_RTL}📎 <b>فیش واریز تومان به فروشنده</b>\n\n"
            f"{_RTL}آگهی <b>{aid}</b> · offer <code>{oid}</code>\n"
            f"{_RTL}عکس یا متن هر فیش را بفرستید.\n"
            f"{_RTL}یک پرداخت ممکن است ۲–۳ فیش باشد — "
            f"همه را بفرستید؛ با «انصراف» خارج می‌شوید.\n\n"
            f"{_RTL}⚠️ قبل از ارسال شمارهٔ <b>آگهی</b> را چک کنید.",
            parse_mode=ParseMode.HTML,
            reply_markup=_admin_seller_toman_prompt_keyboard(oid),
        )
        _track_rcpt_prompt_msg(user_data_store, uid, oid, sent.message_id)
    except Exception:
        logger.exception("deal_stom: prompt failed offer=%s", oid)


async def deal_admin_seller_toman_settled_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin confirms seller received Toman and closes that specific deal."""
    q = update.callback_query
    if not q or not q.from_user:
        return
    if not await _require_full_deal_admin(q):
        return
    parts = (q.data or "").split("|")
    if len(parts) not in (3, 4) or parts[0] != "adm" or parts[1] != "stomset":
        return
    try:
        oid = int(parts[2])
    except (TypeError, ValueError):
        return

    gate = deal_gate_get(oid)
    if not gate:
        await q.answer("معامله پیدا نشد.", show_alert=True)
        return
    if (
        (gate.get("gate_status") or "").strip().lower() == "closed"
        or int(gate.get("seller_toman_settled_at") or 0) > 0
    ):
        await _expire_stale_deal_button(
            q, "این معامله قبلاً بسته شده یا دریافت تومان تأیید شده است."
        )
        await refresh_admin_deal_markup(context.bot, oid)
        return
    from handlers.offers import _seller_euro_fully_confirmed_gate

    if not (
        _gate_awaiting_seller_toman_close(gate)
        or _seller_euro_fully_confirmed_gate(gate)
    ):
        await q.answer(
            "این معامله هنوز به مرحلهٔ نهایی پرداخت به فروشنده نرسیده است.",
            show_alert=True,
        )
        await refresh_admin_deal_markup(context.bot, oid)
        return

    if not await _admin_sensitive_confirmation(
        context,
        q,
        action="seller_toman_settled",
        offer_id=oid,
        confirm_data=f"adm|stomset|{oid}|yes",
        prompt="✅ تأیید نهایی دریافت تومان فروشنده",
        is_confirmation=len(parts) == 4 and parts[3] == "yes",
    ):
        return

    row = get_advert_offer_joined(oid)
    if not row:
        await q.answer("پیشنهاد پیدا نشد.", show_alert=True)
        return
    now = int(time.time())
    if not deal_gate_settle_and_close_atomic(
        oid,
        int(row["advert_rowid"]),
        settled_at=now,
        require_receipt=False,
    ):
        await q.answer(
            "این معامله هم‌زمان تأیید شد یا دیگر در مرحله پایان نیست.",
            show_alert=True,
        )
        await refresh_admin_deal_markup(context.bot, oid)
        return
    gate = deal_gate_get(oid) or {**gate, "seller_toman_settled_at": now}
    _log(
        oid,
        "ادمین از طرف فروشنده تأیید کرد: تومان نشست",
        from_role="admin",
    )
    await _finalize_deal_close(
        context,
        oid,
        gate,
        row,
        closed_by="admin",
        answer_query=q,
        persist_close=False,
    )


def _seller_toman_delivery_payload(
    *,
    offer_id: int,
    entry_type: str,
    text: str,
    file_id: str,
    body_html: str,
    source_chat_id: int = 0,
    source_message_id: int = 0,
) -> dict:
    return {
        "offer_id": int(offer_id),
        "entry_type": (entry_type or "text").strip().lower(),
        "receipt_text": (text or "")[:2000],
        "file_id": (file_id or "").strip(),
        "body_html": body_html,
        "keyboard": "seller_toman_settled",
        "after_hook": "record_seller_toman_receipt",
        "source_chat_id": int(source_chat_id or 0),
        "source_message_id": int(source_message_id or 0),
    }


async def _deliver_deal_queue_item(bot, delivery: dict) -> bool:
    """Deliver one durable item. Repeated calls never resend a completed row."""
    if (delivery.get("status") or "").strip().lower() == "sent":
        return True
    delivery_id = int(delivery["id"])
    if not deal_delivery_claim(delivery_id):
        return False
    oid = int(delivery["offer_id"])
    chat_id = int(delivery["recipient_telegram_id"])
    payload = json.loads(delivery.get("payload_json") or "{}")
    payload_type = (delivery.get("payload_type") or "text").strip().lower()
    body = payload.get("body_html") or ""
    markup = None
    if payload.get("keyboard") == "seller_toman_settled":
        markup = seller_toman_settled_keyboard(oid)
    try:
        if payload_type == "photo":
            sent = await bot.send_photo(
                chat_id,
                payload.get("file_id") or "",
                caption=_photo_caption_html(body),
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        elif payload_type == "document":
            sent = await bot.send_document(
                chat_id,
                payload.get("file_id") or "",
                caption=_photo_caption_html(body),
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        else:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=body,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        message_id = int(getattr(sent, "message_id", 0) or 0)
        if payload.get("after_hook") == "record_seller_toman_receipt":
            recorded = deal_gate_record_seller_toman_delivery(
                oid,
                entry_type=payload.get("entry_type") or payload_type,
                text=payload.get("receipt_text") or "",
                file_id=payload.get("file_id") or "",
                delivery_key=delivery.get("dedupe_key") or str(delivery_id),
                queue_delivery_id=delivery_id,
                telegram_message_id=message_id,
            )
            if not recorded:
                # Telegram already accepted the message. Never retry and duplicate it;
                # leave the stage mismatch for the admin repair audit.
                deal_delivery_mark_sent(delivery_id, message_id)
                logger.error(
                    "deal_delivery: receipt delivered but stage changed offer=%s id=%s",
                    oid,
                    delivery_id,
                )
        else:
            deal_delivery_mark_sent(delivery_id, message_id)
        try:
            bot_outbound_log_insert(
                oid,
                chat_id,
                delivery.get("party") or "user",
                delivery.get("tag") or "پیام معامله",
                msg_type=payload_type,
                body_html=body if payload_type == "text" else None,
                caption_html=body if payload_type != "text" else None,
                photo_file_id=(payload.get("file_id") or "") if payload_type != "text" else None,
                telegram_message_id=message_id,
            )
        except Exception:
            logger.exception("deal_delivery: outbound log failed id=%s", delivery_id)
        source_chat_id = int(payload.get("source_chat_id") or 0)
        source_message_id = int(payload.get("source_message_id") or 0)
        if source_chat_id > 0 and source_message_id > 0:
            await _delete_message_safe(bot, source_chat_id, source_message_id)
        return True
    except RetryAfter as exc:
        retry_after = getattr(exc, "retry_after", 1)
        try:
            seconds = int(retry_after.total_seconds())
        except AttributeError:
            seconds = int(retry_after or 1)
        deal_delivery_defer_rate_limit(delivery_id, seconds, str(exc))
        logger.info(
            "deal_delivery: Telegram rate limit id=%s retry_after=%s",
            delivery_id,
            seconds,
        )
        return False
    except Exception as exc:
        deal_delivery_mark_failed(delivery_id, str(exc))
        logger.warning(
            "deal_delivery: failed id=%s offer=%s chat=%s: %s",
            delivery_id,
            oid,
            chat_id,
            exc,
        )
        return False


async def _enqueue_and_deliver_deal_message(
    bot,
    *,
    offer_id: int,
    chat_id: int,
    party: str,
    tag: str,
    payload_type: str,
    payload: dict,
    dedupe_key: str,
) -> bool:
    delivery = deal_delivery_enqueue(
        offer_id=int(offer_id),
        recipient_telegram_id=int(chat_id),
        party=party,
        tag=tag,
        payload_type=payload_type,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    return await _deliver_deal_queue_item(bot, delivery)


async def run_deal_delivery_retry_sweep(bot) -> int:
    """Quietly retry critical messages; users receive no separate failure notices."""
    delivered = 0
    touched_offers: set[int] = set()
    for item in deal_delivery_due(limit=50):
        if await _deliver_deal_queue_item(bot, item):
            delivered += 1
            touched_offers.add(int(item.get("offer_id") or 0))
    for oid in touched_offers:
        if oid <= 0 or not deal_gate_get(oid):
            continue
        try:
            await sync_deal_admin_notification(bot, oid, deal_complete=True)
        except Exception:
            logger.exception("deal_delivery: admin resync failed offer=%s", oid)
    return delivered


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
    if not gate or not _deal_gate_allows_party_receipts(gate):
        _clear_deal_admin_stom_pending(context)
        if update.message and gate and not _deal_gate_allows_party_receipts(gate):
            await update.message.reply_text(
                f"{_RTL}این معامله بسته شده — ارسال فیش جدید ممکن نیست."
            )
        return False
    pending_aid = int(pending.get("advert_rowid") or 0)
    gate_aid = int(gate.get("advert_rowid") or 0)
    if pending_aid and gate_aid and pending_aid != gate_aid:
        _clear_deal_admin_stom_pending(context)
        await update.message.reply_text(
            f"{_RTL}❌ فلو فیش با آگهی <b>{pending_aid}</b> شروع شده بود ولی دادهٔ معامله "
            f"مغایر است — دوباره از دکمهٔ فیش همان آگهی شروع کنید.",
            parse_mode=ParseMode.HTML,
        )
        return True
    text = (update.message.text or "").strip()
    if not text or len(text) < 2:
        await update.message.reply_text(f"{_RTL}متن فیش را کامل‌تر بفرستید.")
        return True
    seller_id = int(gate.get("seller_telegram_id") or 0)
    receipt_warnings = _log_receipt_consistency(
        oid, gate, text, receipt_kind="seller_toman"
    )
    row = get_advert_offer_joined(oid)
    seq = int((row or {}).get("seq_in_advert") or oid)
    body = (
        f"{_RTL}💳 <b>فیش واریز تومان</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b>\n\n"
        f"{_RTL}ادمین فیش واریز تومان به شما را ارسال کرد:\n\n"
        f"<pre>{html_module.escape(text[:3500])}</pre>"
    )
    dedupe_key = (
        f"seller_toman:{oid}:{int(update.effective_user.id)}:"
        f"{int(update.message.message_id)}"
    )
    delivered = await _enqueue_and_deliver_deal_message(
        context.bot,
        offer_id=oid,
        chat_id=seller_id,
        party="seller",
        tag="فیش تومان از ادمین",
        payload_type="text",
        payload=_seller_toman_delivery_payload(
            offer_id=oid,
            entry_type="text",
            text=text,
            file_id="",
            body_html=body,
            source_chat_id=update.message.chat_id,
            source_message_id=update.message.message_id,
        ),
        dedupe_key=dedupe_key,
    )
    if delivered:
        if receipt_warnings:
            await context.bot.send_message(
                chat_id=int(update.effective_user.id),
                text="⚠️ بررسی دستی فیش: " + "؛ ".join(receipt_warnings),
            )
        gate = deal_gate_get(oid) or gate
        from utils.deal_milestones import notify_toman_to_seller_buyer

        await notify_toman_to_seller_buyer(context.bot, offer_id=oid, gate=gate)
        schedule_seller_stom_close_reminder(context.application, oid)
        _log(oid, "ادمین فیش تومان برای فروشنده فرستاد (متن)", from_role="admin")
        await _admin_receipt_upload_done(context.bot, update, oid)
    else:
        _log(oid, "ارسال فیش تومان در صف تلاش مجدد (متن)", from_role="admin")
        await update.message.reply_text(
            f"{_RTL}⏳ ارسال فیش ناموفق بود و بدون پیام اضافه برای کاربر، خودکار دوباره تلاش می‌شود."
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
    if not gate or not _deal_gate_allows_party_receipts(gate):
        _clear_deal_admin_stom_pending(context)
        if update.message and gate and not _deal_gate_allows_party_receipts(gate):
            await update.message.reply_text(
                f"{_RTL}این معامله بسته شده — ارسال فیش جدید ممکن نیست."
            )
        return False
    pending_aid = int(pending.get("advert_rowid") or 0)
    gate_aid = int(gate.get("advert_rowid") or 0)
    if pending_aid and gate_aid and pending_aid != gate_aid:
        _clear_deal_admin_stom_pending(context)
        await update.message.reply_text(
            f"{_RTL}❌ فلو فیش با آگهی <b>{pending_aid}</b> شروع شده بود ولی دادهٔ معامله "
            f"مغایر است — دوباره از دکمهٔ فیش همان آگهی شروع کنید.",
            parse_mode=ParseMode.HTML,
        )
        return True
    fid_extracted = _extract_receipt_file_id(update.message)
    if not fid_extracted:
        return False
    fid, media_kind = fid_extracted
    entry_type = "document" if media_kind == "document" else "photo"
    cap = (update.message.caption or "").strip()
    seller_id = int(gate.get("seller_telegram_id") or 0)
    receipt_warnings = _log_receipt_consistency(
        oid, gate, cap, receipt_kind="seller_toman"
    )
    row = get_advert_offer_joined(oid)
    seq = int((row or {}).get("seq_in_advert") or oid)
    body = (
        f"{_RTL}💳 <b>فیش واریز تومان</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b>\n\n"
        f"{_RTL}ادمین فیش واریز تومان به شما را ارسال کرد."
    )
    if cap:
        body += f"\n\n<i>{html_module.escape(cap[:400])}</i>"
    dedupe_key = (
        f"seller_toman:{oid}:{int(update.effective_user.id)}:"
        f"{int(update.message.message_id)}"
    )
    delivered = await _enqueue_and_deliver_deal_message(
        context.bot,
        offer_id=oid,
        chat_id=seller_id,
        party="seller",
        tag="فیش تومان از ادمین",
        payload_type=entry_type,
        payload=_seller_toman_delivery_payload(
            offer_id=oid,
            entry_type=entry_type,
            text=cap,
            file_id=fid,
            body_html=body,
            source_chat_id=update.message.chat_id,
            source_message_id=update.message.message_id,
        ),
        dedupe_key=dedupe_key,
    )
    if delivered:
        if receipt_warnings:
            await context.bot.send_message(
                chat_id=int(update.effective_user.id),
                text="⚠️ بررسی دستی فیش: " + "؛ ".join(receipt_warnings),
            )
        gate = deal_gate_get(oid) or gate
        from utils.deal_milestones import notify_toman_to_seller_buyer

        await notify_toman_to_seller_buyer(context.bot, offer_id=oid, gate=gate)
        schedule_seller_stom_close_reminder(context.application, oid)
        _log(oid, f"ادمین فیش تومان برای فروشنده فرستاد ({entry_type})", from_role="admin")
        await _admin_receipt_upload_done(context.bot, update, oid)
    else:
        _log(oid, f"ارسال فیش تومان در صف تلاش مجدد ({entry_type})", from_role="admin")
        await update.message.reply_text(
            f"{_RTL}⏳ ارسال فیش ناموفق بود و بدون پیام اضافه برای کاربر، خودکار دوباره تلاش می‌شود."
        )
    return True


# =============================================================================
# Section 7 | بخش ۷ — Outbound log replay (adm|outlog|)
# EN: Replay deal_bot_send_* messages logged in offer_bot_outbound_log.
# FA: بازپخش پیام‌های ذخیره‌شدهٔ ربات به خریدار/فروشنده برای ادمین.
# =============================================================================


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


# =============================================================================
# Section 8 | بخش ۸ — Account collection
# EN: Collect buyer/seller EUR account text or photo; purge temp messages.
# FA: دریافت حساب یورو طرفین؛ پاک‌سازی پیام‌های موقت و اعلان انتظار.
# =============================================================================


def _party_role_fa(gate: dict, user_id: int) -> str:
    uid = int(user_id)
    if uid == int(gate.get("buyer_telegram_id") or 0):
        return "خریدار یورو"
    if uid == int(gate.get("seller_telegram_id") or 0):
        return "فروشنده یورو"
    return "کاربر"


def _party_deal_identity_html(
    gate: dict, user_id: int, *, stage_fa: str
) -> str:
    """Private/admin-safe deal identity; never added to the public advert."""
    oid = int(gate.get("offer_id") or 0)
    aid = int(gate.get("advert_rowid") or 0)
    row = get_advert_offer_joined(oid) if oid else None
    seq = int((row or {}).get("seq_in_advert") or oid)
    role = _party_role_fa(gate, int(user_id))
    return (
        f"{_RTL}🧾 <b>مشخصات معامله</b>\n"
        f"{_RTL}آگهی <b>{aid}</b> · پیشنهاد <b>{seq}</b> · کد معامله <code>{oid}</code>\n"
        f"{_RTL}نقش شما: <b>{html_module.escape(role)}</b>\n"
        f"{_RTL}مرحله فعلی: <b>{html_module.escape(stage_fa)}</b>\n\n"
    )


def _account_deal_pick_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📝 انتخاب این معامله و ارسال حساب",
                    callback_data=f"deal|accpick|{oid}",
                )
            ]
        ]
    )


def _account_deal_choices_keyboard(
    gates: list[dict], user_id: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for gate in gates[:20]:
        oid = int(gate.get("offer_id") or 0)
        aid = int(gate.get("advert_rowid") or 0)
        row = get_advert_offer_joined(oid) if oid else None
        seq = int((row or {}).get("seq_in_advert") or oid)
        role = _party_role_fa(gate, int(user_id))
        rows.append(
            [
                InlineKeyboardButton(
                    f"آگهی {aid} · پیشنهاد {seq} · {role}",
                    callback_data=f"deal|accpick|{oid}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


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


def _seller_stom_reminder_job_name(offer_id: int) -> str:
    return f"deal_stom_rem_{int(offer_id)}"


def _gate_awaiting_seller_toman_close(gate: dict | None) -> bool:
    if not gate:
        return False
    if (gate.get("gate_status") or "").strip().lower() != "completed":
        return False
    oid = int(gate.get("offer_id") or 0)
    if oid <= 0:
        return False
    if int(gate.get("seller_toman_settled_at") or 0) > 0:
        return False
    if int(gate.get("seller_toman_close_enabled_at") or 0) <= 0:
        return False
    return bool(deal_gate_seller_toman_admin_list(oid))


def cancel_seller_stom_close_reminder(application, offer_id: int) -> None:
    jq = getattr(application, "job_queue", None)
    if not jq:
        return
    name = _seller_stom_reminder_job_name(offer_id)
    for job in jq.get_jobs_by_name(name):
        job.schedule_removal()


def _last_seller_stom_reminder_at(offer_id: int, seller_id: int) -> int:
    from database.db import bot_outbound_log_list
    from utils.deal_milestones import MILESTONE_SELLER_STOM_REMINDER

    sid = int(seller_id)
    tag = MILESTONE_SELLER_STOM_REMINDER
    last = 0
    for row in bot_outbound_log_list(int(offer_id)):
        if int(row.get("recipient_telegram_id") or 0) != sid:
            continue
        if (row.get("tag") or "").strip() != tag:
            continue
        last = max(last, int(row.get("created_at") or 0))
    return last


def _seller_stom_reminder_due(gate: dict, *, now: int | None = None) -> bool:
    """Only one seller reminder, eight hours after receipt delivery."""
    if not _gate_awaiting_seller_toman_close(gate):
        return False
    oid = int(gate.get("offer_id") or 0)
    seller_id = int(gate.get("seller_telegram_id") or 0)
    enabled_at = int(gate.get("seller_toman_close_enabled_at") or 0)
    if enabled_at <= 0:
        return False
    ts = int(now if now is not None else time.time())
    last_rem = _last_seller_stom_reminder_at(oid, seller_id)
    if last_rem > 0:
        return False
    return ts - enabled_at >= _SELLER_STOM_REMINDER_SEC


def schedule_seller_stom_close_reminder(application, offer_id: int) -> None:
    """Schedule the single, quiet seller reminder."""
    jq = getattr(application, "job_queue", None)
    if not jq:
        return
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not _gate_awaiting_seller_toman_close(gate):
        cancel_seller_stom_close_reminder(application, oid)
        return
    cancel_seller_stom_close_reminder(application, oid)
    jq.run_once(
        _job_seller_stom_close_reminder,
        when=_SELLER_STOM_REMINDER_SEC,
        data={"offer_id": oid},
        name=_seller_stom_reminder_job_name(oid),
    )


async def _send_seller_stom_close_reminder(
    bot,
    *,
    offer_id: int,
    gate: dict | None = None,
) -> bool:
    from utils.deal_milestones import MILESTONE_SELLER_STOM_REMINDER
    from utils.deal_outbound import deal_bot_send_message

    oid = int(offer_id)
    gate = gate or deal_gate_get(oid)
    if not _seller_stom_reminder_due(gate or {}):
        return False
    seller_id = int((gate or {}).get("seller_telegram_id") or 0)
    if seller_id <= 0:
        return False
    row = get_advert_offer_joined(oid)
    seq = int((row or {}).get("seq_in_advert") or oid)
    body = (
        f"{_RTL}⏰ <b>یادآوری</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b>\n\n"
        f"{_RTL}لطفاً پس از دریافت تومان، دکمهٔ "
        f"<b>تومان نشست — پایان معامله</b> را بزنید."
    )
    try:
        await deal_bot_send_message(
            bot,
            offer_id=oid,
            chat_id=seller_id,
            party="seller",
            tag=MILESTONE_SELLER_STOM_REMINDER,
            text=body,
            disable_web_page_preview=True,
            reply_markup=seller_toman_settled_keyboard(oid),
        )
        return True
    except Exception as e:
        logger.warning(
            "deal_stom_reminder: send failed seller=%s offer=%s: %s",
            seller_id,
            oid,
            e,
        )
        return False


async def _job_seller_stom_close_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    oid = int(data.get("offer_id") or 0)
    if oid <= 0:
        return
    await _send_seller_stom_close_reminder(context.bot, offer_id=oid)


async def resend_seller_stom_close_prompt(bot, offer_id: int) -> bool:
    """ارسال مجدد پیام + دکمهٔ پایان معامله برای فروشنده."""
    from utils.deal_outbound import deal_bot_send_message

    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not _gate_awaiting_seller_toman_close(gate):
        return False
    seller_id = int((gate or {}).get("seller_telegram_id") or 0)
    if seller_id <= 0:
        return False
    row = get_advert_offer_joined(oid)
    seq = int((row or {}).get("seq_in_advert") or oid)
    body = (
        f"{_RTL}💳 <b>تأیید پایان معامله</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b>\n\n"
        f"{_RTL}پس از دریافت تومان، دکمهٔ زیر را بزنید."
    )
    try:
        await deal_bot_send_message(
            bot,
            offer_id=oid,
            chat_id=seller_id,
            party="seller",
            tag="درخواست مجدد: پایان معامله",
            text=body,
            disable_web_page_preview=True,
            reply_markup=seller_toman_settled_keyboard(oid),
        )
        return True
    except Exception as e:
        logger.warning(
            "resend_seller_stom_close_prompt: seller=%s offer=%s: %s",
            seller_id,
            oid,
            e,
        )
        return False


async def run_seller_stom_reminder_sweep(bot) -> int:
    """بررسی دوره‌ای همهٔ معاملات منتظر تأیید فروشنده (پوشش وب + ری‌استارت)."""
    from database.db import deal_gate_list_awaiting_seller_toman_confirm

    sent = 0
    for gate in deal_gate_list_awaiting_seller_toman_confirm():
        oid = int(gate.get("offer_id") or 0)
        if await _send_seller_stom_close_reminder(bot, offer_id=oid, gate=gate):
            sent += 1
    return sent


def _gate_awaiting_admin_toman_receipt(gate: dict | None) -> bool:
    """Whether admin still needs to drive the deal through Toman receipt delivery."""
    if not gate:
        return False
    status = (gate.get("gate_status") or "").strip().lower()
    if status not in {"pending", "accounts", "completed"}:
        return False
    if int(gate.get("offer_id") or 0) <= 0:
        return False
    if int(gate.get("seller_toman_settled_at") or 0) > 0:
        return False
    return int(gate.get("seller_toman_close_enabled_at") or 0) <= 0


def _last_admin_toman_reminder_at(offer_id: int, admin_id: int) -> int:
    return _last_admin_toman_reminder_delivery(offer_id, admin_id)[0]


def _last_admin_toman_reminder_delivery(
    offer_id: int,
    admin_id: int,
) -> tuple[int, int]:
    """Return the latest reminder timestamp and Telegram message ID."""
    aid = int(admin_id)
    last = 0
    message_id = 0
    for row in bot_outbound_log_list(int(offer_id)):
        if int(row.get("recipient_telegram_id") or 0) != aid:
            continue
        if (row.get("party") or "").strip().lower() != "admin":
            continue
        if (row.get("tag") or "").strip() != _ADMIN_TOMAN_REMINDER_TAG:
            continue
        created_at = int(row.get("created_at") or 0)
        if created_at >= last:
            last = created_at
            message_id = int(row.get("telegram_message_id") or 0)
    return last, message_id


def _admin_toman_reminder_due(
    gate: dict,
    admin_id: int,
    *,
    now: int | None = None,
) -> bool:
    """At most one reminder per deal/admin/hour, persisted across restarts."""
    if not _gate_awaiting_admin_toman_receipt(gate):
        return False
    ts = int(now if now is not None else time.time())
    last = _last_admin_toman_reminder_at(int(gate["offer_id"]), int(admin_id))
    started_at = int(gate.get("started_at") or 0)
    anchor = last if last > 0 else started_at
    return anchor <= 0 or ts - anchor >= _ADMIN_TOMAN_REMINDER_SEC


def _admin_toman_reminder_stage(gate: dict) -> str:
    return {
        "pending": "تأیید نهایی طرفین",
        "accounts": "دریافت اطلاعات حساب",
        "completed": "پرداخت و تسویه",
    }.get((gate.get("gate_status") or "").strip().lower(), "در حال پیگیری")


def _admin_toman_reminder_keyboard(
    offer_id: int,
    gate: dict,
) -> InlineKeyboardMarkup:
    """Reminder actions: continue the workflow, never confirm receipt for seller."""
    from handlers.offers import _seller_euro_fully_confirmed_gate

    oid = int(offer_id)
    rows: list[list[InlineKeyboardButton]] = []
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
                "📋 مشاهده معامله",
                callback_data=f"adm|dgs|resync|{oid}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


async def run_admin_toman_receipt_reminder_sweep(
    bot,
    *,
    now: int | None = None,
) -> int:
    """Send hourly Persian reminders until the Toman receipt reaches seller."""
    from handlers.offers import _deal_admin_recipient_ids
    from utils.deal_outbound import deal_bot_log_text

    sent = 0
    admin_ids = _deal_admin_recipient_ids()
    if not admin_ids:
        return 0
    for gate in deal_gate_list_awaiting_admin_toman_receipt():
        oid = int(gate.get("offer_id") or 0)
        row = get_advert_offer_joined(oid) or {}
        advert_id = int(gate.get("advert_rowid") or row.get("advert_rowid") or 0)
        offer_seq = int(row.get("seq_in_advert") or oid)
        stage = _admin_toman_reminder_stage(gate)
        body = (
            f"{_RTL}⏰ <b>یادآوری ساعتی ادمین</b>\n\n"
            f"{_RTL}آگهی <b>{advert_id}</b> · پیشنهاد <b>{offer_seq}</b>\n"
            f"{_RTL}کد معامله <code>{oid}</code>\n"
            f"{_RTL}مرحله فعلی: <b>{stage}</b>\n\n"
            f"{_RTL}این معامله هنوز به مرحلهٔ <b>ارسال فیش واریز تومان به فروشنده</b> "
            "نرسیده است.\n"
            f"{_RTL}لطفاً وضعیت معامله را بررسی و مرحله‌های باقی‌مانده را پیگیری کنید.\n"
            f"{_RTL}<i>این یادآوری پس از ارسال موفق فیش تومان به فروشنده متوقف می‌شود.</i>"
        )
        keyboard = _admin_toman_reminder_keyboard(oid, gate)
        for admin_id in admin_ids:
            if not _admin_toman_reminder_due(gate, admin_id, now=now):
                continue
            _last_sent_at, previous_message_id = _last_admin_toman_reminder_delivery(
                oid, int(admin_id)
            )
            try:
                sent_message = await bot.send_message(
                    chat_id=int(admin_id),
                    text=body,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                new_message_id = int(getattr(sent_message, "message_id", 0) or 0)
                deal_bot_log_text(
                    oid,
                    int(admin_id),
                    "admin",
                    _ADMIN_TOMAN_REMINDER_TAG,
                    body,
                    telegram_message_id=new_message_id,
                )
                if previous_message_id > 0 and previous_message_id != new_message_id:
                    try:
                        await bot.delete_message(
                            chat_id=int(admin_id),
                            message_id=previous_message_id,
                        )
                    except Exception as delete_exc:
                        logger.warning(
                            "admin_toman_reminder: old message delete failed "
                            "admin=%s offer=%s message=%s: %s",
                            admin_id,
                            oid,
                            previous_message_id,
                            delete_exc,
                        )
                sent += 1
            except Exception as exc:
                logger.warning(
                    "admin_toman_reminder: send failed admin=%s offer=%s: %s",
                    admin_id,
                    oid,
                    exc,
                )
    return sent


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


def _admin_gate_rejected_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    """پس از رد تأیید نهایی — فعال‌سازی مجدد آگهی یا بستن معامله."""
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 فعال‌سازی مجدد آگهی",
                    callback_data=f"adm|dg|react|{oid}",
                ),
            ],
            [
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

_DEAL_PROBLEM_FA = {
    "invalid_gate_status": "وضعیت نامعتبر",
    "stage_without_both_confirmations": "مرحله بدون تأیید دو طرف",
    "payment_stage_without_both_accounts": "حساب یکی از طرفین ناقص",
    "invalid_seller_receipt_log": "لاگ فیش یورو خراب",
    "invalid_seller_toman_log": "لاگ فیش تومان خراب",
    "seller_close_enabled_without_delivered_receipt": "دکمه پایان بدون فیش تحویل‌شده",
    "seller_receipt_missing_delivery_confirmation": "تحویل فیش تأیید نشده",
    "seller_settled_but_gate_not_closed": "تسویه شده ولی باز است",
    "seller_receipt_before_euro_account": "فیش پیش از ارسال حساب یورو",
    "closed_gate_offer_status_mismatch": "وضعیت پیشنهاد با معامله فرق دارد",
    "closed_gate_advert_status_mismatch": "وضعیت آگهی با معامله فرق دارد",
    "critical_delivery_pending": "ارسال تلگرام ناموفق یا متوقف",
    "stuck_pending": "بیش از ۲ ساعت منتظر تأیید",
    "stuck_accounts": "بیش از ۱۲ ساعت منتظر حساب",
    "stuck_completed": "بیش از ۱۲ ساعت در پرداخت",
}


def _admin_reactivate_close_row(offer_id: int) -> list[InlineKeyboardButton]:
    oid = int(offer_id)
    return [
        InlineKeyboardButton(
            "🔄 فعال‌سازی مجدد آگهی",
            callback_data=f"adm|dg|react|{oid}",
        ),
        InlineKeyboardButton(
            "⛔ بستن معامله", callback_data=f"adm|dg|close|{oid}"
        ),
    ]


def _deal_gate_admin_terminal_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    """رد/بسته یا هر وضعیت پایانی — فعال‌سازی مجدد آگهی."""
    oid = int(offer_id)
    return InlineKeyboardMarkup(
        [
            _admin_reactivate_close_row(oid),
            [
                InlineKeyboardButton(
                    "🛠 بررسی و تعمیر وضعیت",
                    callback_data=f"adm|dgs|repair|{oid}",
                )
            ],
            [
                InlineKeyboardButton("🕒 تاریخچه", callback_data=f"adm|dgs|timeline|{oid}"),
                InlineKeyboardButton("📄 خروجی", callback_data=f"adm|dgs|export|{oid}"),
            ],
            [InlineKeyboardButton("🔙 لیست معاملات", callback_data="adm|dgs")],
            [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="adm|panel")],
        ]
    )


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
        [
            InlineKeyboardButton(
                "⚠️ معاملات نیازمند بررسی", callback_data="adm|dgs|problems"
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton("🩺 سلامت عملیات", callback_data="adm|dgs|health")]
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
        [
            InlineKeyboardButton("🕒 تاریخچه", callback_data=f"adm|dgs|timeline|{oid}"),
            InlineKeyboardButton("📄 خروجی", callback_data=f"adm|dgs|export|{oid}"),
        ]
    )
    rows.append(
        [InlineKeyboardButton("🔙 لیست معاملات", callback_data="adm|dgs")]
    )
    rows.append(
        [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="adm|panel")]
    )
    return InlineKeyboardMarkup(rows)


def _deal_gate_admin_completed_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_id)
    gate = deal_gate_get(oid) or {}
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
    rows.append(
        [
            InlineKeyboardButton("🕒 تاریخچه", callback_data=f"adm|dgs|timeline|{oid}"),
            InlineKeyboardButton("📄 خروجی", callback_data=f"adm|dgs|export|{oid}"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                "🛠 بررسی و تعمیر وضعیت",
                callback_data=f"adm|dgs|repair|{oid}",
            )
        ]
    )
    rows.append(_admin_reactivate_close_row(oid))
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


def _problem_age_fa(seconds: int) -> str:
    hours = max(0, int(seconds)) // 3600
    if hours < 1:
        return "کمتر از یک ساعت"
    if hours < 24:
        return f"{hours} ساعت"
    return f"{hours // 24} روز"


def build_admin_problem_deals_html(gates: list[dict]) -> str:
    if not gates:
        return (
            f"{_RTL}✅ <b>معامله نیازمند بررسی پیدا نشد.</b>\n\n"
            f"{_RTL}این صفحه فقط هنگام باز کردن پنل بررسی می‌شود و برای کاربران پیامی نمی‌فرستد."
        )
    parts = [
        f"{_RTL}⚠️ <b>معاملات نیازمند بررسی</b> ({len(gates)})\n",
        f"{_RTL}این فهرست غیرفعال است؛ هیچ اعلان خودکاری برای کاربران ارسال نمی‌شود.\n",
    ]
    for gate in gates[:20]:
        oid = int(gate["offer_id"])
        aid = int(gate["advert_rowid"])
        issues = gate.get("problem_issues") or []
        labels = [_DEAL_PROBLEM_FA.get(str(issue), str(issue)) for issue in issues[:3]]
        parts.append(
            f"\n{_RTL}• آگهی <b>{aid}</b> · offer <code>{oid}</code>"
            f" · {_problem_age_fa(int(gate.get('problem_age_seconds') or 0))}\n"
            f"{_RTL}<code>{html_module.escape('، '.join(labels))}</code>"
        )
    return "".join(parts)


def _deal_problem_list_keyboard(gates: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for gate in gates[:20]:
        oid = int(gate["offer_id"])
        aid = int(gate["advert_rowid"])
        count = len(gate.get("problem_issues") or [])
        rows.append(
            [
                InlineKeyboardButton(
                    f"⚠️ آگهی {aid} · offer {oid} ({count})"[:60],
                    callback_data=f"adm|dgs|{oid}",
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton("🔄 بررسی دوباره", callback_data="adm|dgs|problems")],
            [InlineKeyboardButton("📊 همه معاملات", callback_data="adm|dgs")],
            [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="adm|panel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


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
    for label, key, fid_key in (
        ("خریدار", "buyer_accounts_text", "buyer_accounts_photo_file_id"),
        ("فروشنده", "seller_accounts_text", "seller_accounts_photo_file_id"),
    ):
        raw = (gate.get(key) or "").strip()
        if raw:
            if _account_text_is_photo_marker(raw):
                has_img = bool((gate.get(fid_key) or "").strip())
                acct_parts.append(
                    f"\n{_RTL}🏦 <b>حساب {label}</b>\n"
                    f"{_RTL}📷 عکس ثبت شده"
                    + (
                        " — در آلبوم زیر پیام اصلی معامله"
                        if has_img
                        else " (فایل در دیتابیس نیست)"
                    )
                )
            else:
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


# =============================================================================
# Section 11 | بخش ۱۱ — Admin panel deal list and decisions
# EN: List/search gates; edit accounts; adm|dg| admin decisions on disputes.
# FA: لیست معاملات در پنل؛ ویرایش حساب؛ تصمیم ادمین روی اختلاف.
# =============================================================================


async def admin_show_deal_gate_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    context.user_data["admin_deal_gate_browse"] = True
    gates = deal_gate_list_for_admin()
    text = build_admin_deal_list_html(gates)
    kb = _deal_gate_admin_list_keyboard(gates)
    await _admin_edit_or_send(update, context, text, kb)


async def admin_show_problem_deals(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    from database.db import deal_gate_list_problems

    context.user_data["admin_deal_gate_browse"] = True
    gates = deal_gate_list_problems()
    await _admin_edit_or_send(
        update,
        context,
        build_admin_problem_deals_html(gates),
        _deal_problem_list_keyboard(gates),
    )


def build_deal_timeline_text(offer_id: int) -> str:
    """Plain-text, admin-only chronological record suitable for display/export."""
    oid = int(offer_id)
    events: list[tuple[int, str]] = []
    gate = deal_gate_get(oid) or {}
    if gate:
        events.append(
            (
                int(gate.get("started_at") or 0),
                f"STATE gate={gate.get('gate_status') or 'unknown'} "
                f"admin_decision={gate.get('admin_decision') or '-'}",
            )
        )
        for label, key in (
            ("buyer_toman", "buyer_receipt_log"),
            ("seller_euro", "seller_receipt_log"),
            ("seller_toman", "seller_toman_admin_log"),
        ):
            try:
                receipts = json.loads(gate.get(key) or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                receipts = []
            for index, receipt in enumerate(receipts if isinstance(receipts, list) else []):
                events.append(
                    (
                        int(receipt.get("at") or receipt.get("delivered_at") or 0),
                        f"RECEIPT [{label}] index={index} type={receipt.get('type') or 'text'} "
                        f"file={'yes' if receipt.get('file_id') else 'no'} "
                        f"confirmed_at={receipt.get('buyer_confirmed_at') or receipt.get('delivered_at') or 0}",
                    )
                )
    for item in negotiation_transcript_list(oid):
        ts = int(item.get("created_at") or 0)
        events.append(
            (ts, f"EVENT [{item.get('from') or 'system'}] {item.get('text') or ''}")
        )
    for item in bot_outbound_log_list(oid):
        ts = int(item.get("created_at") or 0)
        events.append(
            (
                ts,
                f"OUTBOUND [{item.get('party')}] {item.get('tag')} "
                f"chat={item.get('recipient_telegram_id')} mid={item.get('telegram_message_id') or 0}",
            )
        )
    for item in deal_delivery_list_for_offer(oid):
        ts = int(item.get("updated_at") or item.get("created_at") or 0)
        error = (item.get("last_error") or "").strip()
        events.append(
            (
                ts,
                f"DELIVERY [{item.get('status')}] {item.get('tag')} "
                f"attempts={item.get('attempts') or 0}"
                + (f" error={error}" if error else ""),
            )
        )
    events.sort(key=lambda row: row[0])
    lines = [f"Deal timeline — offer {oid}"]
    for ts, body in events:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "unknown"
        lines.append(f"{stamp} | {body}")
    if len(lines) == 1:
        lines.append("No recorded events.")
    return "\n".join(lines)


async def admin_show_deal_timeline(
    update: Update, context: ContextTypes.DEFAULT_TYPE, offer_id: int
) -> None:
    text = build_deal_timeline_text(offer_id)
    escaped = html_module.escape(text[-3600:])
    await _admin_edit_or_send(
        update,
        context,
        f"{_RTL}🕒 <b>تاریخچه معامله</b> · offer <code>{int(offer_id)}</code>\n\n"
        f"<pre>{escaped}</pre>",
        InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📄 دریافت فایل کامل",
                        callback_data=f"adm|dgs|export|{int(offer_id)}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 جزئیات معامله", callback_data=f"adm|dgs|{int(offer_id)}"
                    )
                ],
            ]
        ),
    )


async def admin_export_deal_timeline(
    update: Update, context: ContextTypes.DEFAULT_TYPE, offer_id: int
) -> None:
    q = update.callback_query
    if not q or not q.message:
        return
    data = build_deal_timeline_text(offer_id).encode("utf-8")
    await context.bot.send_document(
        chat_id=int(q.message.chat_id),
        document=InputFile(BytesIO(data), filename=f"deal-{int(offer_id)}-timeline.txt"),
        caption=f"تاریخچه کامل معامله {int(offer_id)}",
    )
    await q.answer("فایل تاریخچه ارسال شد.")


async def admin_show_deal_health(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    health = deal_operational_health()
    now = int(time.time())

    def age(value: int) -> str:
        if not value:
            return "—"
        hours = max(0, now - int(value)) // 3600
        return f"{hours} ساعت پیش"

    text = (
        f"{_RTL}🩺 <b>سلامت عملیات معاملات</b>\n\n"
        f"{_RTL}دیتابیس: <code>{html_module.escape(str(health['database_integrity']))}</code>\n"
        f"{_RTL}ارسال‌های باز: <b>{health['queue_open']}</b> · ناموفق: <b>{health['queue_failed']}</b>\n"
        f"{_RTL}معاملات نیازمند بررسی: <b>{health['problem_deals']}</b>\n"
        f"{_RTL}آخرین بکاپ سالم: <b>{age(health['last_backup_at'])}</b>\n"
        f"{_RTL}آخرین بکاپ خارج سرور: <b>{age(health['last_offsite_backup_at'])}</b>\n"
        f"{_RTL}آخرین تست بازیابی: <b>{age(health['last_restore_drill_at'])}</b>\n"
        f"{_RTL}آخرین تطبیق مالی: <b>{age(health['last_reconciliation_at'])}</b>"
        f" · مغایرت: <b>{health['last_reconciliation_issues']}</b>\n"
        f"{_RTL}آخرین پاک‌سازی حریم خصوصی: <b>{age(health['last_privacy_run_at'])}</b>\n"
        f"{_RTL}زمان اجرای ربات: <b>{age(health['started_at'])}</b>\n"
    )
    if health["last_backup_error"]:
        text += f"\n{_RTL}⚠️ خطای بکاپ: <code>{html_module.escape(health['last_backup_error'][:300])}</code>"
    if health["last_offsite_backup_error"]:
        text += f"\n{_RTL}⚠️ خطای بکاپ خارج سرور: <code>{html_module.escape(health['last_offsite_backup_error'][:300])}</code>"
    if health["last_restore_drill_error"]:
        text += f"\n{_RTL}⚠️ خطای تست بازیابی: <code>{html_module.escape(health['last_restore_drill_error'][:300])}</code>"
    if health["last_reconciliation_error"]:
        text += f"\n{_RTL}⚠️ خطای تطبیق مالی: <code>{html_module.escape(health['last_reconciliation_error'][:300])}</code>"
    await _admin_edit_or_send(
        update,
        context,
        text,
        InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="adm|dgs|health")],
                [InlineKeyboardButton("⚠️ نیازمند بررسی", callback_data="adm|dgs|problems")],
                [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="adm|panel")],
            ]
        ),
    )


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
        kb = _deal_gate_admin_terminal_keyboard(offer_id)
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
    if st not in ("accounts", "completed"):
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
        gate_status=st,
        **upsert_fields,
    )
    _log(
        oid,
        f"ادمین — حساب {party_fa}: {raw[:500]}",
        from_role="admin",
    )
    gate = deal_gate_get(oid) or gate
    both_done = bool(
        (gate.get("buyer_accounts_text") or "").strip()
        and (gate.get("seller_accounts_text") or "").strip()
    )
    if both_done and st != "completed":
        await _complete_deal(context, oid)
    elif both_done:
        await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    else:
        await sync_deal_admin_notification(context.bot, oid, deal_complete=False)
    gate = deal_gate_get(oid) or gate
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
    if not both_done:
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


# =============================================================================
# Section 9 | بخش ۹ — Gate start, reminders, yes/no
# EN: start_deal_final_gate; JobQueue reminders; escalate to admin after 2h.
# FA: شروع دروازه؛ یادآوری؛ تأیید/رد نهایی؛ ارجاع به ادمین پس از ۲ ساعت.
# =============================================================================


async def _refresh_deal_channel_status(
    context: ContextTypes.DEFAULT_TYPE,
    advert_rowid: int,
    *,
    offer_id: int,
    gate_status: str,
) -> None:
    """Best-effort public channel refresh after a deal-stage transition."""
    from handlers.offers import refresh_advert_channel_post

    try:
        await refresh_advert_channel_post(context.bot, int(advert_rowid))
    except Exception:
        logger.exception(
            "deal_gate: channel refresh failed offer=%s advert=%s stage=%s",
            offer_id,
            advert_rowid,
            gate_status,
        )


async def _set_deal_gate_stage(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    offer_id: int,
    advert_rowid: int,
    buyer_telegram_id: int,
    seller_telegram_id: int,
    gate_status: str,
    **fields,
) -> None:
    """Persist one gate stage and immediately synchronize the public advert."""
    deal_gate_upsert(
        offer_id=int(offer_id),
        advert_rowid=int(advert_rowid),
        buyer_telegram_id=int(buyer_telegram_id),
        seller_telegram_id=int(seller_telegram_id),
        gate_status=gate_status,
        **fields,
    )
    await _refresh_deal_channel_status(
        context,
        int(advert_rowid),
        offer_id=int(offer_id),
        gate_status=gate_status,
    )


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
    await _set_deal_gate_stage(
        context,
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
    try:
        await sync_deal_admin_notification(context.bot, oid)
        _log(oid, "اعلان معامله برای ادمین بلافاصله پس از پذیرش پیشنهاد")
    except Exception:
        # Party confirmation must continue even if one admin notification fails.
        logger.exception(
            "deal_gate: initial admin notification failed offer=%s", oid
        )


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
    if not gate or (gate.get("gate_status") or "").strip().lower() != "pending":
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


async def _dismiss_admin_escalation(
    bot,
    offer_id: int,
    *,
    resolved_text: str,
) -> None:
    """پیام ارجاع ۲ ساعته را پس از تأیید/رد نهایی به‌روز می‌کند."""
    gate = deal_gate_get(offer_id)
    if not gate:
        return
    stored = _parse_admin_escalation_mids(gate)
    if not stored:
        return
    plain = re.sub(r"<[^>]+>", "", resolved_text or "")
    for chat_id, mid in stored.items():
        cid = int(chat_id)
        m_id = int(mid)
        if m_id < 1:
            continue
        try:
            await bot.edit_message_text(
                chat_id=cid,
                message_id=m_id,
                text=resolved_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=None,
            )
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=cid, message_id=m_id, reply_markup=None
                    )
                except Exception:
                    pass
                continue
            try:
                await bot.edit_message_text(
                    chat_id=cid,
                    message_id=m_id,
                    text=plain,
                    disable_web_page_preview=True,
                    reply_markup=None,
                )
            except Exception:
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=cid, message_id=m_id, reply_markup=None
                    )
                except Exception:
                    pass
        except TelegramError:
            pass
    deal_gate_upsert(
        offer_id=int(offer_id),
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=int(gate["buyer_telegram_id"]),
        seller_telegram_id=int(gate["seller_telegram_id"]),
        admin_escalation_mids="{}",
    )


async def _notify_admin_escalation(
    context: ContextTypes.DEFAULT_TYPE, offer_id: int
) -> None:
    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "").strip().lower() != "pending":
        return
    if int(gate.get("admin_escalated_at") or 0) > 0:
        return
    br = (gate.get("buyer_response") or "").strip().lower()
    sr = (gate.get("seller_response") or "").strip().lower()
    if br == "yes" and sr == "yes":
        return
    row = get_advert_offer_joined(offer_id)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    if not row or not advert:
        return
    from handlers.offers import (
        _deal_admin_recipient_ids,
        _format_deal_party_identity_html,
        _offer_buyer_seller_telegram_ids,
    )

    buyer_id, seller_id = _offer_buyer_seller_telegram_ids(advert, row)
    oid = int(offer_id)
    aid = int(row["advert_rowid"])
    seq = int(row.get("seq_in_advert") or oid)

    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "").strip().lower() != "pending":
        return
    br = (gate.get("buyer_response") or "").strip().lower()
    sr = (gate.get("seller_response") or "").strip().lower()
    if br == "yes" and sr == "yes":
        return

    snap = _status_snapshot(gate)
    now = int(time.time())
    body = (
        f"{_RTL}⚠️ <b>تأیید نهایی معامله — نیاز به تصمیم ادمین</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b> · آگهی <b>{aid}</b>\n"
        f"{_RTL}بیش از ۲ ساعت از شروع تأیید نهایی گذشته.\n\n"
        f"<pre>{html_module.escape(snap)}</pre>\n\n"
        f"{_format_deal_party_identity_html(buyer_id, title='خریدار یورو')}\n"
        f"{_format_deal_party_identity_html(seller_id, title='فروشنده یورو')}\n"
        f"{_RTL}یکی از گزینه‌ها را انتخاب کنید:"
    )
    plain = re.sub(r"<[^>]+>", "", body or "")
    kb = _admin_gate_keyboard(oid)
    recipients = _deal_admin_recipient_ids()
    if not recipients:
        logger.warning("deal_gate_escalate: no recipients offer=%s", oid)
        return
    escalation_mids: dict[int, int] = {}
    for chat_id in recipients:
        cid = int(chat_id)
        try:
            sent = await context.bot.send_message(
                cid,
                body,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=kb,
            )
            escalation_mids[cid] = int(sent.message_id)
        except BadRequest:
            try:
                sent = await context.bot.send_message(
                    cid,
                    plain,
                    disable_web_page_preview=True,
                    reply_markup=kb,
                )
                escalation_mids[cid] = int(sent.message_id)
            except TelegramError as e2:
                logger.warning(
                    "deal_gate_escalate: send failed offer=%s chat=%s: %s",
                    oid,
                    cid,
                    e2,
                )
        except TelegramError as e:
            logger.warning(
                "deal_gate_escalate: send failed offer=%s chat=%s: %s",
                oid,
                cid,
                e,
            )
    if not escalation_mids:
        return
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=aid,
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        admin_escalated_at=now,
        admin_escalation_mids=_serialize_admin_escalation_mids(escalation_mids),
    )
    _log(oid, "اعلان به ادمین — گذشت ۲ ساعت از تأیید نهایی", from_role="admin")


# =============================================================================
# Section 10 | بخش ۱۰ — Party receipts and group-0 routers
# EN: deal|rcpt| buyer toman; deal|srcpt| seller euro; deal|eurset| confirm; stom pending.
# FA: فیش تومان خریدار؛ فیش یورو فروشنده؛ تأیید یورو نشست؛ router گروه ۰.
# Pending keys: _DEAL_RCPT_KEY, _DEAL_ADMIN_STOM_KEY — تا انصراف یا پایان معامله باز می‌مانند.
# =============================================================================


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
    allowed, reason = _party_callback_authorized(
        gate, uid, party="buyer", allowed_statuses={"completed"}
    )
    if reason == "role":
        await q.answer("فقط خریدار این معامله", show_alert=True)
        return
    if not allowed:
        await _expire_stale_deal_button(
            q, "این دکمه منقضی شده است؛ وضعیت جدید معامله را باز کنید."
        )
        return
    buyer_id = int(gate.get("buyer_telegram_id") or 0)
    card_ok = int(gate.get("buyer_toman_card_sent_at") or 0) > 0 or (
        _buyer_toman_card_delivered(offer_id, buyer_id) if buyer_id else False
    )
    if not card_ok:
        await q.answer("ابتدا ادمین کارت واریز را ارسال کند.", show_alert=True)
        return
    if action == "cancel":
        await q.answer("انصراف")
        _clear_deal_receipt_pending(context)
        await _purge_rcpt_prompt_msgs(context.bot, user_data_store, uid, offer_id)
        try:
            await context.bot.send_message(
                uid,
                f"{_RTL}✅ ارسال فیش متوقف شد. دوباره «ارسال فیش واریزی» را بزنید.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    if action != "go":
        await q.answer()
        return

    if await _party_receipt_prepare_switch(
        context, uid, int(offer_id), "buyer"
    ):
        await q.answer("همین‌جا فیش بعدی را بفرستید یا انصراف.", show_alert=True)
        return

    await q.answer()
    try:
        if q.message:
            await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    aid = int(gate["advert_rowid"])
    context.user_data[_DEAL_RCPT_KEY] = {
        "offer_id": int(offer_id),
        "advert_rowid": aid,
        "party": "buyer",
    }
    try:
        sent = await context.bot.send_message(
            uid,
            _party_deal_identity_html(
                gate,
                uid,
                stage_fa="ارسال فیش واریزی تومان",
            )
            + f"{_RTL}📎 <b>ارسال فیش واریزی</b>\n\n"
            f"{_RTL}آگهی <b>{aid}</b> · offer <code>{offer_id}</code>\n"
            f"{_RTL}عکس یا متن هر فیش را بفرستید.\n"
            f"{_RTL}یک واریز ممکن است چند فیش باشد — "
            f"همه را بفرستید؛ «انصراف» برای خروج.",
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
    allowed, reason = _party_callback_authorized(
        gate, uid, party="seller", allowed_statuses={"completed"}
    )
    if reason == "role":
        await q.answer("فقط فروشنده این معامله", show_alert=True)
        return
    if not allowed:
        await _expire_stale_deal_button(
            q, "این دکمه منقضی شده است؛ وضعیت جدید معامله را باز کنید."
        )
        return
    seller_id = int(gate.get("seller_telegram_id") or 0)
    eur_ok = int(gate.get("seller_eur_account_sent_at") or 0) > 0 or (
        _seller_buyer_eur_account_delivered(offer_id, seller_id) if seller_id else False
    )
    if not eur_ok:
        await q.answer("ابتدا حساب یورو به فروشنده ارسال شود.", show_alert=True)
        return
    if action == "cancel":
        await q.answer("انصراف")
        _clear_deal_receipt_pending(context)
        await _purge_rcpt_prompt_msgs(context.bot, user_data_store, uid, offer_id)
        try:
            await context.bot.send_message(
                uid,
                f"{_RTL}✅ ارسال فیش متوقف شد. دوباره «ارسال فیش واریزی یورو» را بزنید.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    if action != "go":
        await q.answer()
        return

    if await _party_receipt_prepare_switch(
        context, uid, int(offer_id), "seller"
    ):
        await q.answer("همین‌جا فیش بعدی را بفرستید یا انصراف.", show_alert=True)
        return

    await q.answer()
    try:
        if q.message:
            await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    aid = int(gate["advert_rowid"])
    context.user_data[_DEAL_RCPT_KEY] = {
        "offer_id": int(offer_id),
        "advert_rowid": aid,
        "party": "seller",
    }
    try:
        sent = await context.bot.send_message(
            uid,
            _party_deal_identity_html(
                gate,
                uid,
                stage_fa="ارسال فیش واریزی یورو",
            )
            + f"{_RTL}📎 <b>ارسال فیش واریزی یورو</b>\n\n"
            f"{_RTL}آگهی <b>{aid}</b> · offer <code>{offer_id}</code>\n"
            f"{_RTL}عکس یا متن هر فیش را بفرستید.\n"
            f"{_RTL}یک واریز ممکن است چند فیش باشد — "
            f"همه را بفرستید؛ «انصراف» برای خروج.",
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
    allowed, reason = _party_callback_authorized(
        gate, uid, party="buyer", allowed_statuses={"completed"}
    )
    if reason == "role":
        await q.answer("فقط خریدار این معامله", show_alert=True)
        return
    if not allowed:
        await _expire_stale_deal_button(
            q, "این تأیید منقضی شده است؛ وضعیت معامله تغییر کرده."
        )
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
    if not gate or not _deal_gate_allows_party_receipts(gate):
        _clear_deal_receipt_pending(context)
        if update.message and gate and not _deal_gate_allows_party_receipts(gate):
            await update.message.reply_text(
                f"{_RTL}این معامله بسته شده — ارسال فیش جدید ممکن نیست."
            )
        return False
    if party == "seller":
        if int(gate.get("seller_telegram_id") or 0) != uid:
            _clear_deal_receipt_pending(context)
            return False
    elif int(gate.get("buyer_telegram_id") or 0) != uid:
        _clear_deal_receipt_pending(context)
        return False
    pending_aid = int(pending.get("advert_rowid") or 0)
    gate_aid = int(gate.get("advert_rowid") or 0)
    if pending_aid and gate_aid and pending_aid != gate_aid:
        _clear_deal_receipt_pending(context)
        await update.message.reply_text(
            f"{_RTL}❌ فلو فیش با آگهی <b>{pending_aid}</b> شروع شده — "
            f"دوباره از دکمهٔ فیش همان آگهی شروع کنید.",
            parse_mode=ParseMode.HTML,
        )
        return True
    text = (update.message.text or "").strip()
    if not text or len(text) < 2:
        await update.message.reply_text(f"{_RTL}متن فیش را کامل‌تر بفرستید.")
        return True
    if party == "seller":
        items = deal_gate_append_seller_receipt(
            oid,
            entry_type="text",
            text=text,
            source_message_id=update.message.message_id,
        )
        gate = deal_gate_get(oid) or gate
        idx = len(items) - 1
        _log(oid, f"فیش یورو متنی فروشنده ({len(text)} کاراکتر)", from_role="seller")
        await _notify_buyer_euro_receipt_confirm(
            context.bot,
            offer_id=oid,
            gate=gate,
            receipt_index=idx,
            entry_type="text",
            text=text,
        )
        await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
        await _party_receipt_ack(
            update,
            party="seller",
            advert_rowid=int(gate.get("advert_rowid") or 0),
        )
        return True
    deal_gate_append_buyer_receipt(
        oid,
        entry_type="text",
        text=text,
        source_message_id=update.message.message_id,
    )
    gate = deal_gate_get(oid) or gate
    _log_receipt_consistency(oid, gate, text, receipt_kind="buyer_toman")
    _log(oid, f"فیش واریز متنی خریدار ({len(text)} کاراکتر)", from_role="buyer")
    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    await _party_receipt_ack(
        update,
        party="buyer",
        advert_rowid=int(gate.get("advert_rowid") or 0),
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
    if not gate or not _deal_gate_allows_party_receipts(gate):
        _clear_deal_receipt_pending(context)
        if update.message and gate and not _deal_gate_allows_party_receipts(gate):
            await update.message.reply_text(
                f"{_RTL}این معامله بسته شده — ارسال فیش جدید ممکن نیست."
            )
        return False
    if party == "seller":
        if int(gate.get("seller_telegram_id") or 0) != uid:
            _clear_deal_receipt_pending(context)
            return False
    elif int(gate.get("buyer_telegram_id") or 0) != uid:
        _clear_deal_receipt_pending(context)
        return False
    pending_aid = int(pending.get("advert_rowid") or 0)
    gate_aid = int(gate.get("advert_rowid") or 0)
    if pending_aid and gate_aid and pending_aid != gate_aid:
        _clear_deal_receipt_pending(context)
        await update.message.reply_text(
            f"{_RTL}❌ فلو فیش با آگهی <b>{pending_aid}</b> شروع شده — "
            f"دوباره از دکمهٔ فیش همان آگهی شروع کنید.",
            parse_mode=ParseMode.HTML,
        )
        return True
    extracted = _extract_receipt_file_id(update.message)
    if not extracted:
        return False
    fid, media_kind = extracted
    entry_type = "document" if media_kind == "document" else "photo"
    cap = (update.message.caption or "").strip()
    if party == "seller":
        items = deal_gate_append_seller_receipt(
            oid,
            entry_type=entry_type,
            text=cap,
            file_id=fid,
            source_message_id=update.message.message_id,
        )
        gate = deal_gate_get(oid) or gate
        _log_receipt_consistency(oid, gate, cap, receipt_kind="seller_euro")
        idx = len(items) - 1
        _log(oid, f"فیش یورو {entry_type} فروشنده", from_role="seller")
        await _notify_buyer_euro_receipt_confirm(
            context.bot,
            offer_id=oid,
            gate=gate,
            receipt_index=idx,
            entry_type=entry_type,
            text=cap,
            file_id=fid,
        )
        await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
        await _party_receipt_ack(
            update,
            party="seller",
            advert_rowid=int(gate.get("advert_rowid") or 0),
        )
        return True
    deal_gate_append_buyer_receipt(
        oid,
        entry_type=entry_type,
        text=cap,
        file_id=fid,
        source_message_id=update.message.message_id,
    )
    gate = deal_gate_get(oid) or gate
    _log_receipt_consistency(oid, gate, cap, receipt_kind="buyer_toman")
    _log(oid, f"فیش واریز {entry_type} خریدار", from_role="buyer")
    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    await _party_receipt_ack(
        update,
        party="buyer",
        advert_rowid=int(gate.get("advert_rowid") or 0),
    )
    return True


async def deal_gate_group0_text_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if await _deal_admin_stom_try_message(update, context):
        raise ApplicationHandlerStop
    if await _deal_admin_proxy_receipt_try_message(update, context):
        raise ApplicationHandlerStop
    if await _deal_receipt_try_message(update, context):
        raise ApplicationHandlerStop
    await deal_gate_accounts_router(update, context)


async def deal_gate_group0_photo_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if await _deal_admin_stom_try_photo(update, context):
        raise ApplicationHandlerStop
    if await _deal_admin_proxy_receipt_try_photo(update, context):
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
        await _expire_stale_deal_button(q, "دکمه نامعتبر یا منقضی است.")
        return
    oid_index = (
        3
        if (parts[0] == "adm" and parts[1] == "dg")
        or (parts[0] == "deal" and parts[1] == "acc")
        else 2
    )
    try:
        callback_offer_id = int(parts[oid_index])
        if callback_offer_id <= 0:
            raise ValueError
    except (IndexError, TypeError, ValueError):
        await _expire_stale_deal_button(q, "دکمه نامعتبر یا منقضی است.")
        return
    if parts[0] == "deal" and parts[1] == "rcpt" and len(parts) >= 4:
        await _handle_deal_receipt_callback(
            update, context, parts[3], callback_offer_id
        )
    elif parts[0] == "deal" and parts[1] == "srcpt" and len(parts) >= 4:
        await _handle_deal_seller_receipt_callback(
            update, context, parts[3], callback_offer_id
        )
    elif parts[0] == "deal" and parts[1] == "eurset" and len(parts) >= 4:
        try:
            ridx = int(parts[3])
        except (TypeError, ValueError):
            return
        await _handle_buyer_euro_settled_callback(
            update, context, callback_offer_id, ridx
        )
    elif parts[0] == "deal" and parts[1] == "stomcfm" and len(parts) >= 3:
        await _handle_seller_toman_settled_callback(
            update, context, callback_offer_id
        )
    elif parts[0] == "deal" and parts[1] == "accpick" and len(parts) >= 3:
        await _handle_account_deal_pick_callback(
            update, context, callback_offer_id
        )
    elif parts[0] == "deal" and parts[1] == "acc" and len(parts) >= 4:
        await _handle_account_confirm_callback(
            update, context, parts[2], callback_offer_id
        )
    elif parts[0] == "deal":
        await _handle_party_response(update, context, parts[1], callback_offer_id)
    elif parts[0] == "adm" and parts[1] == "dg" and len(parts) >= 4:
        await _handle_admin_decision(update, context, parts[2], callback_offer_id)


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
    if not gate or (gate.get("gate_status") or "").strip().lower() != "pending":
        await _expire_stale_deal_button(q, "این مرحله دیگر فعال نیست.")
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
    await _set_deal_gate_stage(
        context,
        offer_id=offer_id,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="accounts",
    )
    _log(offer_id, "هر دو طرف تأیید نهایی (بله) زدند — جمع‌آوری حساب")
    _cancel_gate_reminder_jobs(context, offer_id)
    row = row or get_advert_offer_joined(offer_id)
    if row:
        aid = int(row["advert_rowid"])
        seq = int(row.get("seq_in_advert") or offer_id)
        await _dismiss_admin_escalation(
            context.bot,
            offer_id,
            resolved_text=(
                f"{_RTL}✅ <b>تأیید نهایی انجام شد</b>\n\n"
                f"{_RTL}پیشنهاد <b>{seq}</b> · آگهی <b>{aid}</b>\n"
                f"{_RTL}هر دو طرف تأیید کردند — این اعلان دیگر فعال نیست."
            ),
        )
    try:
        from handlers.offers import clear_offer_flow_user_data
        from utils.telegram_utils import reset_flow_user_bucket

        for party_uid in (buyer_id, seller_id):
            if not party_uid:
                continue
            try:
                ud = context.application.user_data[party_uid]
                clear_offer_flow_user_data(ud)
                candidates = deal_gate_accounts_for_user(int(party_uid))
                if len(candidates) == 1 and not ud.get(
                    _DEAL_ACC_REQUIRE_PICK_KEY
                ):
                    ud[_DEAL_ACC_OFFER_KEY] = int(offer_id)
                else:
                    ud.pop(_DEAL_ACC_OFFER_KEY, None)
                    if len(candidates) > 1:
                        ud[_DEAL_ACC_REQUIRE_PICK_KEY] = True
            except Exception:
                logger.exception(
                    "deal_gate: clear offer flow failed uid=%s offer=%s",
                    party_uid,
                    offer_id,
                )
            reset_flow_user_bucket(user_data_store, int(party_uid))
    except Exception:
        logger.exception("deal_gate: clear offer flow failed offer=%s", offer_id)
    prompt_gate = deal_gate_get(offer_id) or gate
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
        body = _party_deal_identity_html(
            prompt_gate,
            uid,
            stage_fa="دریافت اطلاعات حساب",
        ) + body
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
                reply_markup=_account_deal_pick_keyboard(offer_id),
            )
            _track_deal_msg(user_data_store, uid, offer_id, sent.message_id)
        except Exception:
            pass
    await sync_deal_admin_notification(context.bot, offer_id)
    _log(offer_id, "اعلان معامله ادمین پس از تأیید دوطرفه به‌روزرسانی شد")


async def _on_gate_rejected(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    *,
    rejector_id: int,
    party: str,
    acted_by_admin: bool = False,
) -> None:
    gate = deal_gate_get(offer_id)
    if not gate:
        return
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    update_advert_offer_status(offer_id, "gate_rejected")
    await _set_deal_gate_stage(
        context,
        offer_id=offer_id,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="rejected",
    )
    _cancel_gate_jobs(context, offer_id)
    actor_text = f"ادمین از طرف {party}" if acted_by_admin else party
    row = get_advert_offer_joined(offer_id)
    if row:
        aid = int(row["advert_rowid"])
        seq = int(row.get("seq_in_advert") or offer_id)
        await _dismiss_admin_escalation(
            context.bot,
            offer_id,
            resolved_text=(
                f"{_RTL}❌ <b>تأیید نهایی لغو شد</b>\n\n"
                f"{_RTL}پیشنهاد <b>{seq}</b> · آگهی <b>{aid}</b>\n"
                f"{_RTL}{actor_text} یورو «خیر» را ثبت کرد — این اعلان دیگر فعال نیست."
            ),
        )
    _log(offer_id, f"معامله متوقف شد — {actor_text} «خیر» را ثبت کرد")
    msg = (
        f"{_RTL}❌ <b>تأیید نهایی لغو شد</b>\n\n"
        f"{_RTL}<b>{actor_text} یورو</b> «خیر» را ثبت کرد.\n"
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
    had_admin_message = bool(_parse_admin_notify_mids(gate))
    if row and advert and had_admin_message:
        await sync_deal_admin_notification(context.bot, offer_id)
    elif row and advert:
        from handlers.offers import (
            _format_deal_party_identity_html,
            _send_deal_admin_notifications,
        )

        aid = int(row["advert_rowid"])
        body = (
            f"{_RTL}❌ <b>رد تأیید نهایی</b> · آگهی <b>{aid}</b>\n\n"
            f"{_RTL}{actor_text} یورو «خیر» را ثبت کرد.\n\n"
            f"{_format_deal_party_identity_html(buyer_id, title='خریدار')}\n"
            f"{_format_deal_party_identity_html(seller_id, title='فروشنده')}"
        )
        await _send_deal_admin_notifications(
            context.bot,
            body,
            log_tag=f"deal_gate_no|{offer_id}",
            reply_markup=_admin_gate_rejected_keyboard(offer_id),
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
    if not await _require_full_deal_admin(q):
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

    if action in {"react", "reactok"}:
        if not await _admin_sensitive_confirmation(
            context,
            q,
            action="reactivate",
            offer_id=offer_id,
            confirm_data=f"adm|dg|reactok|{offer_id}",
            prompt="✅ تأیید نهایی فعال‌سازی مجدد",
            is_confirmation=action == "reactok",
        ):
            return
        await _reactivate_advert(context, offer_id, gate, row, q)
        return

    if action in {"close", "closeok"}:
        if not await _admin_sensitive_confirmation(
            context,
            q,
            action="close",
            offer_id=offer_id,
            confirm_data=f"adm|dg|closeok|{offer_id}",
            prompt="✅ تأیید نهایی بستن معامله",
            is_confirmation=action == "closeok",
        ):
            return
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


async def _replace_admin_deal_messages_with_status(
    bot,
    *,
    gate: dict,
    text: str,
) -> None:
    """Replace every stored admin deal card and remove its now-stale album."""
    stored = _parse_admin_notify_mids(gate)
    album_mids = _parse_admin_album_mids(gate)
    chat_ids = set(stored) | set(album_mids)
    for chat_id in chat_ids:
        text_mid = stored.get(int(chat_id))
        await _delete_all_admin_album_messages_for_chat(
            bot,
            chat_id=int(chat_id),
            gate=gate,
            text_mid=text_mid,
            extra_mids=album_mids.get(int(chat_id)),
        )
        if text_mid:
            try:
                await bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(text_mid),
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=None,
                )
                continue
            except Exception:
                logger.exception(
                    "deal_gate: admin terminal status edit failed chat=%s offer=%s",
                    chat_id,
                    gate.get("offer_id"),
                )
        try:
            await bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception(
                "deal_gate: admin terminal status send failed chat=%s offer=%s",
                chat_id,
                gate.get("offer_id"),
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
    seq = int(row.get("seq_in_advert") or offer_id)
    if not deal_gate_archive_and_reactivate(offer_id, aid):
        await q.answer("وضعیت معامله هم‌زمان تغییر کرد.", show_alert=True)
        return
    _log(offer_id, "ادمین: فعال‌سازی مجدد آگهی", from_role="admin")
    await _purge_gate_ui(context, gate, offer_id)
    _cancel_gate_jobs(context, offer_id)
    from handlers.offers import refresh_advert_channel_post

    await refresh_advert_channel_post(context.bot, aid)
    note = (
        f"{_RTL}🔄 <b>معامله لغو و آگهی دوباره فعال شد</b>\n\n"
        f"{_RTL}آگهی <b>{aid}</b> · پیشنهاد <b>{seq}</b>\n"
        f"{_RTL}کد معامله <code>{offer_id}</code>\n\n"
        f"{_RTL}تأیید نهایی قبلی لغو شد؛ می‌توانید پیشنهاد جدید دهید."
    )
    await _replace_admin_deal_messages_with_status(
        context.bot,
        gate=gate,
        text=note,
    )
    for uid, party in ((buyer_id, "buyer"), (seller_id, "seller")):
        if uid:
            await _enqueue_and_deliver_deal_message(
                context.bot,
                offer_id=offer_id,
                chat_id=uid,
                party=party,
                tag="وضعیت: فعال‌سازی مجدد آگهی",
                payload_type="text",
                payload={"body_html": note},
                dedupe_key=f"deal_reactivated:{offer_id}:{uid}",
            )
    await q.answer("آگهی فعال شد.")
    try:
        if q.message:
            await q.message.edit_text(
                note,
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        pass


async def _finalize_deal_close(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    gate: dict,
    row: dict,
    *,
    closed_by: str = "admin",
    answer_query=None,
    persist_close: bool = True,
) -> None:
    aid = int(row["advert_rowid"])
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    if persist_close and not deal_gate_close_atomic(offer_id, aid):
        if answer_query:
            await answer_query.answer("وضعیت معامله هم‌زمان تغییر کرد.", show_alert=True)
        return
    await _refresh_deal_channel_status(
        context,
        aid,
        offer_id=offer_id,
        gate_status="closed",
    )
    who = "فروشنده" if closed_by == "seller" else "ادمین"
    _log(offer_id, f"{who}: بستن معامله و آگهی", from_role=closed_by)
    cancel_seller_stom_close_reminder(context.application, int(offer_id))
    await _purge_gate_ui(context, gate, offer_id)
    _cancel_gate_jobs(context, offer_id)
    seq = int(row.get("seq_in_advert") or offer_id)
    close_note = (
        f"{_RTL}⛔ <b>معامله بسته شد</b>\n\n"
        f"{_RTL}آگهی <b>{aid}</b> · پیشنهاد <b>{seq}</b>\n"
        f"{_RTL}این معامله و آگهی توسط <b>{html_module.escape(who)}</b> بسته شد."
    )
    for uid, party in ((buyer_id, "buyer"), (seller_id, "seller")):
        if uid:
            await _enqueue_and_deliver_deal_message(
                context.bot,
                offer_id=offer_id,
                chat_id=uid,
                party=party,
                tag="وضعیت: بستن معامله",
                payload_type="text",
                payload={"body_html": close_note},
                dedupe_key=f"deal_closed:{offer_id}:{uid}",
            )
    if closed_by == "seller":
        from utils.deal_milestones import notify_admins_deal_closed_by_seller

        await notify_admins_deal_closed_by_seller(
            context.bot,
            offer_id=int(offer_id),
            gate=gate,
            aid=aid,
        )
    try:
        await sync_deal_admin_notification(
            context.bot, int(offer_id), deal_complete=True
        )
    except Exception:
        logger.exception("finalize_deal_close: admin sync offer=%s", offer_id)
    if answer_query:
        try:
            await answer_query.answer("معامله بسته شد.", show_alert=True)
        except Exception:
            pass
        try:
            if answer_query.message:
                await answer_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


async def _close_deal(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    gate: dict,
    row: dict,
    q,
) -> None:
    await _finalize_deal_close(
        context, offer_id, gate, row, closed_by="admin", answer_query=q
    )
    try:
        if q.message:
            await q.message.edit_text(
                f"{_RTL}⛔ معامله بسته شد.",
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        pass


async def _handle_seller_toman_settled_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
) -> None:
    """فروشنده: تأیید دریافت تومان و پایان معامله."""
    q = update.callback_query
    if not q or not q.from_user:
        return
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not gate:
        await q.answer("معامله پیدا نشد.", show_alert=True)
        return
    st = (gate.get("gate_status") or "").strip().lower()
    if st == "closed":
        await q.answer("معامله قبلاً بسته شده.", show_alert=True)
        return
    seller_id = int(gate.get("seller_telegram_id") or 0)
    if int(q.from_user.id) != seller_id:
        await q.answer("فقط فروشنده می‌تواند این را تأیید کند.", show_alert=True)
        return
    if not deal_gate_seller_toman_admin_list(oid):
        await q.answer("هنوز فیش تومان از ادمین ارسال نشده.", show_alert=True)
        return
    row = get_advert_offer_joined(oid)
    if not row:
        await q.answer("پیشنهاد پیدا نشد.", show_alert=True)
        return
    now = int(time.time())
    if not deal_gate_settle_and_close_atomic(
        oid,
        int(row["advert_rowid"]),
        settled_at=now,
        require_receipt=True,
    ):
        await q.answer(
            "این معامله هم‌زمان تأیید شد یا دیگر در مرحله پایان نیست.",
            show_alert=True,
        )
        return
    gate = deal_gate_get(oid) or gate
    await _finalize_deal_close(
        context,
        oid,
        gate,
        row,
        closed_by="seller",
        answer_query=q,
        persist_close=False,
    )


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


def _resolve_party_accounts_gate(
    context: ContextTypes.DEFAULT_TYPE, uid: int
) -> dict | None:
    """Resolve only an unambiguous or explicitly selected account-stage deal."""
    candidates = deal_gate_accounts_for_user(int(uid))
    if not candidates:
        context.user_data.pop(_DEAL_ACC_OFFER_KEY, None)
        context.user_data.pop(_DEAL_ACC_REQUIRE_PICK_KEY, None)
        return None

    prefer = context.user_data.get(_DEAL_ACC_OFFER_KEY)
    if prefer is not None:
        try:
            preferred_id = int(prefer)
        except (TypeError, ValueError):
            preferred_id = 0
        for gate in candidates:
            if int(gate.get("offer_id") or 0) == preferred_id:
                return gate
        context.user_data.pop(_DEAL_ACC_OFFER_KEY, None)

    if len(candidates) > 1:
        context.user_data[_DEAL_ACC_REQUIRE_PICK_KEY] = True
        return None
    if context.user_data.get(_DEAL_ACC_REQUIRE_PICK_KEY):
        return None
    return candidates[0]


async def _prompt_account_deal_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    gates: list[dict],
) -> bool:
    if not update.message or not update.effective_user or not gates:
        return False
    context.user_data[_DEAL_ACC_REQUIRE_PICK_KEY] = True
    context.user_data.pop(_DEAL_ACC_OFFER_KEY, None)
    await update.message.reply_text(
        f"{_RTL}⚠️ <b>چند معامله فعال دارید.</b>\n\n"
        f"{_RTL}برای جلوگیری از ثبت حساب در معامله اشتباه، ابتدا معامله موردنظر را انتخاب کنید؛ "
        f"سپس متن یا عکس حساب را بفرستید.",
        parse_mode=ParseMode.HTML,
        reply_markup=_account_deal_choices_keyboard(
            gates, int(update.effective_user.id)
        ),
    )
    return True


async def _handle_account_deal_pick_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    uid = int(q.from_user.id)
    candidates = deal_gate_accounts_for_user(uid)
    gate = next(
        (
            item
            for item in candidates
            if int(item.get("offer_id") or 0) == int(offer_id)
        ),
        None,
    )
    if not gate:
        await q.answer(
            "این معامله دیگر در مرحله دریافت حساب نیست.",
            show_alert=True,
        )
        return
    _clear_account_pending(context)
    context.user_data[_DEAL_ACC_OFFER_KEY] = int(offer_id)
    context.user_data[_DEAL_ACC_REQUIRE_PICK_KEY] = len(candidates) > 1
    await q.answer("این معامله انتخاب شد.")
    await context.bot.send_message(
        chat_id=uid,
        text=(
            _party_deal_identity_html(
                gate,
                uid,
                stage_fa="دریافت اطلاعات حساب",
            )
            + f"{_RTL}✅ این معامله انتخاب شد. اکنون <b>متن یا عکس حساب همین معامله</b> را ارسال کنید."
        ),
        parse_mode=ParseMode.HTML,
    )
    return None


def _clear_party_accounts_offer(
    context: ContextTypes.DEFAULT_TYPE,
    uid: int,
    *,
    offer_id: int | None = None,
) -> None:
    try:
        current = context.user_data.get(_DEAL_ACC_OFFER_KEY)
        if offer_id is None or int(current or 0) == int(offer_id):
            context.user_data.pop(_DEAL_ACC_OFFER_KEY, None)
    except Exception:
        pass
    try:
        ud = context.application.user_data[int(uid)]
        current = ud.get(_DEAL_ACC_OFFER_KEY)
        if offer_id is None or int(current or 0) == int(offer_id):
            ud.pop(_DEAL_ACC_OFFER_KEY, None)
    except Exception:
        pass


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
    extracted = _extract_receipt_file_id(message)
    if not extracted:
        return None
    fid, kind = extracted
    return fid if kind == "photo" else None


def _extract_receipt_file_id(message) -> tuple[str, str] | None:
    """(file_id, 'photo'|'document') — عکس، تصویر، یا PDF فیش."""
    if message.photo:
        return str(message.photo[-1].file_id), "photo"
    doc = message.document
    if not doc:
        return None
    mime = (doc.mime_type or "").lower()
    if mime.startswith("image/"):
        return str(doc.file_id), "photo"
    if mime == "application/pdf":
        return str(doc.file_id), "document"
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
    _clear_party_accounts_offer(context, int(uid), offer_id=oid)

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

    if both_done:
        await _complete_deal(context, oid)
    else:
        await sync_deal_admin_notification(context.bot, oid)
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
    from utils.flow_guards import user_offer_wizard_text_step

    if user_offer_wizard_text_step(context):
        return
    from handlers.iran_panel_sync import is_iran_tx_flow_active

    if is_iran_tx_flow_active(context) or context.user_data.get("admin_iran_txn_mode") in (
        "in",
        "out",
    ):
        return
    gate = _resolve_party_accounts_gate(context, uid)
    if not gate:
        candidates = deal_gate_accounts_for_user(int(uid))
        if candidates and await _prompt_account_deal_choice(
            update, context, candidates
        ):
            raise ApplicationHandlerStop
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
        await _expire_stale_deal_button(
            q, "این پیش‌نمایش منقضی شده — دوباره عکس بفرستید."
        )
        return

    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "").strip().lower() != "accounts":
        _clear_account_pending(context)
        await _expire_stale_deal_button(q, "این مرحله دیگر فعال نیست.")
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
        await _expire_stale_deal_button(q, "حساب شما قبلاً ثبت شده.")
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
    from utils.flow_guards import user_offer_wizard_text_step

    if user_offer_wizard_text_step(context):
        return
    gate = _resolve_party_accounts_gate(context, uid)
    if not gate:
        candidates = deal_gate_accounts_for_user(int(uid))
        if candidates and await _prompt_account_deal_choice(
            update, context, candidates
        ):
            raise ApplicationHandlerStop
        logger.debug(
            "deal_gate: skip text uid=%s — no accounts gate (gate=%s)",
            uid,
            gate.get("gate_status") if gate else None,
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
    await _set_deal_gate_stage(
        context,
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
        _clear_party_accounts_offer(
            context,
            int(uid),
            offer_id=int(offer_id),
        )
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
