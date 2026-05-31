"""
handlers/deal_gate.py — تأیید نهایی دوطرفه پس از پذیرش پیشنهاد

EN: Final yes/no gate, reminders, admin actions, account collection, transcript logging.
FA: دروازه تأیید، یادآوری، اقدام ادمین، جمع حساب، آرشیو در DB.
"""

from __future__ import annotations

import html as html_module
import json
import logging
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config.settings import ADMIN_IDS
from database.db import (
    deal_gate_active_for_user,
    deal_gate_delete,
    deal_gate_get,
    deal_gate_upsert,
    get_advert_offer_joined,
    get_euro_advert_by_rowid,
    negotiation_transcript_append_line,
    update_advert_offer_status,
    update_euro_advert_status,
)
from state import user_data_store

logger = logging.getLogger(__name__)

_RTL = "\u200f"
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
    )
    recipients = _deal_admin_recipient_ids()
    if not recipients:
        logger.warning("deal_admin_sync: no recipients offer=%s", oid)
        return

    stored = _parse_admin_notify_mids(gate)
    updated = dict(stored)
    plain = re.sub(r"<[^>]+>", "", admin_html or "")

    for chat_id in recipients:
        mid = stored.get(chat_id)
        if mid:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=mid,
                    text=admin_html,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                logger.info(
                    "deal_admin_sync: edited offer=%s chat_id=%s mid=%s",
                    oid,
                    chat_id,
                    mid,
                )
                continue
            except BadRequest as e:
                err = str(e).lower()
                if "message is not modified" in err:
                    continue
            except TelegramError as e:
                logger.warning(
                    "deal_admin_sync: edit failed offer=%s chat_id=%s: %s",
                    oid,
                    chat_id,
                    e,
                )

        try:
            sent = await bot.send_message(
                chat_id,
                admin_html,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            updated[chat_id] = sent.message_id
            logger.info(
                "deal_admin_sync: sent offer=%s chat_id=%s mid=%s",
                oid,
                chat_id,
                sent.message_id,
            )
        except BadRequest:
            try:
                sent = await bot.send_message(chat_id, plain, disable_web_page_preview=True)
                updated[chat_id] = sent.message_id
            except TelegramError as e2:
                logger.warning(
                    "deal_admin_sync: send failed offer=%s chat_id=%s: %s",
                    oid,
                    chat_id,
                    e2,
                )
        except Forbidden:
            logger.warning(
                "deal_admin_sync: forbidden chat_id=%s (ادمین /start بزند)",
                chat_id,
            )
        except TelegramError as e:
            logger.warning(
                "deal_admin_sync: send failed offer=%s chat_id=%s: %s",
                oid,
                chat_id,
                e,
            )

    if updated != stored:
        deal_gate_upsert(
            offer_id=oid,
            advert_rowid=aid,
            buyer_telegram_id=buyer_id,
            seller_telegram_id=seller_id,
            admin_notify_mids=_serialize_admin_notify_mids(updated),
        )


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
        "• شماره شبا (IR…)\n"
        "• شماره کارت (در صورت نیاز)"
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
) -> None:
    from utils.telegram_utils import send_or_replace_main_menu

    if deal_complete:
        text = (
            f"{_RTL}✅ <b>اطلاعات لازم برای ادمین ارسال شد.</b>\n\n"
            f"{_RTL}لطفاً صبور باشید تا مراحل بعدی توسط ادمین انجام شود.\n"
            f"{_RTL}⚠️ بدون هماهنگی ادمین واریز نکنید."
        )
    else:
        text = (
            f"{_RTL}✅ <b>اطلاعات حساب شما ثبت شد.</b>\n\n"
            f"{_RTL}پس از ثبت حساب طرف مقابل، جزئیات برای ادمین ارسال می‌شود.\n"
            f"{_RTL}لطفاً صبور باشید تا مراحل بعدی توسط ادمین انجام شود."
        )
    await send_or_replace_main_menu(
        context.bot,
        chat_id=int(user_id),
        user_id=int(user_id),
        store=user_data_store,
        text=text,
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
    buyer_mid = seller_mid = None
    if buyer_id:
        try:
            sent = await bot.send_message(
                buyer_id,
                _gate_intro_html(advert, row, party_label="خریدار یورو"),
                parse_mode=ParseMode.HTML,
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
            sent = await bot.send_message(
                seller_id,
                _gate_intro_html(advert, row, party_label="فروشنده یورو"),
                parse_mode=ParseMode.HTML,
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
    jq.run_repeating(
        _job_hourly_status,
        interval=_HOURLY_SEC,
        first=_HOURLY_SEC,
        data={"offer_id": oid},
        name=hr,
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


async def deal_gate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    parts = (q.data or "").strip().split("|")
    if len(parts) < 3:
        return
    if parts[0] == "deal":
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
        from handlers.offers import _clear_offer_flow

        _clear_offer_flow(context)
    except Exception:
        pass
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
                f"{_RTL}لطفاً در یک پیام به‌صورت کامل "
                f"اطلاعات حساب دریافت یورو را بفرستید:\n\n"
                f"<pre>{html_module.escape(hint)}</pre>"
            )
        else:
            body = (
                f"{_RTL}✅ <b>هر دو طرف تأیید نهایی کردند.</b>\n\n"
                f"{_RTL}لطفاً در یک پیام به‌صورت کامل "
                f"اطلاعات حساب دریافت تومان را بفرستید:\n\n"
                f"<pre>{html_module.escape(hint)}</pre>"
            )
        try:
            sent = await context.bot.send_message(
                uid,
                body,
                parse_mode=ParseMode.HTML,
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
    for uid in (buyer_id, seller_id):
        if not uid:
            continue
        try:
            await context.bot.send_message(
                uid, msg, parse_mode=ParseMode.HTML
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


async def deal_gate_accounts_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """پیام متنی حساب‌ها پس از تأیید دوطرفه."""
    if not update.message or not update.effective_user:
        return
    from utils.flow_guards import user_advert_offer_wizard_active

    if user_advert_offer_wizard_active(context):
        return
    gate = deal_gate_active_for_user(update.effective_user.id)
    if not gate:
        return
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
        return

    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="accounts",
        **{key: text[:2000]},
    )
    _log(oid, f"حساب {party}: {text[:500]}", from_role="buyer" if is_buyer else "seller")
    logger.info(
        "deal_gate: account saved offer=%s uid=%s party=%s",
        oid,
        uid,
        party,
    )

    gate = deal_gate_get(oid)
    both_done = bool(
        (gate.get("buyer_accounts_text") or "").strip()
        and (gate.get("seller_accounts_text") or "").strip()
    )

    await _purge_user_deal_chat(
        context.bot, user_data_store, uid, oid, gate
    )
    try:
        await update.message.delete()
    except Exception:
        pass

    await sync_deal_admin_notification(context.bot, oid)

    if both_done:
        await _complete_deal(context, oid)
    else:
        await _notify_user_account_wait(context, uid, deal_complete=False)
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
        await _notify_user_account_wait(context, int(uid), deal_complete=True)
