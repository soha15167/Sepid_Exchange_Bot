"""
handlers/admin.py — Admin panel / پنل مدیریت

EN:
  Users CRUD, restrictions, advert list/edit/delete, offer management,
  proxy offers, fee override, bot on/off, channel post refresh.

FA:
  مدیریت کاربران، آگهی‌ها، پیشنهادها، محدودیت، کارمزد دستی، خاموش/روشن ربات.
"""

from __future__ import annotations

import asyncio
import html as html_module
import re
import sqlite3
import time
from datetime import datetime

from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config.settings import ADMIN_IDS
from config.settings import (
    ADVERT_CHANNEL_ID,
    BOT_RESTART_BROADCAST_MENU,
    BOT_RESTART_COMMAND,
    CHANNEL_USERNAME,
)
from database.db import (
    get_db,
    delete_user,
    update_user_field,
    display_name_exists,
    search_users,
    save_user,
    get_user,
    get_user_by_id,
    get_user_by_phone,
    get_setting,
    set_setting,
    set_user_restriction,
    get_restriction_block_message,
    get_all_registered_telegram_ids,
    get_euro_advert_by_rowid,
    get_offer_by_advert_and_seq,
    admin_delete_offer_by_id,
    admin_update_offer_rate,
    admin_update_offer_proposed_euro,
    get_advert_offer_joined,
    insert_advert_offer,
    list_advert_offers_joined_for_advert,
)
from state import user_data_store
from keyboards.admin_home import admin_home_inline_keyboard
from keyboards.menus import (
    admin_panel_back_keyboard,
    admin_restrict_actions_keyboard,
    EXCHANGE_OPTION,
    PAYMENT_OPTIONS,
)
from utils.euro_fees import format_fee_eur, advert_fee_override_eur
from handlers.offers import (
    offer_proposal_inline_button,
    refresh_advert_channel_post,
    refresh_offer_notification_cards_after_rate_change,
    negotiation_cleanup_for_offer,
    dispatch_offer_created_notifications,
    purge_offer_thread_messages,
    neg_transcript_get,
    _public_offer_name,
    _is_hybrid_euro_exchange_advert,
    _offer_skips_toman_rate_step,
)
from messages import texts
from models.enums import UserState
from utils.sms import (
    generate_sms_code,
    is_otp_code_valid,
    try_send_verification_sms,
    uses_twilio_verify,
)
from utils.validators import is_valid_phone, is_valid_email
from utils.telegram_utils import (
    remember_cleanup_id,
    cleanup_ids,
    send_or_replace_main_menu,
    remove_main_menu_anchor_message,
)

# سرورهای قدیمی فقط ADMIN_RESTRICT_LEVEL در enum دارند؛ بدون این بلوک import با AttributeError می‌میرد.
_ADMIN_RESTRICT_DAYS_STATE_NAME = (
    UserState.ADMIN_RESTRICT_DAYS.name
    if hasattr(UserState, "ADMIN_RESTRICT_DAYS")
    else UserState.ADMIN_RESTRICT_LEVEL.name
)

# ویزارد «ویرایش آگهی»: منوی انتخاب فیلد و مراحل مقدار با اینلاین (چت تمیزتر)
_ADMIN_KB_BACK = "⬅️ بازگشت"
_ADMIN_KB_CANCEL = "❌ انصراف"


def _admin_edit_advert_fields_inline_kb(*, show_rate_row: bool) -> InlineKeyboardMarkup:
    def cell(label: str, field: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"admin_adv_edf|pick|{field}")

    if show_rate_row:
        grid = [
            [cell("👤 نام آگهی‌دهنده", "full_name"), cell("💶 مقدار یورو", "euro_amount")],
            [cell("💰 نرخ (تومان)", "rate_toman"), cell("📝 توضیحات", "description")],
            [cell("💳 روش‌ها", "methods"), cell("🌍 کشور (خارج ایران)", "account_country")],
            [cell("🏦 واریز آنی", "instant_transfer"), cell("🧾 کارمزد (یورو)", "fee_override_eur")],
        ]
    else:
        grid = [
            [cell("👤 نام آگهی‌دهنده", "full_name"), cell("💶 مقدار یورو", "euro_amount")],
            [cell("📝 توضیحات", "description"), cell("💳 روش‌ها", "methods")],
            [cell("🌍 کشور (خارج ایران)", "account_country"), cell("🏦 واریز آنی", "instant_transfer")],
            [cell("🧾 کارمزد (یورو)", "fee_override_eur")],
        ]
    grid.append(
        [
            InlineKeyboardButton("⬅️ شمارهٔ دیگر آگهی", callback_data="admin_adv_edf|back_id"),
            InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel"),
        ]
    )
    return InlineKeyboardMarkup(grid)


def _admin_edit_advert_value_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ بازگشت به فیلدها", callback_data="admin_adv_edf|back_fields")],
            [InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")],
        ]
    )


def _admin_edit_user_fields_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🏷️ نام نمایشی آگهی", "🔗 یوزرنیم تلگرام"],
            ["👤 نام", "👤 نام خانوادگی"],
            ["📱 شماره", "📧 ایمیل"],
            ["🏠 آدرس"],
            [_ADMIN_KB_BACK, _ADMIN_KB_CANCEL],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def _admin_exit_edit_advert_wizard(context: ContextTypes.DEFAULT_TYPE, update: Update) -> None:
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    ids = user_data_store.setdefault(uid, {}).pop(_ADMIN_CLEANUP_KEY, [])
    await cleanup_ids(context.bot, chat_id=chat_id, ids=ids)
    ids_u = user_data_store.setdefault(uid, {}).pop(_ADMIN_USER_INPUT_KEY, [])
    await cleanup_ids(context.bot, chat_id=chat_id, ids=ids_u)
    await remove_main_menu_anchor_message(context.bot, user_id=uid, store=user_data_store)
    for k in ("edit_advert_id", "edit_advert_field", "edit_adv_methods", "edit_adv_instant"):
        context.user_data.pop(k, None)
    context.user_data["state"] = UserState.ADMIN_MENU.name
    _persist_admin_wizard_state(uid, context)
    await _best_effort_remove_keyboard(update)
    await _try_restore_admin_dashboard(context, context.bot)


async def _admin_back_edit_advert_to_id_step(context: ContextTypes.DEFAULT_TYPE, update: Update) -> None:
    uid = update.effective_user.id
    for k in ("edit_advert_id", "edit_advert_field", "edit_adv_methods", "edit_adv_instant"):
        context.user_data.pop(k, None)
    context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_ID.name
    _persist_admin_wizard_state(uid, context)
    await _best_effort_remove_keyboard(update)


async def _admin_exit_edit_user_wizard(context: ContextTypes.DEFAULT_TYPE, update: Update) -> None:
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    ids = user_data_store.setdefault(uid, {}).pop(_ADMIN_CLEANUP_KEY, [])
    await cleanup_ids(context.bot, chat_id=chat_id, ids=ids)
    context.user_data.pop("edit_user_id", None)
    context.user_data.pop("edit_field", None)
    context.user_data.pop("pending_phone_update", None)
    context.user_data["state"] = UserState.ADMIN_MENU.name
    _persist_admin_wizard_state(uid, context)
    await _best_effort_remove_keyboard(update)
    await _try_restore_admin_dashboard(context, context.bot)


def _is_admin(user_id: int) -> bool:
    return user_id in set(ADMIN_IDS or [])


# Right-to-left mark: تلگرام با متن فارسی + ایموجی/اعداد LTR را اشتباه چین می‌کند؛ یک RLM اول متن معمولاً کافی است.
_RTL = "\u200f"


def _admin_reset_subflow_keys(context: ContextTypes.DEFAULT_TYPE) -> None:
    """پاک کردن دادهٔ ویزارد ادمین وقتی کاربر دکمهٔ دیگری از منوی ریپلای را می‌زند."""
    for key in (
        "admin_add_ad_step",
        "admin_new_advert_owner_id",
        "admin_post_advert_for",
        "edit_advert_id",
        "edit_advert_field",
        "edit_adv_methods",
        "edit_adv_instant",
        "delete_advert_id",
        "delete_user_id",
        "edit_user_id",
        "edit_field",
        "admin_exch",
        "pending_phone_update",
        "new_user_id",
        "new_user",
        "restrict_uid",
        "admin_offer_advert",
        "admin_offer_db_id",
        "admin_offer_edit_action",
        "admin_proxy_aid",
        "admin_proxy_alias",
        "admin_proxy_rate",
    ):
        context.user_data.pop(key, None)


_CANCEL = "❌ انصراف"

_EDIT_FIELD_LABEL_TO_FIELD = {
    "🏷️ نام نمایشی آگهی": "display_name",
    "🔗 یوزرنیم تلگرام": "username",
    "👤 نام": "full_name",
    "👤 نام خانوادگی": "last_name",
    "📱 شماره": "phone_number",
    "📧 ایمیل": "email",
    "🏠 آدرس": "address",
}

def _inline_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")]])


def _admin_add_user_otp_keyboard() -> InlineKeyboardMarkup:
    """پس از وارد کردن شماره در «افزودن کاربر» — پیامک یا نمایش کد؛ انصراف → منوی ادمین."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 ارسال مجدد پیامک", callback_data="admin_add_otp_resend")],
            [InlineKeyboardButton("🔐 نمایش کد در چت", callback_data="admin_add_otp_show")],
            [InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")],
        ]
    )

def _inline_advert_edit_done() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ ثبت", callback_data="admin_adv_edit_done")],
            [InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")],
        ]
    )

def _parse_int_from_text(text: str) -> int | None:
    """Extract digits from any text and parse as int (handles RTL/LTR marks)."""
    raw = (text or "").strip()
    digits = re.findall(r"\d+", raw)
    if not digits:
        return None
    try:
        return int("".join(digits))
    except Exception:
        return None


def _admin_offer_ad_summary_html(advert: dict) -> str:
    body = _build_channel_ad_text(advert)
    lines = [
        ln
        for ln in body.splitlines()
        if ln.strip() and not ln.strip().startswith("🤖")
    ]
    return "\n".join(lines).strip()


def _admin_offer_row_summary_html(advert: dict, orow: dict, *, aid: int, seq: int) -> str:
    rate = int(orow.get("rate_toman") or 0)
    adv_amt = _fmt_eur_amount(advert.get("euro_amount"))
    try:
        pe = int(orow.get("proposed_euro_amount") or 0)
    except (TypeError, ValueError):
        pe = 0
    pe_line = (
        f"{_RTL}💶 مقدار یوروی پیشنهاد: <b>{pe:,}</b> (در آگهی: {adv_amt})\n"
        if pe > 0
        else f"{_RTL}💶 مقدار یورو: <b>{adv_amt}</b> (همان آگهی)\n"
    )
    desc = (orow.get("description") or "").strip() or "—"
    pc = (orow.get("proposer_account_country") or "").strip() or "—"
    return (
        f"{_RTL}📌 <b>پیشنهاد {seq}</b> روی آگهی <b>{aid}</b>\n"
        f"{_RTL}💰 نرخ: <b>{rate:,}</b> تومان\n"
        f"{pe_line}"
        f"{_RTL}🏦 کشور حساب پیشنهاددهنده: {html_module.escape(pc)}\n"
        f"{_RTL}📝 توضیحات: {html_module.escape(desc)}\n"
    )


def _admin_offers_list_html(offers: list[dict]) -> str:
    if not offers:
        return f"{_RTL}<i>هنوز پیشنهادی برای این آگهی ثبت نشده است.</i>\n"
    lines = [f"{_RTL}<b>پیشنهادهای این آگهی</b> ({len(offers)} مورد):"]
    for o in offers:
        seq = int(o.get("seq_in_advert") or o.get("id") or 0)
        rate = int(o.get("rate_toman") or 0)
        st = _admin_neg_offer_status_fa(o.get("status"))
        try:
            pe = int(o.get("proposed_euro_amount") or 0)
        except (TypeError, ValueError):
            pe = 0
        amt = f" — <b>{pe:,}</b> یورو" if pe > 0 else ""
        alias = (o.get("offer_alias_name") or "").strip()
        if alias:
            who = html_module.escape(alias)
        else:
            who = str(int(o.get("proposer_telegram_id") or 0))
        lines.append(
            f"{_RTL}• <b>{seq}</b> — <b>{rate:,}</b> تومان{amt} — {who} — <i>{st}</i>"
        )
    lines.append(f"{_RTL}\nیک پیشنهاد را از دکمه‌ها انتخاب کنید یا شمارهٔ آن را بفرستید.")
    return "\n".join(lines)


def _admin_offer_pick_keyboard(offers: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row_buf: list[InlineKeyboardButton] = []
    for o in offers:
        oid = int(o["id"])
        seq = int(o.get("seq_in_advert") or oid)
        row_buf.append(
            InlineKeyboardButton(f"📌 {seq}", callback_data=f"adm|ofpick|{oid}")
        )
        if len(row_buf) >= 3:
            rows.append(row_buf)
            row_buf = []
    if row_buf:
        rows.append(row_buf)
    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")])
    return InlineKeyboardMarkup(rows)


def _admin_offer_action_keyboard(offer_db_id: int, *, advert_id: int) -> InlineKeyboardMarkup:
    oid = int(offer_db_id)
    aid = int(advert_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 حذف", callback_data=f"adm|ofdel|{oid}"),
                InlineKeyboardButton("💰 نرخ", callback_data=f"adm|ofrate|{oid}"),
            ],
            [InlineKeyboardButton("💶 مقدار یورو", callback_data=f"adm|ofeur|{oid}")],
            [
                InlineKeyboardButton(
                    "◀️ لیست پیشنهادها", callback_data=f"adm|oflist|{aid}"
                )
            ],
            [InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")],
        ]
    )


async def _admin_offer_show_pick_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    aid: int,
    advert: dict,
) -> None:
    offers = list_advert_offers_joined_for_advert(aid)
    summary = _admin_offer_ad_summary_html(advert)
    body = (
        f"{_RTL}📋 <b>خلاصه آگهی {aid}</b>\n\n{summary}\n\n"
        f"{_admin_offers_list_html(offers)}"
    )
    kb = _admin_offer_pick_keyboard(offers) if offers else _inline_cancel()
    context.user_data["admin_offer_advert"] = aid
    context.user_data.pop("admin_offer_db_id", None)
    context.user_data.pop("admin_offer_edit_action", None)
    context.user_data["state"] = UserState.ADMIN_MANAGE_OFFER_SEQ.name
    _persist_admin_wizard_state(update.effective_user.id, context)
    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.edit_text(
                body,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            _admin_offer_wiz_note(context, update.callback_query.message.message_id)
            return
        except Exception:
            pass
    sent = await _admin_reply(
        update,
        body,
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    _admin_offer_wiz_note(context, getattr(sent, "message_id", None))


async def _admin_offer_show_detail_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    aid: int,
    oid: int,
) -> None:
    advert = get_euro_advert_by_rowid(aid)
    row = get_advert_offer_joined(oid)
    if not advert or not row or int(row.get("advert_rowid") or 0) != aid:
        await _admin_reply(update, f"{_RTL}❌ پیشنهاد پیدا نشد.", reply_markup=_inline_cancel())
        return
    seq = int(row.get("seq_in_advert") or row.get("id") or oid)
    context.user_data["admin_offer_advert"] = aid
    context.user_data["admin_offer_db_id"] = oid
    context.user_data.pop("admin_offer_edit_action", None)
    context.user_data["state"] = UserState.ADMIN_MANAGE_OFFER_CMD.name
    _persist_admin_wizard_state(update.effective_user.id, context)
    offer_blk = _admin_offer_row_summary_html(advert, row, aid=aid, seq=seq)
    body = (
        f"{_RTL}✅ <b>پیشنهاد انتخاب شد</b>\n\n{offer_blk}\n"
        f"{_RTL}یکی از دکمه‌ها را بزنید:"
    )
    kb = _admin_offer_action_keyboard(oid, advert_id=aid)
    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.edit_text(
                body,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            _admin_offer_wiz_note(context, update.callback_query.message.message_id)
            return
        except Exception:
            pass
    sent = await _admin_reply(
        update, body, reply_markup=kb, parse_mode=ParseMode.HTML
    )
    _admin_offer_wiz_note(context, getattr(sent, "message_id", None))


async def _admin_offer_execute_delete(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    oid: int,
    aid: int,
) -> bool:
    meta = admin_delete_offer_by_id(oid)
    if not meta:
        return False
    await purge_offer_thread_messages(
        context.bot,
        user_data_store,
        int(meta["owner_id"]),
        int(meta["proposer_telegram_id"]),
        oid,
    )
    negotiation_cleanup_for_offer(context.application.bot_data, oid)
    await refresh_advert_channel_post(context.bot, aid)
    for uid in (meta["owner_id"], meta["proposer_telegram_id"]):
        if uid:
            try:
                await context.bot.send_message(
                    int(uid),
                    f"{_RTL}یک پیشنهاد برای آگهی {aid} توسط مدیریت حذف شد. "
                    f"{_RTL}شمارهٔ پیشنهادها به‌روز شد.",
                )
            except Exception:
                pass
    return True


async def _admin_offer_finish_edit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    aid: int,
    oid: int,
    success_html: str,
) -> None:
    cid = update.effective_chat.id
    await _admin_offer_wiz_purge(context.bot, cid, context)
    context.user_data.pop("admin_offer_edit_action", None)
    context.user_data["state"] = UserState.ADMIN_MENU.name
    _persist_admin_wizard_state(update.effective_user.id, context)
    await _admin_reply(
        update,
        success_html,
        reply_markup=None,
        context=context,
        parse_mode=ParseMode.HTML,
    )


def _admin_parse_offer_manage_cmd(text: str) -> tuple[str, int | None]:
    """
    delete | rate | euro | unknown
  """
    raw = (text or "").strip()
    low = raw.casefold()
    if low in ("حذف", "delete", "del"):
        return "delete", None
    m = re.match(
        r"^(?:نرخ|rate)\s*[:：]?\s*([\d\u06f0-\u06f9\u0660-\u0669,\s]+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        return "rate", _parse_int_from_text(m.group(1))
    m = re.match(
        r"^(?:یورو|مقدار|eur)\s*[:：]?\s*([\d\u06f0-\u06f9\u0660-\u0669,\s]+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        return "euro", _parse_int_from_text(m.group(1))
    if re.fullmatch(r"[\d\u06f0-\u06f9\u0660-\u0669,\s]+", raw):
        return "rate", _parse_int_from_text(raw)
    return "unknown", None


def _admin_neg_offer_status_fa(status_val: str | None) -> str:
    s = (status_val or "pending").strip().lower()
    return {
        "pending": "در انتظار",
        "accepted": "پذیرفته",
        "rejected": "رد شده",
    }.get(s, html_module.escape(s))


def _admin_negotiation_report_chunks(
    advert_rowid: int, offers: list[dict], app_data: dict
) -> list[str]:
    """چند تکهٔ HTML برای ارسال جداگانه (محدودیت طول تلگرام)."""
    RLM = "\u200f"
    MAX = 3800
    header = (
        f"{RLM}🗣️ <b>مذاکرات آگهی</b> <code>#{advert_rowid}</code> · "
        f"پیشنهادها: <b>{len(offers)}</b>\n"
    )
    if not offers:
        return [header + f"\n{RLM}هیچ پیشنهادی برای این آگهی ثبت نشده."]
    blocks: list[str] = []
    for row in offers:
        oid = int(row["id"])
        seq = int(row.get("seq_in_advert") or oid)
        tid = int(row.get("proposer_telegram_id") or 0)
        alias = (row.get("offer_alias_name") or "").strip()
        pname = alias if alias else _public_offer_name(get_user(tid), tid)
        entries = neg_transcript_get(app_data, oid)
        lines_plain: list[str] = []
        for e in entries:
            fr = (e.get("from") or "").strip().lower()
            if fr == "owner":
                lab = "آگهی‌دهنده"
            elif fr == "proposer":
                lab = "پیشنهاددهنده"
            elif fr == "other":
                lab = "؟"
            else:
                lab = str(e.get("from") or "؟")
            lines_plain.append(f"{lab}: {e.get('text') or ''}")
        inner = "\n".join(lines_plain) if lines_plain else "—"
        box = html_module.escape(inner)
        st_fa = _admin_neg_offer_status_fa(row.get("status"))
        blocks.append(
            f"\n{RLM}━━━━ <b>پیشنهاد #{seq}</b> · id <code>{oid}</code> · وضعیت: <b>{st_fa}</b>\n"
            f"{RLM}پیشنهاددهنده: <b>{html_module.escape(pname)}</b> <code>{tid}</code>\n"
            f"<pre>{box}</pre>\n"
        )
    parts: list[str] = []
    buf = header
    for block in blocks:
        if len(buf) + len(block) > MAX:
            parts.append(buf)
            buf = f"{RLM}<i>ادامه…</i>\n" + block
        else:
            buf += block
    if buf:
        parts.append(buf)
    return parts


def _friendly_db_error(e: Exception) -> str:
    msg = str(e) or e.__class__.__name__
    low = msg.lower()
    if "idx_users_display_name_unique" in low or "display_name" in low:
        return "❌ نام نمایشی آگهی تکراری است."
    if "unique" in low and "telegram_id" in low:
        return "❌ این آیدی تلگرام قبلاً ثبت شده است."
    return f"❌ اضافه نشد. ({msg})"

def _fmt_thousands(val) -> str:
    """Format numbers like 200,000 for readability (best effort)."""
    if val is None:
        return "—"
    if isinstance(val, (int, float)):
        try:
            return f"{int(val):,}"
        except Exception:
            return str(val)
    s = str(val).strip()
    try:
        # Handle numeric strings like "200000" or "200000.0"
        n = int(float(s.replace(",", "")))
        return f"{n:,}"
    except Exception:
        return s


def _format_methods_list_rtl(methods: list[str]) -> str:
    from utils.channel_format import format_payment_methods_rtl

    return format_payment_methods_rtl(methods, html=True)


def _fmt_eur_amount(amount) -> str:
    if amount is None:
        return "—"
    try:
        return f"{int(amount):,}"
    except (TypeError, ValueError):
        return str(amount)


def _advert_country_display_line(account_country_raw, advert: dict | None = None) -> str:
    """یک خط HTML برای کشور؛ برچسب کوتاه برای خرید/فروش با نرخ تومان."""
    from utils.channel_format import format_country_display_line

    return format_country_display_line(account_country_raw, advert, html=True)


def _channel_ad_footer(advert: dict, *, euro_exchange_no_rate: bool = False) -> str:
    from utils.channel_format import format_channel_ad_footer

    return format_channel_ad_footer(
        bot_username=advert.get("bot_username"),
        euro_exchange_no_rate=euro_exchange_no_rate,
    )


def _build_channel_ad_text(advert: dict) -> str:
    """
    Build channel text from euro_adverts row.
    Supports operation in {"خرید","فروش","معاوضه"} (best effort).
    """
    advert_id = advert.get("rowid") or advert.get("id") or advert.get("advert_id")
    full_name = advert.get("full_name") or "—"
    operation = advert.get("operation") or "—"
    amount = advert.get("euro_amount")
    rate = advert.get("rate_toman")
    desc = advert.get("description") or "—"
    methods_raw = advert.get("methods") or ""
    city_ir = advert.get("city_ir") or "—"
    city_int = advert.get("city_int") or "—"
    instant_transfer = advert.get("instant_transfer")

    real_link = f"https://t.me/{CHANNEL_USERNAME}/{advert.get('channel_message_id')}" if advert.get("channel_message_id") else f"https://t.me/{CHANNEL_USERNAME}/..."

    euro_ex = int(advert.get("euro_exchange") or 0) == 1
    is_legacy_exchange = operation == "معاوضه"
    is_hybrid_exchange = (not is_legacy_exchange) and euro_ex and operation in ("خرید", "فروش")
    fee_ov = advert_fee_override_eur(advert)

    if is_legacy_exchange:
        method = methods_raw or "—"
        show_instant = bool(instant_transfer)
        instant_line = f"⚡ <b>امکان واریز آنی:</b> {instant_transfer}\n" if show_instant else ""
        amt_int = None
        try:
            amt_int = int(amount) if amount is not None else None
        except (TypeError, ValueError):
            amt_int = None
        ctry_line = _advert_country_display_line(advert.get("account_country"), advert)
        cit = (str(city_int).strip() if city_int is not None else "")
        foreign_city_line = f"🌆 <b>شهر خارج:</b> {city_int}\n" if cit and cit not in ("—", "-", "–") else ""
        return (
            f"📋 <b><a href=\"{real_link}\">آگهی شماره {advert_id}</a></b>\n\n"
            f"👤 <b>آگهی‌دهنده:</b> {full_name}\n"
            f"🏷️ <b>نوع آگهی:</b> معاوضه Euro به Euro\n"
            "🔀 <b>روش معاوضه:</b>\n"
            f"{_RTL}یورو به یورو\n\n"
            f"💶 <b>مقدار:</b> {_fmt_eur_amount(amount)} یورو\n"
            f"🧾 <b>کارمزد (هر طرف):</b> {format_fee_eur(amt_int, fee_ov)}\n\n"
            f"{ctry_line}"
            f"{foreign_city_line}"
            f"\u200f🏙️ <b>شهر ایران:</b> {city_ir}\n\n"
            f"📦 <b>روش دریافت/تحویل:</b> {method}\n"
            f"{instant_line}"
            f"📄 <b>توضیحات:</b> {desc}"
            f"{_channel_ad_footer(advert, euro_exchange_no_rate=True)}"
        )

    if is_hybrid_exchange:
        side = operation
        advert_type = "خرید یورو" if side == "خرید" else "فروش یورو"
        method = methods_raw or "—"
        amt_int = None
        try:
            amt_int = int(amount) if amount is not None else None
        except (TypeError, ValueError):
            amt_int = None
        in_person_value = "دریافت حضوری" if side == "خرید" else "تحویل حضوری"
        show_instant = side != "خرید" and method == "امکان واریز به حساب دارم"
        instant_line = f"⚡ <b>امکان واریز آنی:</b> {instant_transfer}\n" if show_instant and instant_transfer else ""
        show_foreign_city = method == in_person_value
        foreign_city_line = f"🌆 <b>شهر خارج:</b> {city_int}\n" if show_foreign_city else ""
        foreign_country_line = _advert_country_display_line(advert.get("account_country"), advert)
        rtl_city_ir_line = f"\u200f🏙️ <b>شهر ایران:</b> {city_ir}"
        door_label = "روش دریافت" if side == "خرید" else "روش تحویل"
        return (
            f"📋 <b><a href=\"{real_link}\">آگهی شماره {advert_id}</a></b>\n\n"
            f"👤 <b>آگهی‌دهنده:</b> {full_name}\n"
            f"🏷️ <b>نوع آگهی:</b> {advert_type}\n"
            "🔀 <b>روش معاوضه:</b>\n"
            f"{_RTL}یورو به یورو\n\n"
            f"💶 <b>مقدار:</b> {_fmt_eur_amount(amount)} یورو\n"
            f"🧾 <b>کارمزد (هر طرف):</b> {format_fee_eur(amt_int, fee_ov)}\n\n"
            f"{foreign_country_line}"
            f"{foreign_city_line}"
            f"{rtl_city_ir_line}\n\n"
            f"📦 <b>{door_label}:</b> {method}\n"
            f"{instant_line}"
            f"📄 <b>توضیحات:</b> {desc}"
            f"{_channel_ad_footer(advert, euro_exchange_no_rate=True)}"
        )

    # buy/sell (نرخ تومان)
    advert_type = "خرید یورو" if operation == "خرید" else "فروش یورو"
    methods_list = [m.strip() for m in methods_raw.split(",")] if methods_raw else []
    methods_label = "روش‌های دریافت" if operation == "خرید" else "روش‌های پرداخت"
    methods_block = f"💳 <b>{methods_label}:</b>\n{_format_methods_list_rtl(methods_list)}\n\n"
    instant_line = f"⚡ <b>امکان واریز آنی:</b> {instant_transfer}\n" if (instant_transfer and operation != "خرید") else ""

    amt_int = None
    try:
        amt_int = int(amount) if amount is not None else None
    except (TypeError, ValueError):
        amt_int = None

    ctry_line = _advert_country_display_line(advert.get("account_country"), advert)

    try:
        rate_i = int(rate) if rate is not None and str(rate).strip() != "" else 0
    except (TypeError, ValueError):
        rate_i = 0

    return (
        f"📋 <b><a href=\"{real_link}\">آگهی شماره {advert_id}</a></b>\n\n"
        f"👤 <b>آگهی‌دهنده:</b> {full_name}\n"
        f"🏷️ <b>نوع آگهی:</b> {advert_type}\n"
        f"{methods_block}"
        f"💶 <b>مقدار:</b> {_fmt_eur_amount(amount)} یورو\n"
        f"💰 <b>نرخ:</b> {rate_i:,} تومان\n"
        f"🧾 <b>کارمزد (هر طرف):</b> {format_fee_eur(amt_int, fee_ov)}\n\n"
        f"{ctry_line}"
        f"{instant_line}"
        f"📄 <b>توضیحات:</b> {desc}"
        f"{_channel_ad_footer(advert)}"
    )


async def _admin_deliver_negotiation_report(
    update: Update, context: ContextTypes.DEFAULT_TYPE, advert_id: int
) -> str | None:
    """ارسال گزارش مذاکرات؛ در صورت خطا رشتهٔ خطا، در صورت موفقیت None."""
    if not get_euro_advert_by_rowid(advert_id):
        return "ℹ️ آگهی پیدا نشد."
    offers = list_advert_offers_joined_for_advert(advert_id)
    chunks = _admin_negotiation_report_chunks(
        advert_id, offers, context.application.bot_data
    )
    uid = update.effective_user.id
    cid = update.effective_chat.id
    for i, ch in enumerate(chunks):
        if i == len(chunks) - 1:
            await _admin_reply(
                update,
                ch,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_panel_back_keyboard(),
                context=context,
                disable_web_page_preview=True,
            )
        else:
            sent = await context.bot.send_message(
                cid,
                text=ch,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            remember_cleanup_id(
                user_data_store, uid, sent.message_id, _ADMIN_CLEANUP_KEY
            )
    return None


async def admin_neg_ad_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ادمین: /neg_ad 74 — گزارش مذاکرات آگهی (بدون نیاز به دکمهٔ پنل)."""
    if not update.message or not update.effective_user:
        return
    if not _is_admin(update.effective_user.id):
        return
    parts = (update.message.text or "").strip().split(maxsplit=1)
    if len(parts) < 2 or not (parts[1] or "").strip():
        await update.message.reply_text(
            f"{_RTL}گزارش مذاکرات یک آگهی:\n"
            f"{_RTL}<code>/neg_ad شماره_آگهی</code>\n"
            f"{_RTL}مثال: <code>/neg_ad 74</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel_back_keyboard(),
        )
        return
    advert_id = _parse_int_from_text(parts[1])
    if advert_id is None:
        await update.message.reply_text(
            "❌ شماره آگهی معتبر وارد کنید.",
            reply_markup=admin_panel_back_keyboard(),
        )
        return
    err = await _admin_deliver_negotiation_report(update, context, advert_id)
    if err:
        await update.message.reply_text(
            err, reply_markup=admin_panel_back_keyboard()
        )


async def admin_add_user_otp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """افزودن کاربر ادمین — مرحلهٔ کد بعد از شماره (پیامک / نمایش کد)."""
    query = update.callback_query
    if not query or not query.message:
        return
    uid = query.from_user.id
    if not _is_admin(uid):
        return
    new_user = context.user_data.get("new_user") or {}
    if new_user.get("_step") != "verify_code":
        try:
            await query.answer("این دکمه فقط هنگام تأیید شمارهٔ کاربر جدید است.", show_alert=True)
        except Exception:
            pass
        return
    try:
        await query.answer()
    except Exception:
        pass
    phone = (new_user.get("phone_number") or "").strip()
    code = str(new_user.get("sms_code") or generate_sms_code())
    new_user["sms_code"] = code
    context.user_data["new_user"] = new_user
    chat_id = query.message.chat_id
    data = query.data or ""

    if data == "admin_add_otp_resend":
        if try_send_verification_sms(phone, code):
            new_user["otp_verify_twilio"] = uses_twilio_verify()
            context.user_data["new_user"] = new_user
            text = "📨 پیامک دوباره ارسال شد.\nلطفاً کدی که کاربر دریافت کرد را وارد کنید:"
        else:
            new_user["otp_verify_twilio"] = False
            context.user_data["new_user"] = new_user
            text = (
                "⚠️ پیامک ارسال نشد.\n"
                "«نمایش کد در چت» را بزنید یا کد را از پیام قبلی وارد کنید."
            )
        try:
            await query.edit_message_text(text, reply_markup=_admin_add_user_otp_keyboard())
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=_admin_add_user_otp_keyboard()
            )
        return

    if data == "admin_add_otp_show":
        if new_user.get("otp_verify_twilio"):
            text = (
                "ℹ️ با <b>Twilio Verify</b> کد فقط به خط موبایل کاربر پیامک می‌شود.\n"
                "از کاربر بخواهید کد را بگوید و همان را در ربات وارد کنید."
            )
        else:
            text = (
                f"🔐 کد تأیید (برای وارد کردن در ربات): <code>{code}</code>\n\n"
                "این کد را اینجا تایپ کنید تا کاربر ذخیره شود."
            )
        try:
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=_admin_add_user_otp_keyboard(),
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=_admin_add_user_otp_keyboard(),
            )
        return


async def admin_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline cancel for any admin step."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    if not _is_admin(user_id):
        return
    dash_cid = context.user_data.get("adm_dash_cid")
    dash_mid = context.user_data.get("adm_dash_mid")
    chat_id = query.message.chat_id if query.message else update.effective_chat.id
    ids = user_data_store.setdefault(user_id, {}).pop(_ADMIN_CLEANUP_KEY, [])
    await cleanup_ids(context.bot, chat_id=chat_id, ids=ids)
    ids_u = user_data_store.setdefault(user_id, {}).pop(_ADMIN_USER_INPUT_KEY, [])
    await cleanup_ids(context.bot, chat_id=chat_id, ids=ids_u)
    await remove_main_menu_anchor_message(context.bot, user_id=user_id, store=user_data_store)
    context.user_data.clear()
    context.user_data["state"] = UserState.ADMIN_MENU.name
    _persist_admin_wizard_state(user_id, context)
    if dash_cid is not None:
        context.user_data["adm_dash_cid"] = dash_cid
    if dash_mid is not None:
        context.user_data["adm_dash_mid"] = dash_mid
    try:
        await query.message.delete()
    except Exception:
        pass
    await _try_restore_admin_dashboard(context, context.bot)


async def admin_delete_advert_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    if not _is_admin(user_id):
        return

    m = re.match(r"^admin_del_adv_yes_(\d+)$", query.data or "")
    if not m:
        return
    advert_id = int(m.group(1))

    # Fetch channel message id first
    with get_db() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT channel_message_id FROM euro_adverts WHERE rowid = ?",
            (advert_id,),
        ).fetchone()
        cur.execute("DELETE FROM euro_adverts WHERE rowid = ?", (advert_id,))
        deleted = cur.rowcount > 0

    # Best-effort delete channel message
    try:
        if row and row[0]:
            await context.bot.delete_message(chat_id=ADVERT_CHANNEL_ID, message_id=int(row[0]))
    except Exception:
        pass

    chat_id = query.message.chat_id
    # Clean up confirm message
    try:
        await query.message.delete()
    except Exception:
        pass

    msg = "✅ آگهی حذف شد." if deleted else "ℹ️ آگهی پیدا نشد."
    if not await _admin_edit_dashboard(context, context.bot, msg):
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=admin_panel_back_keyboard())


async def admin_delete_user_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass
    admin_uid = query.from_user.id
    if not _is_admin(admin_uid):
        return

    m = re.match(r"^admin_del_user_yes_(\d+)$", query.data or "")
    if not m:
        return
    target_uid = int(m.group(1))

    context.user_data.pop("delete_user_id", None)
    context.user_data["state"] = UserState.ADMIN_MENU.name
    _persist_admin_wizard_state(admin_uid, context)

    ok = delete_user(target_uid)
    chat_id = query.message.chat_id if query.message else update.effective_chat.id
    try:
        if query.message:
            await query.message.delete()
    except Exception:
        pass

    msg = "✅ حذف شد." if ok else "ℹ️ کاربر پیدا نشد."
    if not await _admin_edit_dashboard(context, context.bot, msg):
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=admin_panel_back_keyboard())


async def _admin_apply_edit_advert_field_from_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, query, field: str
) -> None:
    """انتقال از مرحلهٔ انتخاب فیلد (اینلاین) به روش‌ها / واریز آنی / ورود مقدار."""
    uid = query.from_user.id
    advert_id = context.user_data.get("edit_advert_id")
    if not isinstance(advert_id, int):
        try:
            await query.edit_message_text(
                f"{_RTL}❌ خطا: آگهی انتخاب نشده.",
                reply_markup=_inline_cancel(),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return
    context.user_data["edit_advert_field"] = field
    if field == "methods":
        context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_METHODS.name
        with get_db() as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT methods, COALESCE(euro_exchange, 0) FROM euro_adverts WHERE rowid = ?",
                (advert_id,),
            ).fetchone()
        if row and int(row[1] or 0) == 1:
            selected = {EXCHANGE_OPTION}
        else:
            current = row[0] if row and row[0] else ""
            parts = [m.strip() for m in current.split(",") if m.strip()]
            if EXCHANGE_OPTION in parts:
                selected = {EXCHANGE_OPTION}
            else:
                selected = set(parts)
        context.user_data["edit_adv_methods"] = list(selected)
        _persist_admin_wizard_state(uid, context)
        try:
            await query.edit_message_text(
                f"{_RTL}💳 روش‌ها را انتخاب کنید:",
                reply_markup=_admin_methods_keyboard(selected),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return
    if field == "instant_transfer":
        context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_INSTANT.name
        with get_db() as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT instant_transfer FROM euro_adverts WHERE rowid = ?",
                (advert_id,),
            ).fetchone()
        current = row[0] if row and row[0] else None
        context.user_data["edit_adv_instant"] = current
        _persist_admin_wizard_state(uid, context)
        try:
            await query.edit_message_text(
                f"{_RTL}🏦 وضعیت واریز آنی را انتخاب کنید:",
                reply_markup=_admin_instant_keyboard(current),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_VALUE.name
    _persist_admin_wizard_state(uid, context)
    if field == "fee_override_eur":
        try:
            await query.edit_message_text(
                "🧾 <b>کارمزد (هر طرف، یورو)</b>\n\n"
                "عدد را وارد کنید (مثلاً <code>6</code>).\n"
                "برای <b>کارمزد ثابت صفر</b> بنویسید: <code>0</code>\n"
                "برای بازگشت به فرمول خودکار بنویسید: <b>خودکار</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=_admin_edit_advert_value_inline_kb(),
            )
        except Exception:
            pass
        return
    try:
        await query.edit_message_text(
            f"{_RTL}✏️ مقدار جدید را وارد کنید:\n\n"
            f"{_RTL}برای بازگشت یا انصراف از دکمه‌های زیر استفاده کنید.",
            parse_mode=ParseMode.HTML,
            reply_markup=_admin_edit_advert_value_inline_kb(),
        )
    except Exception:
        pass


def _admin_methods_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    # Same rules as user flow: multi-select for PayPal/Wise/Revolut/IBAN; EXCHANGE_OPTION is exclusive single-select.
    rows = []
    for o in PAYMENT_OPTIONS:
        checked = "✅ " if o in selected else ""
        rows.append([InlineKeyboardButton(f"{checked}{o}", callback_data=f"admin_adv_m_toggle|{o}")])
    ex_checked = "✅ " if EXCHANGE_OPTION in selected else ""
    rows.append(
        [InlineKeyboardButton(f"{ex_checked}{EXCHANGE_OPTION}", callback_data=f"admin_adv_m_toggle|{EXCHANGE_OPTION}")]
    )
    rows.append([InlineKeyboardButton("✅ ثبت", callback_data="admin_adv_edit_done")])
    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")])
    return InlineKeyboardMarkup(rows)


def _admin_instant_keyboard(current: str | None) -> InlineKeyboardMarkup:
    def _btn(label: str, val: str):
        prefix = "✅ " if (current == val) else ""
        return InlineKeyboardButton(prefix + label, callback_data=f"admin_adv_instant|{val}")

    return InlineKeyboardMarkup(
        [
            [_btn("دارم", "دارم"), _btn("ندارم", "ندارم")],
            [_btn("حذف این فیلد", "__clear__")],
            [InlineKeyboardButton("✅ ثبت", callback_data="admin_adv_edit_done")],
            [InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")],
        ]
    )


async def admin_advert_inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass
    if not _is_admin(query.from_user.id):
        return

    data = query.data or ""
    state = context.user_data.get("state")

    if data.startswith("admin_adv_edf|"):
        uid = query.from_user.id
        if state == UserState.ADMIN_EDIT_ADVERT_VALUE.name and data == "admin_adv_edf|back_fields":
            advert_id = context.user_data.get("edit_advert_id")
            context.user_data.pop("edit_advert_field", None)
            context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_FIELD.name
            adv_b = get_euro_advert_by_rowid(int(advert_id)) if isinstance(advert_id, int) else None
            show_b = not _offer_skips_toman_rate_step(adv_b) if adv_b else True
            _persist_admin_wizard_state(uid, context)
            try:
                await query.edit_message_text(
                    f"{_RTL}✏️ کدام فیلد آگهی را ویرایش کنیم؟",
                    parse_mode=ParseMode.HTML,
                    reply_markup=_admin_edit_advert_fields_inline_kb(show_rate_row=show_b),
                )
            except Exception:
                pass
            return
        if state == UserState.ADMIN_EDIT_ADVERT_FIELD.name:
            parts = data.split("|")
            if len(parts) >= 3 and parts[1] == "pick":
                field = parts[2]
                allowed = {
                    "full_name",
                    "euro_amount",
                    "rate_toman",
                    "description",
                    "methods",
                    "account_country",
                    "instant_transfer",
                    "fee_override_eur",
                }
                if field in allowed:
                    await _admin_apply_edit_advert_field_from_callback(
                        update, context, query, field
                    )
                return
            if len(parts) >= 2 and parts[1] == "back_id":
                await _admin_back_edit_advert_to_id_step(context, update)
                _persist_admin_wizard_state(uid, context)
                try:
                    await query.edit_message_text(
                        f"{_RTL}✏️ شماره آگهی (<code>rowid</code>) را وارد کنید:\n\n"
                        f"{_RTL}برای خروج دکمهٔ زیر را بزنید.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=_inline_cancel(),
                    )
                except Exception:
                    pass
                return
        return

    if data.startswith("admin_adv_m_toggle|") and state == UserState.ADMIN_EDIT_ADVERT_METHODS.name:
        method = data.split("|", 1)[1]
        selected = set(context.user_data.get("edit_adv_methods") or [])
        if method == EXCHANGE_OPTION:
            if EXCHANGE_OPTION in selected:
                selected = set()
            else:
                selected = {EXCHANGE_OPTION}
        else:
            selected.discard(EXCHANGE_OPTION)
            if method in selected:
                selected.remove(method)
            else:
                selected.add(method)
        context.user_data["edit_adv_methods"] = list(selected)
        await query.edit_message_reply_markup(reply_markup=_admin_methods_keyboard(selected))
        return

    if data.startswith("admin_adv_instant|") and state == UserState.ADMIN_EDIT_ADVERT_INSTANT.name:
        val = data.split("|", 1)[1]
        context.user_data["edit_adv_instant"] = None if val == "__clear__" else val
        await query.edit_message_reply_markup(reply_markup=_admin_instant_keyboard(context.user_data.get("edit_adv_instant")))
        return

    if data == "admin_adv_edit_done" and state in {
        UserState.ADMIN_EDIT_ADVERT_METHODS.name,
        UserState.ADMIN_EDIT_ADVERT_INSTANT.name,
    }:
        advert_id = context.user_data.get("edit_advert_id")
        field = context.user_data.get("edit_advert_field")
        if not isinstance(advert_id, int) or field not in {"methods", "instant_transfer"}:
            try:
                await query.message.delete()
            except Exception:
                pass
            context.user_data["state"] = UserState.ADMIN_MENU.name
            await context.bot.send_message(chat_id=query.message.chat_id, text="❌ خطا در وضعیت.")
            return

        if field == "methods":
            selected = list(dict.fromkeys(context.user_data.get("edit_adv_methods") or []))
            if EXCHANGE_OPTION in selected:
                value = EXCHANGE_OPTION
            else:
                value = ", ".join(selected)
        else:
            value = context.user_data.get("edit_adv_instant")

        if field == "methods" and value == EXCHANGE_OPTION:
            with get_db() as conn:
                cur = conn.cursor()
                op_row = cur.execute("SELECT operation FROM euro_adverts WHERE rowid = ?", (advert_id,)).fetchone()
                if not op_row or op_row[0] not in ("خرید", "فروش"):
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                    context.user_data["state"] = UserState.ADMIN_MENU.name
                    context.user_data.pop("edit_adv_methods", None)
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="❌ معاوضه Euro به Euro فقط برای آگهی «خرید یورو» یا «فروش یورو» قابل انتخاب است.",
                    )
                    return
                cur.execute(
                    """
                    UPDATE euro_adverts
                    SET methods = ?, rate_toman = 0, euro_exchange = 1
                    WHERE rowid = ?
                    """,
                    (value, advert_id),
                )
                row0 = cur.execute(
                    """
                    SELECT rowid, user_id, full_name, euro_amount, rate_toman, description, methods, operation,
                           city_ir, city_int, account_country, instant_transfer, channel_chat_id, channel_message_id,
                           COALESCE(euro_exchange, 0)
                    FROM euro_adverts WHERE rowid = ?
                    """,
                    (advert_id,),
                ).fetchone()
            try:
                if row0 and row0[13]:
                    adv0 = {
                        "rowid": row0[0],
                        "user_id": row0[1],
                        "full_name": row0[2],
                        "euro_amount": int(row0[3]) if row0[3] is not None and str(row0[3]).isdigit() else row0[3],
                        "rate_toman": int(row0[4]) if row0[4] is not None and str(row0[4]).isdigit() else row0[4],
                        "description": row0[5],
                        "methods": row0[6],
                        "operation": row0[7],
                        "city_ir": row0[8],
                        "city_int": row0[9],
                        "account_country": row0[10],
                        "instant_transfer": row0[11],
                        "channel_chat_id": row0[12],
                        "channel_message_id": row0[13],
                        "euro_exchange": row0[14],
                        "bot_username": (await context.bot.get_me()).username,
                    }
                    await refresh_advert_channel_post(context.bot, int(advert_id))
            except Exception:
                pass
            try:
                await query.message.delete()
            except Exception:
                pass
            context.user_data.pop("edit_adv_methods", None)
            context.user_data.pop("edit_adv_instant", None)
            context.user_data["state"] = UserState.ADMIN_EXCH_EDIT_FLOW.name
            context.user_data["admin_exch"] = {"advert_id": advert_id, "side": op_row[0]}
            await _send_admin_exch_delivery_prompt(context, query.message.chat_id)
            return

        with get_db() as conn:
            cur = conn.cursor()
            if field == "methods":
                cur.execute(
                    """
                    UPDATE euro_adverts
                    SET methods = ?, euro_exchange = 0,
                        operation = CASE WHEN operation = 'معاوضه' THEN 'فروش' ELSE operation END
                    WHERE rowid = ?
                    """,
                    (value, advert_id),
                )
            else:
                cur.execute(f"UPDATE euro_adverts SET {field} = ? WHERE rowid = ?", (value, advert_id))
            row = cur.execute(
                """
                SELECT rowid, user_id, full_name, euro_amount, rate_toman, description, methods, operation,
                       city_ir, city_int, account_country, instant_transfer, channel_chat_id, channel_message_id,
                       COALESCE(euro_exchange, 0)
                FROM euro_adverts
                WHERE rowid = ?
                """,
                (advert_id,),
            ).fetchone()

        try:
            await query.message.delete()
        except Exception:
            pass

        context.user_data.pop("edit_adv_methods", None)
        context.user_data.pop("edit_adv_instant", None)

        if not row:
            context.user_data["state"] = UserState.ADMIN_MENU.name
            await context.bot.send_message(chat_id=query.message.chat_id, text="ℹ️ آگهی پیدا نشد.")
            return

        op = row[7]
        euro_ex = int(row[14] or 0)
        try:
            rt_i = int(row[4]) if row[4] is not None and str(row[4]).strip() != "" else 0
        except (TypeError, ValueError):
            rt_i = 0

        if field == "methods" and op != "معاوضه" and euro_ex == 0 and rt_i <= 0:
            context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_RATE.name
            context.user_data["edit_advert_id"] = advert_id
            sent = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    "💰 لطفاً نرخ را به تومان وارد کنید (فقط عدد، مثال: 200000).\n\n"
                    "برای خروج دکمهٔ انصراف را بزنید."
                ),
                reply_markup=_inline_cancel(),
            )
            remember_cleanup_id(
                user_data_store, query.from_user.id, sent.message_id, _ADMIN_CLEANUP_KEY
            )
            return

        context.user_data["state"] = UserState.ADMIN_MENU.name

        advert = {
            "rowid": row[0],
            "user_id": row[1],
            "full_name": row[2],
            "euro_amount": int(row[3]) if row[3] is not None and str(row[3]).isdigit() else row[3],
            "rate_toman": int(row[4]) if row[4] is not None and str(row[4]).isdigit() else row[4],
            "description": row[5],
            "methods": row[6],
            "operation": row[7],
            "city_ir": row[8],
            "city_int": row[9],
            "account_country": row[10],
            "instant_transfer": row[11],
            "channel_chat_id": row[12],
            "channel_message_id": row[13],
            "euro_exchange": row[14],
            "bot_username": (await context.bot.get_me()).username,
        }

        updated = False
        note = ""
        try:
            if advert.get("channel_message_id"):
                await refresh_advert_channel_post(context.bot, int(advert["rowid"]))
                updated = True
            else:
                note = " (این آگهی قبل از آپدیت ساخته شده و پیام کانال در دیتابیس ذخیره نشده.)"
        except Exception:
            updated = False

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="✅ آگهی ویرایش شد."
            + (" (کانال هم آپدیت شد.)" if updated else " (آپدیت کانال ناموفق بود.)")
            + note,
        )
        return


async def _send_admin_exch_delivery_prompt(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    payload = context.user_data.get("admin_exch") or {}
    side = payload.get("side") or "فروش"
    aid = int(payload["advert_id"])
    advert_ex = get_euro_advert_by_rowid(aid)
    if advert_ex and _is_hybrid_euro_exchange_advert(advert_ex):
        payload["delivery"] = "دریافت حضوری" if side == "خرید" else "تحویل حضوری"
        payload.pop("instant", None)
        payload["step"] = "country"
        context.user_data["admin_exch"] = payload
        context.user_data["state"] = UserState.ADMIN_EXCH_EDIT_FLOW.name
        await context.bot.send_message(
            chat_id=chat_id,
            text="🌍 لطفا کشور صاحب حساب خارج از ایران را وارد کنید:",
            reply_markup=_inline_cancel(),
        )
        return
    if side == "خرید":
        message_text = "📥 لطفاً روش دریافت یورو را انتخاب کنید:"
        can_l = "امکان دریافت به حساب دارم"
        no_l = "امکان دریافت حضوری دارم (دریافت حضوری)"
    else:
        message_text = "📤 لطفاً روش تحویل یورو را انتخاب کنید:"
        can_l = "امکان واریز دارم"
        no_l = "امکان واریز ندارم (تحویل حضوری)"
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(can_l, callback_data=f"admin_xd|{aid}|can")],
            [InlineKeyboardButton(no_l, callback_data=f"admin_xd|{aid}|no")],
            [InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")],
        ]
    )
    await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=kb)


async def _send_admin_exch_instant_prompt(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    aid = int((context.user_data.get("admin_exch") or {})["advert_id"])
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ندارم", callback_data=f"admin_xd|{aid}|inst_dont"),
                InlineKeyboardButton("دارم", callback_data=f"admin_xd|{aid}|inst_have"),
                InlineKeyboardButton("اطلاعی ندارم", callback_data=f"admin_xd|{aid}|inst_unk"),
            ],
            [InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")],
        ]
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text="🏦 آیا امکان واریز آنی را دارید:",
        reply_markup=kb,
    )


async def admin_exchange_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.message:
        return
    try:
        await query.answer()
    except Exception:
        pass
    if not _is_admin(query.from_user.id):
        return

    parts = (query.data or "").split("|")
    if len(parts) != 3 or parts[0] != "admin_xd":
        return
    try:
        advert_id = int(parts[1])
    except ValueError:
        return
    action = parts[2]

    payload = context.user_data.get("admin_exch") or {}
    if payload.get("advert_id") != advert_id:
        await query.answer("این مرحله منقضی شده. دوباره ویرایش آگهی را شروع کنید.", show_alert=True)
        return

    side = payload.get("side")
    chat_id = query.message.chat_id

    if action in ("can", "no"):
        try:
            await query.message.delete()
        except Exception:
            pass
        if action == "can":
            if side == "خرید":
                payload["delivery"] = "امکان دریافت به حساب دارم"
                payload.pop("instant", None)
                payload["step"] = "country"
                context.user_data["admin_exch"] = payload
                context.user_data["state"] = UserState.ADMIN_EXCH_EDIT_FLOW.name
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🌍 لطفا کشور صاحب حساب خارج از ایران را وارد کنید:",
                    reply_markup=_inline_cancel(),
                )
            else:
                payload["delivery"] = "امکان واریز به حساب دارم"
                context.user_data["admin_exch"] = payload
                await _send_admin_exch_instant_prompt(context, chat_id)
        else:
            payload["delivery"] = "دریافت حضوری" if side == "خرید" else "تحویل حضوری"
            payload.pop("instant", None)
            payload["step"] = "country"
            context.user_data["admin_exch"] = payload
            context.user_data["state"] = UserState.ADMIN_EXCH_EDIT_FLOW.name
            await context.bot.send_message(
                chat_id=chat_id,
                text="🌍 لطفا کشور صاحب حساب خارج از ایران را وارد کنید:",
                reply_markup=_inline_cancel(),
            )
        return

    if action in ("inst_have", "inst_dont", "inst_unk"):
        try:
            await query.message.delete()
        except Exception:
            pass
        val = {"inst_have": "دارم", "inst_dont": "ندارم", "inst_unk": "اطلاعی ندارم"}.get(action)
        if val is None:
            return
        payload["instant"] = val
        payload["step"] = "country"
        context.user_data["admin_exch"] = payload
        context.user_data["state"] = UserState.ADMIN_EXCH_EDIT_FLOW.name
        await context.bot.send_message(
            chat_id=chat_id,
            text="🌍 لطفا کشور صاحب حساب خارج از ایران را وارد کنید:",
            reply_markup=_inline_cancel(),
        )


def _fmt_user_block(row, idx: int | None = None) -> str:
    tg_id, username, display_name, fn, ln, phone, email, address = row
    # Force correct direction: Persian RTL + keep identifiers LTR.
    RLM = "\u200f"
    LRM = "\u200e"
    uname = f"{LRM}@{username}" if username else "—"
    adv = display_name or "—"
    name = f"{fn or ''} {ln or ''}".strip() or "—"
    is_admin_flag = "✅" if _is_admin(int(tg_id)) else "—"
    prefix = f"{RLM}{idx}) " if isinstance(idx, int) else ""
    return (
        f"{prefix}{RLM}👤 <b>{adv}</b>\n"
        f"{RLM}🧾 <b>نام/نام‌خانوادگی:</b> {name}\n"
        f"{RLM}🔗 <b>یوزرنیم:</b> <code>{uname}</code>\n"
        f"{RLM}🆔 <b>آیدی تلگرام:</b> <code>{LRM}{tg_id}</code>\n"
        f"{RLM}📱 <b>شماره:</b> <code>{LRM}{phone or '—'}</code>\n"
        f"{RLM}📧 <b>ایمیل:</b> <code>{LRM}{email or '—'}</code>\n"
        f"{RLM}🏠 <b>آدرس:</b> <code>{LRM}{address or '—'}</code>\n"
        f"{RLM}🛡️ <b>ادمین:</b> {is_admin_flag}\n"
        f"{RLM}💬 <b>پیام دادن:</b> <a href=\"tg://user?id={tg_id}\">ارسال پیام</a>\n"
    )


_ADMIN_CLEANUP_KEY = "admin_cleanup_message_ids"
_ADMIN_USER_INPUT_KEY = "admin_cleanup_user_inputs"
_ADMIN_EDIT_MAX = 3900
# اگر context.user_data بعد از ری‌استارت یا clear بدون state ماند، ویزارد ادمین از settings بازیابی می‌شود.
_ADMIN_PENDING_KEY = "admin_pending"

_ADMIN_WIZARD_STATE_NAMES = frozenset(
    {
        UserState.ADMIN_SEARCH_USER.name,
        UserState.ADMIN_SEARCH_ADVERT.name,
        UserState.ADMIN_NEG_VIEW_ADVERT.name,
        UserState.ADMIN_EDIT_ADVERT_ID.name,
        UserState.ADMIN_EDIT_ADVERT_FIELD.name,
        UserState.ADMIN_EDIT_ADVERT_VALUE.name,
        UserState.ADMIN_EDIT_ADVERT_METHODS.name,
        UserState.ADMIN_EDIT_ADVERT_INSTANT.name,
        UserState.ADMIN_EDIT_ADVERT_RATE.name,
        UserState.ADMIN_EXCH_EDIT_FLOW.name,
        UserState.ADMIN_DELETE_ADVERT_ID.name,
        UserState.ADMIN_DELETE_ADVERT_CONFIRM.name,
        UserState.ADMIN_ADD_ADVERT.name,
        UserState.ADMIN_DELETE_USER_ID.name,
        UserState.ADMIN_DELETE_CONFIRM.name,
        UserState.ADMIN_EDIT_USER_ID.name,
        UserState.ADMIN_EDIT_FIELD.name,
        UserState.ADMIN_EDIT_VALUE.name,
        UserState.ADMIN_EDIT_PHONE_VERIFY.name,
        UserState.ADMIN_ADD_USER_ID.name,
        UserState.ADMIN_ADD_USER_FIELD.name,
        UserState.ADMIN_RESTRICT_USER_ID.name,
        _ADMIN_RESTRICT_DAYS_STATE_NAME,
        UserState.ADMIN_MANAGE_OFFER_ADVERT.name,
        UserState.ADMIN_MANAGE_OFFER_SEQ.name,
        UserState.ADMIN_MANAGE_OFFER_CMD.name,
        UserState.ADMIN_MANAGE_OFFER_RATE_INPUT.name,
        UserState.ADMIN_MANAGE_OFFER_EURO_INPUT.name,
        UserState.ADMIN_FEE_ADVERT_ID.name,
        UserState.ADMIN_FEE_VALUE.name,
        UserState.ADMIN_PROXY_OFFER_ADVERT.name,
        UserState.ADMIN_PROXY_OFFER_NAME.name,
        UserState.ADMIN_PROXY_OFFER_RATE.name,
        UserState.ADMIN_PROXY_OFFER_DESC.name,
    }
)

# نسخهٔ قدیمی DB (قبل از یکدست‌سازی با UserState.name)
_ADMIN_PENDING_LEGACY = {
    "search_user": UserState.ADMIN_SEARCH_USER.name,
    "search_advert": UserState.ADMIN_SEARCH_ADVERT.name,
    "neg_view_advert": UserState.ADMIN_NEG_VIEW_ADVERT.name,
    # دیتابیس/نسخهٔ قدیمی enum روی سرور
    "ADMIN_RESTRICT_LEVEL": _ADMIN_RESTRICT_DAYS_STATE_NAME,
}


def _normalize_admin_pending(raw: str | None) -> str | None:
    if not raw:
        return None
    r = str(raw).strip()
    if r in _ADMIN_WIZARD_STATE_NAMES:
        return r
    return _ADMIN_PENDING_LEGACY.get(r)


def _admin_pending_db_key(user_id: int) -> str:
    return f"admin_pending_{int(user_id)}"


def _clear_admin_pending(user_id: int) -> None:
    user_data_store.setdefault(user_id, {}).pop(_ADMIN_PENDING_KEY, None)
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM settings WHERE key = ?", (_admin_pending_db_key(user_id),))
    except Exception:
        pass


def _set_admin_pending(user_id: int, kind: str | None) -> None:
    if kind is None:
        _clear_admin_pending(user_id)
        return
    norm = _normalize_admin_pending(kind)
    if not norm or norm not in _ADMIN_WIZARD_STATE_NAMES:
        _clear_admin_pending(user_id)
        return
    user_data_store.setdefault(user_id, {})[_ADMIN_PENDING_KEY] = norm
    try:
        set_setting(_admin_pending_db_key(user_id), norm)
    except Exception:
        pass


def _get_admin_pending(user_id: int) -> str | None:
    mem = (user_data_store.get(user_id) or {}).get(_ADMIN_PENDING_KEY)
    n = _normalize_admin_pending(mem if isinstance(mem, str) else None)
    if n:
        return n
    try:
        row = get_setting(_admin_pending_db_key(user_id))
    except Exception:
        row = None
    n = _normalize_admin_pending(row if isinstance(row, str) else None)
    if n:
        user_data_store.setdefault(user_id, {})[_ADMIN_PENDING_KEY] = n
    return n


def _recover_admin_wizard_state(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        pending = _get_admin_pending(user_id)
        if not pending:
            return
        cur = context.user_data.get("state")
        if cur != pending:
            context.user_data["state"] = pending
    except Exception:
        pass


def _persist_admin_wizard_state(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        st = context.user_data.get("state")
        if st in _ADMIN_WIZARD_STATE_NAMES:
            _set_admin_pending(user_id, st)
        else:
            _clear_admin_pending(user_id)
    except Exception:
        pass


async def _try_restore_admin_dashboard(context: ContextTypes.DEFAULT_TYPE, bot) -> None:
    cid = context.user_data.get("adm_dash_cid")
    mid = context.user_data.get("adm_dash_mid")
    if cid is None or mid is None:
        return
    kb = admin_home_inline_keyboard()
    try:
        await bot.edit_message_text(
            chat_id=int(cid),
            message_id=int(mid),
            text=texts.ADMIN_WELCOME,
            reply_markup=kb,
        )
    except Exception:
        try:
            await bot.edit_message_reply_markup(
                chat_id=int(cid),
                message_id=int(mid),
                reply_markup=kb,
            )
        except Exception:
            pass


async def _admin_edit_dashboard(
    context: ContextTypes.DEFAULT_TYPE,
    bot,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str | None = None,
) -> bool:
    cid = context.user_data.get("adm_dash_cid")
    mid = context.user_data.get("adm_dash_mid")
    if cid is None or mid is None:
        return False
    try:
        kw: dict = {
            "chat_id": int(cid),
            "message_id": int(mid),
            "text": text[:_ADMIN_EDIT_MAX],
            "reply_markup": reply_markup if reply_markup is not None else admin_panel_back_keyboard(),
        }
        if parse_mode:
            kw["parse_mode"] = parse_mode
        await bot.edit_message_text(**kw)
        return True
    except Exception:
        return False


async def _admin_reply(update: Update, text: str, **kwargs):
    """مثل reply_text؛ با state منوی ادمین، داشبورد قبلی را اگر هست به‌روز می‌کند و روی همین پاسخ دکمهٔ منو می‌گذارد."""
    ctx = kwargs.pop("context", None)
    if (
        ctx is not None
        and kwargs.get("reply_markup") is None
        and ctx.user_data.get("state") == UserState.ADMIN_MENU.name
    ):
        await _try_restore_admin_dashboard(ctx, ctx.bot)
        kwargs["reply_markup"] = admin_home_inline_keyboard()
    m = update.message
    sent = await m.reply_text(text, **kwargs)
    remember_cleanup_id(user_data_store, update.effective_user.id, sent.message_id, _ADMIN_CLEANUP_KEY)
    return sent


_ADMIN_OFFER_WIZ_MIDS_KEY = "admin_offer_wizard_mids"


def _admin_offer_wiz_note(context: ContextTypes.DEFAULT_TYPE, *mids: int | None) -> None:
    lst = context.user_data.setdefault(_ADMIN_OFFER_WIZ_MIDS_KEY, [])
    for x in mids:
        if x is None:
            continue
        try:
            lst.append(int(x))
        except (TypeError, ValueError):
            pass


async def _admin_offer_wiz_purge(bot, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    for mid in context.user_data.pop(_ADMIN_OFFER_WIZ_MIDS_KEY, []) or []:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(mid))
        except Exception:
            pass


async def _admin_persist_fee_override_eur(
    context: ContextTypes.DEFAULT_TYPE, advert_id: int, sql_val: float | None
) -> tuple[bool, str]:
    """به‌روزرسانی fee_override_eur (کارمزد یورو برای هر طرف) و رفرش کانال؛ برمی‌گرداند (کانال_آپدیت_شد، یادداشت)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE euro_adverts SET fee_override_eur = ? WHERE rowid = ?",
            (sql_val, advert_id),
        )
        conn.commit()
    note = ""
    updated = False
    try:
        adv = get_euro_advert_by_rowid(advert_id)
        if adv and adv.get("channel_message_id"):
            await refresh_advert_channel_post(context.bot, int(advert_id))
            updated = True
        else:
            note = " (این آگهی قبل از آپدیت ساخته شده و پیام کانال در دیتابیس ذخیره نشده.)"
    except Exception:
        pass
    return updated, note


async def _best_effort_remove_keyboard(update: Update) -> None:
    """Remove reply keyboard without leaving visible bubble (best effort)."""
    try:
        m = await update.effective_message.reply_text("\u2063", reply_markup=ReplyKeyboardRemove())
        try:
            await m.delete()
        except Exception:
            pass
    except Exception:
        pass


async def _admin_strip_reply_keyboard(bot, chat_id: int) -> None:
    """حذف کیبورد ریپلای قدیمی (مثلاً بعد از آپدیت قبلی)."""
    try:
        m = await bot.send_message(chat_id=chat_id, text="\u2063", reply_markup=ReplyKeyboardRemove())
        await m.delete()
    except Exception:
        pass


async def _ensure_username(context: ContextTypes.DEFAULT_TYPE, tg_id: int, current: str | None) -> str | None:
    """Try to fetch missing username from Telegram if possible and persist."""
    if current:
        return current
    try:
        chat = await context.bot.get_chat(tg_id)
        uname = getattr(chat, "username", None)
        if uname:
            update_user_field(tg_id, "username", uname)
        return uname
    except Exception:
        return None


async def _broadcast_main_menu_before_service_restart(bot) -> None:
    """
    به هر کاربر ثبت‌نام‌شده یک منوی اصلی تازه می‌فرستد.
    تلگرام اجازهٔ حذف خودکار «کل تاریخچهٔ چت» را به ربات نمی‌دهد؛ این کار فقط یک نقطهٔ تازه در انتهای چت است.
    """
    uids = get_all_registered_telegram_ids()
    admin_skip = set(ADMIN_IDS or [])
    restart_note = "🔄 سرویس ربات دوباره راه‌اندازی شد.\n\n🏠 منوی اصلی:"
    for uid in uids:
        if uid in admin_skip:
            continue
        try:
            block = get_restriction_block_message(uid)
            if block:
                await send_or_replace_main_menu(
                    bot,
                    chat_id=uid,
                    user_id=uid,
                    store=user_data_store,
                    text=block,
                )
            else:
                await send_or_replace_main_menu(
                    bot,
                    chat_id=uid,
                    user_id=uid,
                    store=user_data_store,
                    text=restart_note,
                )
        except Exception:
            pass
        await asyncio.sleep(0.05)


def _schedule_host_service_restart(bot, shell_command: str) -> None:
    """پس از اعلان اختیاری به کاربران، فرمان ری‌استارت روی سرور (پروسهٔ ربات ممکن است قطع شود)."""

    async def _delayed() -> None:
        if BOT_RESTART_BROADCAST_MENU:
            try:
                await _broadcast_main_menu_before_service_restart(bot)
            except Exception:
                pass
        await asyncio.sleep(2.0)
        try:
            proc = await asyncio.create_subprocess_shell(
                shell_command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=120)
        except Exception:
            pass

    asyncio.create_task(_delayed())


async def _admin_cleanup_switch(context: ContextTypes.DEFAULT_TYPE, admin_uid: int, chat_id: int) -> None:
    ids = user_data_store.setdefault(admin_uid, {}).pop(_ADMIN_CLEANUP_KEY, [])
    await cleanup_ids(context.bot, chat_id=chat_id, ids=ids)
    ids_u = user_data_store.setdefault(admin_uid, {}).pop(_ADMIN_USER_INPUT_KEY, [])
    await cleanup_ids(context.bot, chat_id=chat_id, ids=ids_u)
    _admin_reset_subflow_keys(context)


async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _is_admin(user_id):
        chat_id = update.effective_chat.id
        try:
            if update.message:
                await update.message.delete()
        except Exception:
            pass
        sent_denied = await context.bot.send_message(
            chat_id=chat_id,
            text="⛔️ شما دسترسی ادمین ندارید.",
        )

        async def _delete_denial_later() -> None:
            await asyncio.sleep(6.0)
            try:
                await context.bot.delete_message(chat_id=sent_denied.chat_id, message_id=sent_denied.message_id)
            except Exception:
                pass

        asyncio.create_task(_delete_denial_later())
        return sent_denied

    old_cid = context.user_data.get("adm_dash_cid")
    old_mid = context.user_data.get("adm_dash_mid")
    chat_id = update.effective_chat.id
    await _admin_cleanup_switch(context, user_id, chat_id)
    context.user_data.clear()
    context.user_data["state"] = UserState.ADMIN_MENU.name
    try:
        _persist_admin_wizard_state(user_id, context)
    except Exception:
        pass
    if old_cid is not None and old_mid is not None:
        try:
            await context.bot.delete_message(chat_id=int(old_cid), message_id=int(old_mid))
        except Exception:
            pass
    await _best_effort_remove_keyboard(update)
    sent = await update.effective_message.reply_text(
        texts.ADMIN_WELCOME,
        reply_markup=admin_home_inline_keyboard(),
    )
    context.user_data["adm_dash_cid"] = sent.chat_id
    context.user_data["adm_dash_mid"] = sent.message_id
    try:
        if update.message:
            await update.message.delete()
    except Exception:
        pass
    return sent


async def admin_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.message:
        return
    try:
        await query.answer()
    except Exception:
        pass
    admin_uid = query.from_user.id
    if not _is_admin(admin_uid):
        return

    data = query.data or ""
    if not data.startswith("adm|"):
        return

    parts = data.split("|")
    action = parts[1] if len(parts) > 1 else ""
    chat_id = query.message.chat_id
    context.user_data["adm_dash_cid"] = chat_id
    context.user_data["adm_dash_mid"] = query.message.message_id

    if action == "panel":
        await _admin_cleanup_switch(context, admin_uid, chat_id)
        context.user_data["state"] = UserState.ADMIN_MENU.name
        _persist_admin_wizard_state(admin_uid, context)
        await _try_restore_admin_dashboard(context, context.bot)
        return

    if action == "exit":
        await _admin_cleanup_switch(context, admin_uid, chat_id)
        await _admin_strip_reply_keyboard(context.bot, chat_id)
        dc = context.user_data.get("adm_dash_cid")
        dm = context.user_data.get("adm_dash_mid")
        context.user_data.clear()
        context.user_data["state"] = UserState.MAIN_MENU.name
        _persist_admin_wizard_state(admin_uid, context)
        try:
            if dc is not None and dm is not None:
                await context.bot.delete_message(chat_id=int(dc), message_id=int(dm))
        except Exception:
            pass
        await send_or_replace_main_menu(
            context.bot,
            chat_id=chat_id,
            user_id=admin_uid,
            store=user_data_store,
        )
        return

    _OFFER_INLINE_ACTIONS = frozenset(
        {"ofpick", "ofdel", "ofrate", "ofeur", "oflist"}
    )
    if action in {"rxclr", "rxgo"} or action in _OFFER_INLINE_ACTIONS:
        pass
    else:
        await _admin_cleanup_switch(context, admin_uid, chat_id)
        context.user_data["adm_dash_cid"] = chat_id
        context.user_data["adm_dash_mid"] = query.message.message_id

    if action == "ofpick" and len(parts) > 2:
        try:
            oid = int(parts[2])
        except (TypeError, ValueError):
            await query.answer("شناسه نامعتبر", show_alert=True)
            return
        row = get_advert_offer_joined(oid)
        if not row:
            await query.answer("پیشنهاد پیدا نشد", show_alert=True)
            return
        await _admin_offer_show_detail_page(
            update, context, aid=int(row["advert_rowid"]), oid=oid
        )
        return

    if action == "oflist" and len(parts) > 2:
        try:
            aid = int(parts[2])
        except (TypeError, ValueError):
            await query.answer("شماره آگهی نامعتبر", show_alert=True)
            return
        advert = get_euro_advert_by_rowid(aid)
        if not advert:
            await query.answer("آگهی پیدا نشد", show_alert=True)
            return
        await _admin_offer_show_pick_page(update, context, aid=aid, advert=advert)
        return

    if action == "ofdel" and len(parts) > 2:
        try:
            oid = int(parts[2])
        except (TypeError, ValueError):
            await query.answer("شناسه نامعتبر", show_alert=True)
            return
        row = get_advert_offer_joined(oid)
        if not row:
            await query.answer("پیشنهاد پیدا نشد", show_alert=True)
            return
        aid = int(row["advert_rowid"])
        ok = await _admin_offer_execute_delete(context, oid=oid, aid=aid)
        if not ok:
            await query.answer("حذف نشد", show_alert=True)
            return
        await query.answer("پیشنهاد حذف شد")
        advert = get_euro_advert_by_rowid(aid)
        if advert:
            await _admin_offer_show_pick_page(update, context, aid=aid, advert=advert)
        return

    if action == "ofrate" and len(parts) > 2:
        try:
            oid = int(parts[2])
        except (TypeError, ValueError):
            await query.answer("شناسه نامعتبر", show_alert=True)
            return
        row = get_advert_offer_joined(oid)
        if not row:
            await query.answer("پیشنهاد پیدا نشد", show_alert=True)
            return
        aid = int(row["advert_rowid"])
        seq = int(row.get("seq_in_advert") or oid)
        context.user_data["admin_offer_advert"] = aid
        context.user_data["admin_offer_db_id"] = oid
        context.user_data["admin_offer_edit_action"] = "rate"
        context.user_data["state"] = UserState.ADMIN_MANAGE_OFFER_RATE_INPUT.name
        _persist_admin_wizard_state(admin_uid, context)
        try:
            await query.message.edit_text(
                f"{_RTL}💰 <b>ویرایش نرخ</b> — پیشنهاد <b>{seq}</b> آگهی <b>{aid}</b>\n\n"
                f"{_RTL}نرخ جدید را به <b>تومان</b> (فقط عدد) بفرستید:",
                parse_mode=ParseMode.HTML,
                reply_markup=_inline_cancel(),
            )
            _admin_offer_wiz_note(context, query.message.message_id)
        except Exception:
            pass
        return

    if action == "ofeur" and len(parts) > 2:
        try:
            oid = int(parts[2])
        except (TypeError, ValueError):
            await query.answer("شناسه نامعتبر", show_alert=True)
            return
        row = get_advert_offer_joined(oid)
        if not row:
            await query.answer("پیشنهاد پیدا نشد", show_alert=True)
            return
        aid = int(row["advert_rowid"])
        seq = int(row.get("seq_in_advert") or oid)
        context.user_data["admin_offer_advert"] = aid
        context.user_data["admin_offer_db_id"] = oid
        context.user_data["admin_offer_edit_action"] = "euro"
        context.user_data["state"] = UserState.ADMIN_MANAGE_OFFER_EURO_INPUT.name
        _persist_admin_wizard_state(admin_uid, context)
        try:
            await query.message.edit_text(
                f"{_RTL}💶 <b>ویرایش مقدار یورو</b> — پیشنهاد <b>{seq}</b> آگهی <b>{aid}</b>\n\n"
                f"{_RTL}مقدار یوروی پیشنهادی را (فقط عدد) بفرستید:",
                parse_mode=ParseMode.HTML,
                reply_markup=_inline_cancel(),
            )
            _admin_offer_wiz_note(context, query.message.message_id)
        except Exception:
            pass
        return

    if action == "bot0":
        try:
            set_setting("bot_enabled", "0")
        except Exception:
            pass
        try:
            await context.bot.send_message(chat_id=ADVERT_CHANNEL_ID, text="⛔️ ربات غیرفعال شد.")
        except Exception:
            pass
        context.user_data["state"] = UserState.ADMIN_MENU.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(context, context.bot, "⛔️ ربات غیرفعال شد.")
        return

    if action == "bot1":
        try:
            set_setting("bot_enabled", "1")
        except Exception:
            pass
        try:
            await context.bot.send_message(chat_id=ADVERT_CHANNEL_ID, text="✅ ربات فعال شد.")
        except Exception:
            pass
        context.user_data["state"] = UserState.ADMIN_MENU.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(context, context.bot, "✅ ربات فعال شد.")
        return

    if action == "brate":
        from handlers.bonbast_daily import post_bonbast_rates_now

        try:
            await query.answer("در حال دریافت نرخ از bonbast…")
        except Exception:
            pass
        context.user_data["state"] = UserState.ADMIN_MENU.name
        _persist_admin_wizard_state(admin_uid, context)
        try:
            ok = await post_bonbast_rates_now(context.bot)
            if ok:
                msg = "✅ نرخ ارز در کانال منتشر شد."
            else:
                msg = (
                    "❌ کانال برای پست نرخ تنظیم نشده.\n"
                    "در `.env` مقدار `ADVERT_CHANNEL_ID` یا `BONBAST_CHANNEL_ID` را بررسی کنید."
                )
        except Exception as exc:
            msg = f"❌ خطا در دریافت/ارسال نرخ: {exc}"
        await _admin_edit_dashboard(
            context,
            context.bot,
            msg,
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "rsvc":
        cmd = (BOT_RESTART_COMMAND or "").strip()
        if not cmd:
            context.user_data["state"] = UserState.ADMIN_MENU.name
            _persist_admin_wizard_state(admin_uid, context)
            await _admin_edit_dashboard(
                context,
                context.bot,
                "❌ دستور ری‌استارت روی سرور تنظیم نشده.\n"
                "در `.env` مقدار `BOT_RESTART_COMMAND` را بگذارید، مثال:\n"
                "`BOT_RESTART_COMMAND=systemctl restart نام-سرویس`",
                reply_markup=admin_panel_back_keyboard(),
            )
            return
        context.user_data["state"] = UserState.ADMIN_MENU.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            "⏳ حدود ۲ ثانیه دیگر فرمان ری‌استارت روی سرور اجرا می‌شود…",
            reply_markup=admin_panel_back_keyboard(),
        )
        _schedule_host_service_restart(context.bot, cmd)
        return

    if action == "ofm":
        await _admin_cleanup_switch(context, admin_uid, chat_id)
        context.user_data["adm_dash_cid"] = chat_id
        context.user_data["adm_dash_mid"] = query.message.message_id
        context.user_data["state"] = UserState.ADMIN_MANAGE_OFFER_ADVERT.name
        context.user_data.pop("admin_offer_advert", None)
        context.user_data.pop("admin_offer_db_id", None)
        context.user_data["admin_offer_wizard_mids"] = []
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            f"{_RTL}📋 مدیریت پیشنهاد آگهی\n\n{_RTL}1️⃣ شماره آگهی (rowid) را در همین چت بفرستید:",
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "pof":
        await _admin_cleanup_switch(context, admin_uid, chat_id)
        context.user_data["adm_dash_cid"] = chat_id
        context.user_data["adm_dash_mid"] = query.message.message_id
        context.user_data["state"] = UserState.ADMIN_PROXY_OFFER_ADVERT.name
        for k in ("admin_proxy_aid", "admin_proxy_alias", "admin_proxy_rate"):
            context.user_data.pop(k, None)
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            f"{_RTL}🎭 <b>پیشنهاد نمایشی</b> (نام دلخواه برای صاحب آگهی؛ حتی اگر آگهی از قبل پیشنهاد دارد)\n\n"
            f"{_RTL}1️⃣ شماره آگهی (rowid) را در همین چت بفرستید:",
            reply_markup=admin_panel_back_keyboard(),
            parse_mode="HTML",
        )
        return

    if action == "lu":
        context.user_data["state"] = UserState.ADMIN_MENU.name
        _persist_admin_wizard_state(admin_uid, context)
        with get_db() as conn:
            cur = conn.cursor()
            try:
                rows = cur.execute(
                    "SELECT telegram_id, username, display_name, full_name, last_name, phone_number, email, address FROM users ORDER BY rowid DESC LIMIT 30"
                ).fetchall()
            except Exception:
                rows = []
        if not rows:
            await _admin_edit_dashboard(context, context.bot, "ℹ️ کاربری یافت نشد.")
            return
        enriched = []
        for r in rows:
            tg_id = int(r[0])
            uname = await _ensure_username(context, tg_id, r[1])
            enriched.append((r[0], uname, r[2], r[3], r[4], r[5], r[6], r[7]))
        blocks = "\n".join([_fmt_user_block(r, idx=i + 1) for i, r in enumerate(enriched)])
        await _admin_edit_dashboard(
            context,
            context.bot,
            "👥 <b>لیست کاربران</b>\n\n" + blocks,
            reply_markup=admin_panel_back_keyboard(),
            parse_mode="HTML",
        )
        return

    if action == "su":
        context.user_data["state"] = UserState.ADMIN_SEARCH_USER.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            "🔎 عبارت جستجو را وارد کنید (نام نمایشی آگهی / نام / @username / شماره / آیدی):",
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "sad":
        context.user_data["state"] = UserState.ADMIN_SEARCH_ADVERT.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            "🔎 شماره آگهی را وارد کنید:",
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "negv":
        context.user_data["state"] = UserState.ADMIN_NEG_VIEW_ADVERT.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            f"{_RTL}🗣️ شمارهٔ آگهی (<code>rowid</code>) یا مثلاً <code>/neg_ad 74</code>",
            reply_markup=admin_panel_back_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "ea":
        context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_ID.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            "✏️ شماره آگهی را وارد کنید:",
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "da":
        context.user_data["state"] = UserState.ADMIN_DELETE_ADVERT_ID.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            "🗑️ شماره آگهی را وارد کنید:",
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "aa":
        context.user_data["state"] = UserState.ADMIN_ADD_ADVERT.name
        context.user_data["admin_add_ad_step"] = "user_id"
        context.user_data.pop("admin_new_advert_owner_id", None)
        context.user_data.pop("admin_post_advert_for", None)
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            f"{_RTL}🆔 آیدی عددی تلگرام کاربر صاحب آگهی را وارد کنید.\n"
            f"{_RTL}اگر کاربر در ربات ثبت‌نام نکرده، یکی از این‌ها را بفرستید: ۰ ، - ، ندارد",
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "du":
        context.user_data["state"] = UserState.ADMIN_DELETE_USER_ID.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            "🗑️ آیدی عددی کاربر را وارد کنید:",
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "eu":
        context.user_data["state"] = UserState.ADMIN_EDIT_USER_ID.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            "✏️ آیدی عددی کاربر را وارد کنید:",
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "au":
        context.user_data["state"] = UserState.ADMIN_ADD_USER_ID.name
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            "➕ آیدی عددی تلگرام کاربر را وارد کنید:",
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "al":
        context.user_data["state"] = UserState.ADMIN_MENU.name
        _persist_admin_wizard_state(admin_uid, context)
        with get_db() as conn:
            cur = conn.cursor()
            try:
                rows = cur.execute(
                    """
                    SELECT
                        a.rowid,
                        COALESCE(u.display_name, a.full_name) AS adv_name,
                        u.username,
                        a.euro_amount,
                        a.rate_toman,
                        a.operation
                    FROM euro_adverts a
                    LEFT JOIN users u ON u.telegram_id = a.user_id
                    ORDER BY a.rowid DESC
                    LIMIT 15
                    """
                ).fetchall()
            except Exception:
                rows = []
        if not rows:
            await _admin_edit_dashboard(context, context.bot, "ℹ️ آگهی‌ای یافت نشد.")
            return
        lines = []
        for r in rows:
            advert_id, adv_name, uname, amount, rate, op = r
            u_at = f"@{uname}" if uname else "—"
            lines.append(f"- #{advert_id} | {op} | {adv_name} | {u_at} | {amount}€ | {_fmt_thousands(rate)}")
        await _admin_edit_dashboard(
            context,
            context.bot,
            "📢 آخرین آگهی‌ها:\n" + "\n".join(lines),
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "rx":
        context.user_data["state"] = UserState.ADMIN_RESTRICT_USER_ID.name
        context.user_data.pop("restrict_uid", None)
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            f"{_RTL}🆔 آیدی عددی تلگرام کاربر را وارد کنید:",
            reply_markup=admin_panel_back_keyboard(),
        )
        return

    if action == "rxclr":
        if len(parts) < 3:
            return
        try:
            tuid = int(parts[2])
        except ValueError:
            return
        ok = set_user_restriction(tuid, False)
        context.user_data["state"] = UserState.ADMIN_MENU.name
        context.user_data.pop("restrict_uid", None)
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            "✅ محدودیت برداشته شد." if ok else "❌ انجام نشد.",
        )
        return

    if action == "rxgo":
        if len(parts) < 3:
            return
        try:
            tuid = int(parts[2])
        except ValueError:
            return
        if not get_user(tuid):
            await _admin_edit_dashboard(context, context.bot, f"{_RTL}❌ کاربری با این آیدی در ربات ثبت‌نام نکرده است.")
            context.user_data["state"] = UserState.ADMIN_MENU.name
            _persist_admin_wizard_state(admin_uid, context)
            return
        context.user_data["restrict_uid"] = tuid
        context.user_data["state"] = _ADMIN_RESTRICT_DAYS_STATE_NAME
        _persist_admin_wizard_state(admin_uid, context)
        await _admin_edit_dashboard(
            context,
            context.bot,
            f"{_RTL}⏳ تعداد روز محدودیت را وارد کنید.\n{_RTL}۰ = محدودیت دائمی",
            reply_markup=admin_panel_back_keyboard(),
        )
        return


async def admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin menu buttons (reply keyboard)."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not _is_admin(user_id):
        return

    try:
        _recover_admin_wizard_state(user_id, context)
    except Exception:
        pass

    text = (update.message.text or "").strip()
    state = context.user_data.get("state")

    # Only intercept messages when admin panel is active or a menu action is clicked.
    admin_actions = {
        "👥 لیست کاربران",
        "🔎 جستجوی کاربر",
        "🔎 جستجوی آگهی",
        "🗣️ مذاکرات آگهی",
        "➕ ثبت آگهی",
        "✏️ ویرایش آگهی",
        "🗑️ حذف آگهی",
        "⛔️ غیرفعال کردن ربات",
        "✅ فعال کردن ربات",
        "➕ افزودن کاربر",
        "✏️ ویرایش کاربر",
        "🗑️ حذف کاربر",
        "📢 لیست آگهی‌ها",
        "🔒 محدودیت دسترسی کاربر",
        "📋 مدیریت پیشنهاد آگهی",
        "🧾 ویرایش کارمزد آگهی",
        "🏠 بازگشت به منو اصلی",
    }
    # Note: ADMIN_MENU alone must NOT hijack normal user flows.
    admin_states = {
        UserState.ADMIN_SEARCH_USER.name,
        UserState.ADMIN_SEARCH_ADVERT.name,
        UserState.ADMIN_NEG_VIEW_ADVERT.name,
        UserState.ADMIN_EDIT_ADVERT_ID.name,
        UserState.ADMIN_EDIT_ADVERT_FIELD.name,
        UserState.ADMIN_EDIT_ADVERT_VALUE.name,
        UserState.ADMIN_EDIT_ADVERT_METHODS.name,
        UserState.ADMIN_EDIT_ADVERT_INSTANT.name,
        UserState.ADMIN_EDIT_ADVERT_RATE.name,
        UserState.ADMIN_EXCH_EDIT_FLOW.name,
        UserState.ADMIN_DELETE_ADVERT_ID.name,
        UserState.ADMIN_DELETE_ADVERT_CONFIRM.name,
        UserState.ADMIN_ADD_ADVERT.name,
        UserState.ADMIN_DELETE_USER_ID.name,
        UserState.ADMIN_DELETE_CONFIRM.name,
        UserState.ADMIN_EDIT_USER_ID.name,
        UserState.ADMIN_EDIT_FIELD.name,
        UserState.ADMIN_EDIT_VALUE.name,
        UserState.ADMIN_EDIT_PHONE_VERIFY.name,
        UserState.ADMIN_ADD_USER_ID.name,
        UserState.ADMIN_ADD_USER_FIELD.name,
        UserState.ADMIN_RESTRICT_USER_ID.name,
        _ADMIN_RESTRICT_DAYS_STATE_NAME,
        UserState.ADMIN_MANAGE_OFFER_ADVERT.name,
        UserState.ADMIN_MANAGE_OFFER_SEQ.name,
        UserState.ADMIN_MANAGE_OFFER_CMD.name,
        UserState.ADMIN_MANAGE_OFFER_RATE_INPUT.name,
        UserState.ADMIN_MANAGE_OFFER_EURO_INPUT.name,
        UserState.ADMIN_FEE_ADVERT_ID.name,
        UserState.ADMIN_FEE_VALUE.name,
        UserState.ADMIN_PROXY_OFFER_ADVERT.name,
        UserState.ADMIN_PROXY_OFFER_NAME.name,
        UserState.ADMIN_PROXY_OFFER_RATE.name,
        UserState.ADMIN_PROXY_OFFER_DESC.name,
    }
    if state not in admin_states and text not in admin_actions:
        # Let normal (non-admin) flows handle this message.
        return

    try:
        if state in admin_states and update.message:
            remember_cleanup_id(user_data_store, user_id, update.message.message_id, _ADMIN_USER_INPUT_KEY)

        if text == "🏠 بازگشت به منو اصلی":
            _clear_admin_pending(user_id)
            chat_id = update.effective_chat.id
            ids = user_data_store.setdefault(user_id, {}).pop(_ADMIN_CLEANUP_KEY, [])
            await cleanup_ids(context.bot, chat_id=chat_id, ids=ids)
            ids_u = user_data_store.setdefault(user_id, {}).pop(_ADMIN_USER_INPUT_KEY, [])
            await cleanup_ids(context.bot, chat_id=chat_id, ids=ids_u)
            await _admin_strip_reply_keyboard(context.bot, chat_id)
            dc = context.user_data.get("adm_dash_cid")
            dm = context.user_data.get("adm_dash_mid")
            context.user_data.clear()
            context.user_data["state"] = UserState.MAIN_MENU.name
            await _best_effort_remove_keyboard(update)
            try:
                await update.message.delete()
            except Exception:
                pass
            try:
                if dc is not None and dm is not None:
                    await context.bot.delete_message(chat_id=int(dc), message_id=int(dm))
            except Exception:
                pass
            return await send_or_replace_main_menu(
                context.bot,
                chat_id=chat_id,
                user_id=user_id,
                store=user_data_store,
            )

        if text in {"⛔️ غیرفعال کردن ربات", "✅ فعال کردن ربات"}:
            chat_id_toggle = update.effective_chat.id
            ids_toggle = user_data_store.setdefault(user_id, {}).pop(_ADMIN_CLEANUP_KEY, [])
            await cleanup_ids(context.bot, chat_id=chat_id_toggle, ids=ids_toggle)
            ids_ut = user_data_store.setdefault(user_id, {}).pop(_ADMIN_USER_INPUT_KEY, [])
            await cleanup_ids(context.bot, chat_id=chat_id_toggle, ids=ids_ut)
            enabled = (text == "✅ فعال کردن ربات")
            try:
                set_setting("bot_enabled", "1" if enabled else "0")
            except Exception:
                pass
            # Notify channel (best effort)
            try:
                await context.bot.send_message(
                    chat_id=ADVERT_CHANNEL_ID,
                    text=("✅ ربات فعال شد." if enabled else "⛔️ ربات غیرفعال شد."),
                )
            except Exception:
                pass
            return await _admin_reply(update,
                ("✅ ربات فعال شد." if enabled else "⛔️ ربات غیرفعال شد."),
                reply_markup=None, context=context,
            )

        admin_reply_menu = admin_actions - {
            "🏠 بازگشت به منو اصلی",
            "⛔️ غیرفعال کردن ربات",
            "✅ فعال کردن ربات",
        }
        _admin_posting_euro_states = {
            UserState.SERVICE_SELECTION.name,
            UserState.EURO_AMOUNT.name,
            UserState.EURO_RATE.name,
            UserState.EURO_DESCRIPTION.name,
            UserState.EURO_ACCOUNT_COUNTRY.name,
            UserState.EURO_INSTANT_TRANSFER.name,
            UserState.EURO_CONFIRM_ADVERT.name,
            UserState.EXCHANGE_INIT.name,
            UserState.EXCHANGE_INSTANT_TRANSFER.name,
            UserState.EXCHANGE_AMOUNT.name,
            UserState.EXCHANGE_COUNTRY_INT.name,
            UserState.EXCHANGE_CITY_INT.name,
            UserState.EXCHANGE_CITY_IR.name,
            UserState.EXCHANGE_DESCRIPTION.name,
            UserState.EXCHANGE_CONFIRM.name,
        }
        if text in admin_reply_menu:
            chat_id_switch = update.effective_chat.id
            ids_switch = user_data_store.setdefault(user_id, {}).pop(_ADMIN_CLEANUP_KEY, [])
            await cleanup_ids(context.bot, chat_id=chat_id_switch, ids=ids_switch)
            ids_usw = user_data_store.setdefault(user_id, {}).pop(_ADMIN_USER_INPUT_KEY, [])
            await cleanup_ids(context.bot, chat_id=chat_id_switch, ids=ids_usw)
            posting = bool(context.user_data.get("admin_post_advert_for"))
            if state in admin_states:
                _admin_reset_subflow_keys(context)
                user_data_store.pop(user_id, None)
                context.user_data["state"] = UserState.ADMIN_MENU.name
                state = UserState.ADMIN_MENU.name
            elif posting and state in _admin_posting_euro_states:
                user_data_store.pop(user_id, None)
                context.user_data.clear()
                context.user_data["state"] = UserState.ADMIN_MENU.name
                state = UserState.ADMIN_MENU.name

        # ---------- step-based flows ----------
        if state == UserState.ADMIN_ADD_ADVERT.name:
            step = context.user_data.get("admin_add_ad_step", "user_id")
            if step == "user_id":
                raw = (text or "").strip()
                raw_digits = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
                skip_markers = {"0", "-", "ندارد", "بدون", "خالی"}
                if raw in skip_markers or raw.casefold() in {m.casefold() for m in skip_markers} or raw_digits == "0":
                    uid = 0
                else:
                    uid = _parse_int_from_text(text)
                if uid is None:
                    return await _admin_reply(update,
                        f"{_RTL}❌ آیدی عددی معتبر وارد کنید، یا برای آگهی بدون کاربر ربات یکی از این‌ها را بفرستید: ۰ ، - ، ندارد",
                        reply_markup=_inline_cancel(),
                    )
                if uid != 0 and not get_user(uid):
                    return await _admin_reply(update,
                        f"{_RTL}❌ کاربری با این آیدی در ربات ثبت‌نام نکرده است.\n"
                        f"{_RTL}اگر صاحب آگهی در ربات نیست، «۰» یا «ندارد» بفرستید.",
                        reply_markup=_inline_cancel(),
                    )
                context.user_data["admin_new_advert_owner_id"] = uid
                context.user_data["admin_add_ad_step"] = "display_name"
                return await _admin_reply(update,
                    f"{_RTL}🏷️ نام ظاهرشونده در آگهی را وارد کنید:\n"
                    f"{_RTL}(این نام در متن آگهی کانال دیده می‌شود)",
                    reply_markup=_inline_cancel(),
                )
            if step == "display_name":
                dn = text.strip()
                if not dn:
                    return await _admin_reply(update,f"{_RTL}❌ نام را وارد کنید:", reply_markup=_inline_cancel())
                owner = context.user_data.get("admin_new_advert_owner_id")
                if not isinstance(owner, int):
                    context.user_data["state"] = UserState.ADMIN_MENU.name
                    context.user_data.pop("admin_add_ad_step", None)
                    return await _admin_reply(update,
                        f"{_RTL}❌ خطا در فلو. دوباره از «ثبت آگهی» شروع کنید.",
                        reply_markup=None, context=context,
                    )
                context.user_data["admin_post_advert_for"] = {"user_id": owner, "display_name": dn}
                context.user_data.pop("admin_new_advert_owner_id", None)
                context.user_data.pop("admin_add_ad_step", None)
                context.user_data["state"] = UserState.SERVICE_SELECTION.name
                user_data_store.setdefault(user_id, {"methods": [], "operation": ""})
                user_data_store[user_id]["methods"] = []
                user_data_store[user_id]["operation"] = ""
                return await _admin_reply(update,
                    f"{_RTL}لطفا عملیات مورد نظر را انتخاب کنید:",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton("🟢 خرید یورو", callback_data="service_op_buy"),
                                InlineKeyboardButton("🔴 فروش یورو", callback_data="service_op_sell"),
                            ],
                            [InlineKeyboardButton("❌ انصراف", callback_data="inline_cancel")],
                        ],
                    ),
                )

        if state == UserState.ADMIN_EXCH_EDIT_FLOW.name:
            payload = context.user_data.get("admin_exch") or {}
            step = payload.get("step")
            advert_id = payload.get("advert_id")
            side = payload.get("side")
            delivery = payload.get("delivery")
            if not isinstance(advert_id, int) or not step or side not in ("خرید", "فروش"):
                context.user_data["state"] = UserState.ADMIN_MENU.name
                context.user_data.pop("admin_exch", None)
                return await _admin_reply(update,"❌ خطا در فلو معاوضه. دوباره از ویرایش آگهی شروع کنید.", reply_markup=None, context=context)

            in_person = "دریافت حضوری" if side == "خرید" else "تحویل حضوری"

            if step == "country":
                payload["account_country"] = text.strip()
                if delivery == in_person:
                    payload["step"] = "city_int"
                    context.user_data["admin_exch"] = payload
                    return await _admin_reply(update,
                        "🌍 لطفا نام شهر خارج از ایران را وارد کنید:",
                        reply_markup=_inline_cancel(),
                    )
                payload["city_int"] = "—"
                payload["step"] = "city_ir"
                context.user_data["admin_exch"] = payload
                return await _admin_reply(update,
                    "🏙️ لطفا نام شهر داخل ایران را وارد کنید:",
                    reply_markup=_inline_cancel(),
                )

            if step == "city_int":
                payload["city_int"] = text.strip()
                payload["step"] = "city_ir"
                context.user_data["admin_exch"] = payload
                return await _admin_reply(update,
                    "🏙️ لطفا نام شهر داخل ایران را وارد کنید:",
                    reply_markup=_inline_cancel(),
                )

            if step == "city_ir":
                payload["city_ir"] = text.strip()
                payload["step"] = "desc"
                context.user_data["admin_exch"] = payload
                return await _admin_reply(update,
                    "📝 لطفا توضیحات را وارد کنید. اگر ندارید بنویسید: ندارم",
                    reply_markup=_inline_cancel(),
                )

            if step == "desc":
                desc_val = text.strip()
                delivery_method = payload.get("delivery") or "—"
                country = payload.get("account_country") or "—"
                city_int = payload.get("city_int") or "—"
                city_ir = payload.get("city_ir") or "—"
                inst = payload.get("instant")
                show_instant = side != "خرید" and delivery_method == "امکان واریز به حساب دارم"
                instant_db = inst if show_instant else None

                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        UPDATE euro_adverts SET
                            methods = ?,
                            account_country = ?,
                            city_ir = ?,
                            city_int = ?,
                            instant_transfer = ?,
                            description = ?
                        WHERE rowid = ?
                        """,
                        (delivery_method, country, city_ir, city_int, instant_db, desc_val, advert_id),
                    )
                    row = cur.execute(
                        """
                        SELECT rowid, user_id, full_name, euro_amount, rate_toman, description, methods, operation,
                               city_ir, city_int, account_country, instant_transfer, channel_chat_id, channel_message_id,
                               COALESCE(euro_exchange, 0)
                        FROM euro_adverts
                        WHERE rowid = ?
                        """,
                        (advert_id,),
                    ).fetchone()

                context.user_data["state"] = UserState.ADMIN_MENU.name
                context.user_data.pop("admin_exch", None)

                if not row:
                    return await _admin_reply(update,"ℹ️ آگهی پیدا نشد.", reply_markup=None, context=context)

                advert = {
                    "rowid": row[0],
                    "user_id": row[1],
                    "full_name": row[2],
                    "euro_amount": int(row[3]) if row[3] is not None and str(row[3]).isdigit() else row[3],
                    "rate_toman": int(row[4]) if row[4] is not None and str(row[4]).isdigit() else row[4],
                    "description": row[5],
                    "methods": row[6],
                    "operation": row[7],
                    "city_ir": row[8],
                    "city_int": row[9],
                    "account_country": row[10],
                    "instant_transfer": row[11],
                    "channel_chat_id": row[12],
                    "channel_message_id": row[13],
                    "euro_exchange": row[14],
                    "bot_username": (await context.bot.get_me()).username,
                }

                updated = False
                note = ""
                try:
                    if advert.get("channel_message_id"):
                        await refresh_advert_channel_post(context.bot, int(advert["rowid"]))
                        updated = True
                    else:
                        note = " (این آگهی قبل از آپدیت ساخته شده و پیام کانال در دیتابیس ذخیره نشده.)"
                except Exception:
                    updated = False

                return await _admin_reply(update,
                    "✅ جزئیات معاوضه ذخیره شد و آگهی به‌روز شد."
                    + (" (کانال هم آپدیت شد.)" if updated else " (آپدیت کانال ناموفق بود.)")
                    + note,
                    reply_markup=None, context=context,
                )

        if state == UserState.ADMIN_SEARCH_USER.name:
            rows = search_users(text, limit=20)
            context.user_data["state"] = UserState.ADMIN_MENU.name
            _clear_admin_pending(user_id)
            if not rows:
                return await _admin_reply(update,"ℹ️ نتیجه‌ای پیدا نشد.", reply_markup=None, context=context)
            enriched = []
            for r in rows:
                tg_id = int(r[0])
                uname = await _ensure_username(context, tg_id, r[1])
                rr = (r[0], uname, r[2], r[3], r[4], r[5], r[6], r[7])
                enriched.append(rr)
            blocks = "\n".join([_fmt_user_block(r, idx=i + 1) for i, r in enumerate(enriched)])
            return await _admin_reply(update,
                "🔎 <b>نتایج جستجو</b>\n\n" + blocks,
                parse_mode="HTML",
                reply_markup=None, context=context,
                disable_web_page_preview=True,
            )

        if state == UserState.ADMIN_SEARCH_ADVERT.name:
            advert_id = _parse_int_from_text(text)
            context.user_data["state"] = UserState.ADMIN_MENU.name
            _clear_admin_pending(user_id)
            if advert_id is None:
                return await _admin_reply(update,"❌ شماره آگهی معتبر وارد کنید.", reply_markup=None, context=context)
            with get_db() as conn:
                cur = conn.cursor()
                row = cur.execute(
                    """
                    SELECT
                        a.rowid,
                        a.user_id,
                        a.operation,
                        a.euro_amount,
                        a.rate_toman,
                        a.description,
                        COALESCE(u.display_name, a.full_name) AS adv_name,
                        u.username
                    FROM euro_adverts a
                    LEFT JOIN users u ON u.telegram_id = a.user_id
                    WHERE a.rowid = ?
                    """,
                    (advert_id,),
                ).fetchone()
            if not row:
                return await _admin_reply(update,"ℹ️ آگهی پیدا نشد.", reply_markup=None, context=context)
            aid, uid, op, amount, rate, desc, adv_name, uname = row
            LRM = "\u200e"
            RLM = "\u200f"
            u_at = f"{LRM}@{uname}" if uname else "—"
            msg = (
                f"{RLM}📢 <b>آگهی #{aid}</b>\n"
                f"{RLM}👤 <b>آگهی‌دهنده:</b> {adv_name}\n"
                f"{RLM}🔗 <b>یوزرنیم:</b> <code>{u_at}</code>\n"
                f"{RLM}🆔 <b>آیدی تلگرام:</b> <code>{LRM}{uid}</code>\n"
                f"{RLM}✨ <b>نوع:</b> {op}\n"
                f"{RLM}💶 <b>مقدار:</b> <code>{LRM}{amount}</code>\n"
                f"{RLM}💰 <b>نرخ:</b> <code>{LRM}{_fmt_thousands(rate)}</code>\n"
                f"{RLM}📝 <b>توضیحات:</b> {desc or '—'}\n"
            )
            return await _admin_reply(update,msg, parse_mode="HTML", reply_markup=None, context=context, disable_web_page_preview=True)

        if state == UserState.ADMIN_NEG_VIEW_ADVERT.name:
            advert_id = _parse_int_from_text(text)
            if advert_id is None:
                context.user_data["state"] = UserState.ADMIN_NEG_VIEW_ADVERT.name
                _persist_admin_wizard_state(user_id, context)
                return await _admin_reply(
                    update,
                    "❌ شماره آگهی معتبر وارد کنید.",
                    reply_markup=None,
                    context=context,
                )
            err = await _admin_deliver_negotiation_report(update, context, advert_id)
            context.user_data["state"] = UserState.ADMIN_NEG_VIEW_ADVERT.name
            _persist_admin_wizard_state(user_id, context)
            if err:
                return await _admin_reply(
                    update,
                    err,
                    reply_markup=None,
                    context=context,
                )
            return

        if state == UserState.ADMIN_EDIT_ADVERT_ID.name:
            raw_t = (text or "").strip()
            if raw_t == _ADMIN_KB_CANCEL:
                await _admin_exit_edit_advert_wizard(context, update)
                return
            advert_id = _parse_int_from_text(text)
            if advert_id is None:
                return await _admin_reply(
                    update,
                    "❌ شماره آگهی معتبر وارد کنید (فقط عدد) یا دکمهٔ انصراف را بزنید:",
                    reply_markup=_inline_cancel(),
                )
            context.user_data["edit_advert_id"] = advert_id
            context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_FIELD.name
            adv_pick = get_euro_advert_by_rowid(int(advert_id))
            show_rate = not _offer_skips_toman_rate_step(adv_pick) if adv_pick else True
            return await _admin_reply(
                update,
                f"{_RTL}✏️ کدام فیلد آگهی را ویرایش کنیم؟",
                reply_markup=_admin_edit_advert_fields_inline_kb(show_rate_row=show_rate),
            )

        if state == UserState.ADMIN_EDIT_ADVERT_FIELD.name:
            raw_t = (text or "").strip()
            if raw_t == _ADMIN_KB_CANCEL:
                await _admin_exit_edit_advert_wizard(context, update)
                return
            if raw_t == _ADMIN_KB_BACK:
                await _admin_back_edit_advert_to_id_step(context, update)
                return await _admin_reply(
                    update,
                    f"{_RTL}✏️ شماره آگهی را وارد کنید:\n\n"
                    f"{_RTL}برای خروج دکمهٔ انصراف را بزنید.",
                    reply_markup=_inline_cancel(),
                )
            field_map = {
                "👤 نام آگهی‌دهنده": "full_name",
                "💶 مقدار یورو": "euro_amount",
                "💰 نرخ (تومان)": "rate_toman",
                "📝 توضیحات": "description",
                "💳 روش‌ها": "methods",
                "🌍 کشور (خارج ایران)": "account_country",
                "🏦 واریز آنی": "instant_transfer",
                "🧾 کارمزد (یورو)": "fee_override_eur",
            }
            field = field_map.get(text)
            if not field:
                aid_bad = context.user_data.get("edit_advert_id")
                adv_bad = get_euro_advert_by_rowid(int(aid_bad)) if isinstance(aid_bad, int) else None
                show_bad = not _offer_skips_toman_rate_step(adv_bad) if adv_bad else True
                return await _admin_reply(
                    update,
                    "❌ لطفاً یکی از دکمه‌های اینلاین را بزنید.",
                    reply_markup=_admin_edit_advert_fields_inline_kb(show_rate_row=show_bad),
                )
            context.user_data["edit_advert_field"] = field
            if field == "methods":
                context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_METHODS.name
                await _best_effort_remove_keyboard(update)
                # preload current methods from DB
                with get_db() as conn:
                    cur = conn.cursor()
                    row = cur.execute(
                        "SELECT methods, COALESCE(euro_exchange, 0) FROM euro_adverts WHERE rowid = ?",
                        (context.user_data.get("edit_advert_id"),),
                    ).fetchone()
                if row and int(row[1] or 0) == 1:
                    selected = {EXCHANGE_OPTION}
                else:
                    current = row[0] if row and row[0] else ""
                    parts = [m.strip() for m in current.split(",") if m.strip()]
                    if EXCHANGE_OPTION in parts:
                        selected = {EXCHANGE_OPTION}
                    else:
                        selected = set(parts)
                context.user_data["edit_adv_methods"] = list(selected)
                return await _admin_reply(update,"💳 روش‌ها را انتخاب کنید:", reply_markup=_admin_methods_keyboard(selected))
            if field == "instant_transfer":
                context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_INSTANT.name
                await _best_effort_remove_keyboard(update)
                with get_db() as conn:
                    cur = conn.cursor()
                    row = cur.execute("SELECT instant_transfer FROM euro_adverts WHERE rowid = ?", (context.user_data.get("edit_advert_id"),)).fetchone()
                current = row[0] if row and row[0] else None
                context.user_data["edit_adv_instant"] = current
                return await _admin_reply(update,"🏦 وضعیت واریز آنی را انتخاب کنید:", reply_markup=_admin_instant_keyboard(current))

            context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_VALUE.name
            await _best_effort_remove_keyboard(update)
            if field == "fee_override_eur":
                return await _admin_reply(
                    update,
                    "🧾 <b>کارمزد (هر طرف، یورو)</b>\n\n"
                    "عدد را وارد کنید (مثلاً <code>6</code>).\n"
                    "برای <b>کارمزد ثابت صفر</b> بنویسید: <code>0</code>\n"
                    "برای بازگشت به فرمول خودکار بنویسید: <b>خودکار</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=_admin_edit_advert_value_inline_kb(),
                )
            return await _admin_reply(
                update,
                f"{_RTL}✏️ مقدار جدید را وارد کنید:\n\n"
                f"{_RTL}برای بازگشت یا انصراف از دکمه‌های زیر استفاده کنید.",
                reply_markup=_admin_edit_advert_value_inline_kb(),
            )

        if state == UserState.ADMIN_EDIT_ADVERT_RATE.name:
            raw_t = (text or "").strip()
            if raw_t == _ADMIN_KB_CANCEL:
                await _admin_exit_edit_advert_wizard(context, update)
                return
            advert_id = context.user_data.get("edit_advert_id")
            vv = _parse_int_from_text(text)
            if not isinstance(advert_id, int) or vv is None or vv <= 0:
                return await _admin_reply(
                    update,
                    "❌ نرخ معتبر وارد کنید (فقط عدد مثبت، مثال: 200000):\n\n"
                    "برای خروج دکمهٔ انصراف را بزنید.",
                    reply_markup=_inline_cancel(),
                )
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE euro_adverts SET rate_toman = ? WHERE rowid = ?", (str(vv), advert_id))
                row = cur.execute(
                    """
                    SELECT rowid, user_id, full_name, euro_amount, rate_toman, description, methods, operation,
                           city_ir, city_int, account_country, instant_transfer, channel_chat_id, channel_message_id,
                           COALESCE(euro_exchange, 0)
                    FROM euro_adverts
                    WHERE rowid = ?
                    """,
                    (advert_id,),
                ).fetchone()
            context.user_data["state"] = UserState.ADMIN_MENU.name
            if not row:
                return await _admin_reply(update,"ℹ️ آگهی پیدا نشد.", reply_markup=None, context=context)
            advert = {
                "rowid": row[0],
                "user_id": row[1],
                "full_name": row[2],
                "euro_amount": int(row[3]) if row[3] is not None and str(row[3]).isdigit() else row[3],
                "rate_toman": int(row[4]) if row[4] is not None and str(row[4]).isdigit() else row[4],
                "description": row[5],
                "methods": row[6],
                "operation": row[7],
                "city_ir": row[8],
                "city_int": row[9],
                "account_country": row[10],
                "instant_transfer": row[11],
                "channel_chat_id": row[12],
                "channel_message_id": row[13],
                "euro_exchange": row[14],
                "bot_username": (await context.bot.get_me()).username,
            }
            updated = False
            note = ""
            try:
                if advert.get("channel_message_id"):
                    await refresh_advert_channel_post(context.bot, int(advert["rowid"]))
                    updated = True
                else:
                    note = " (این آگهی قبل از آپدیت ساخته شده و پیام کانال در دیتابیس ذخیره نشده.)"
            except Exception:
                updated = False
            await _best_effort_remove_keyboard(update)
            return await _admin_reply(update,
                "✅ نرخ ذخیره شد و آگهی به‌روز شد."
                + (" (کانال هم آپدیت شد.)" if updated else " (آپدیت کانال ناموفق بود.)")
                + note,
                reply_markup=None, context=context,
            )

        if state == UserState.ADMIN_EDIT_ADVERT_VALUE.name:
            advert_id = context.user_data.get("edit_advert_id")
            field = context.user_data.get("edit_advert_field")
            value = text.strip()
            if value == _ADMIN_KB_CANCEL:
                await _admin_exit_edit_advert_wizard(context, update)
                return
            if value == _ADMIN_KB_BACK:
                context.user_data.pop("edit_advert_field", None)
                context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_FIELD.name
                adv_b = get_euro_advert_by_rowid(int(advert_id)) if isinstance(advert_id, int) else None
                show_b = not _offer_skips_toman_rate_step(adv_b) if adv_b else True
                return await _admin_reply(
                    update,
                    f"{_RTL}✏️ کدام فیلد آگهی را ویرایش کنیم؟",
                    reply_markup=_admin_edit_advert_fields_inline_kb(show_rate_row=show_b),
                )
            if not isinstance(advert_id, int) or not isinstance(field, str):
                context.user_data["state"] = UserState.ADMIN_MENU.name
                return await _admin_reply(update,"❌ خطا در وضعیت. دوباره تلاش کنید.", reply_markup=None, context=context)

            if field == "fee_override_eur":
                low = value.lower()
                if low in ("خودکار", "auto", "پاک", "حذف", "-", "none", ""):
                    sql_val = None
                else:
                    try:
                        x = float(value.replace(",", ".").replace("٫", "."))
                    except (TypeError, ValueError):
                        return await _admin_reply(
                            update,
                            "❌ عدد معتبر یورو وارد کنید یا برای فرمول خودکار بنویسید: خودکار",
                            reply_markup=_admin_edit_advert_value_inline_kb(),
                        )
                    if x < 0 or x > 1_000_000:
                        return await _admin_reply(
                            update,
                            "❌ مقدار باید بین ۰ تا ۱۰۰۰۰۰۰ یورو باشد یا «خودکار» برای فرمول پلکانی.",
                            reply_markup=_admin_edit_advert_value_inline_kb(),
                        )
                    sql_val = x
                updated, note = await _admin_persist_fee_override_eur(
                    context, advert_id, sql_val
                )
                context.user_data["state"] = UserState.ADMIN_MENU.name
                msg = (
                    "✅ کارمزد دستی حذف شد؛ از این پس فرمول خودکار اعمال می‌شود."
                    if sql_val is None
                    else (
                        "✅ کارمزد ثابت <b>۰ یورو</b> برای هر طرف ذخیره شد (در آگهی به‌صورت ۰ یورو نمایش داده می‌شود)."
                        if sql_val == 0
                        else "✅ کارمزد ذخیره شد."
                    )
                )
                await _best_effort_remove_keyboard(update)
                return await _admin_reply(
                    update,
                    msg
                    + (" (کانال هم آپدیت شد.)" if updated else " (آپدیت کانال ناموفق بود.)")
                    + note,
                    parse_mode="HTML",
                    reply_markup=None,
                    context=context,
                )

            # best-effort numeric coercion
            if field in {"euro_amount", "rate_toman"}:
                vv = _parse_int_from_text(value)
                if vv is None:
                    return await _admin_reply(
                        update,
                        "❌ فقط عدد وارد کنید:",
                        reply_markup=_admin_edit_advert_value_inline_kb(),
                    )
                value = str(vv)

            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(f"UPDATE euro_adverts SET {field} = ? WHERE rowid = ?", (value, advert_id))
                row = cur.execute(
                    """
                    SELECT rowid, user_id, full_name, euro_amount, rate_toman, description, methods, operation,
                           city_ir, city_int, account_country, instant_transfer, channel_chat_id, channel_message_id,
                           COALESCE(euro_exchange, 0)
                    FROM euro_adverts
                    WHERE rowid = ?
                    """,
                    (advert_id,),
                ).fetchone()

            context.user_data["state"] = UserState.ADMIN_MENU.name
            if not row:
                return await _admin_reply(update,"ℹ️ آگهی پیدا نشد.", reply_markup=None, context=context)

            advert = {
                "rowid": row[0],
                "user_id": row[1],
                "full_name": row[2],
                "euro_amount": int(row[3]) if row[3] is not None and str(row[3]).isdigit() else row[3],
                "rate_toman": int(row[4]) if row[4] is not None and str(row[4]).isdigit() else row[4],
                "description": row[5],
                "methods": row[6],
                "operation": row[7],
                "city_ir": row[8],
                "city_int": row[9],
                "account_country": row[10],
                "instant_transfer": row[11],
                "channel_chat_id": row[12],
                "channel_message_id": row[13],
                "euro_exchange": row[14],
                "bot_username": (await context.bot.get_me()).username,
            }

            # Update channel post if possible
            updated = False
            note = ""
            try:
                if advert.get("channel_message_id"):
                    await refresh_advert_channel_post(context.bot, int(advert["rowid"]))
                    updated = True
                else:
                    note = " (این آگهی قبل از آپدیت ساخته شده و پیام کانال در دیتابیس ذخیره نشده.)"
            except Exception:
                updated = False

            await _best_effort_remove_keyboard(update)
            return await _admin_reply(update,
                "✅ آگهی ویرایش شد."
                + (" (کانال هم آپدیت شد.)" if updated else " (آپدیت کانال ناموفق بود.)")
                + note,
                reply_markup=None, context=context,
            )

        if state == UserState.ADMIN_DELETE_ADVERT_ID.name:
            advert_id = _parse_int_from_text(text)
            if advert_id is None:
                return await _admin_reply(update,"❌ شماره آگهی معتبر وارد کنید:", reply_markup=_inline_cancel())
            context.user_data["delete_advert_id"] = advert_id
            context.user_data["state"] = UserState.ADMIN_DELETE_ADVERT_CONFIRM.name
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🗑️ تایید حذف آگهی", callback_data=f"admin_del_adv_yes_{advert_id}")],
                    [InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")],
                ]
            )
            return await _admin_reply(update,f"⚠️ حذف آگهی <code>#{advert_id}</code> را تایید می‌کنید؟", parse_mode="HTML", reply_markup=kb)

        if state == UserState.ADMIN_DELETE_USER_ID.name:
            uid = _parse_int_from_text(text)
            if uid is None:
                return await _admin_reply(update,"❌ آیدی عددی معتبر وارد کنید.")
            context.user_data["delete_user_id"] = uid
            context.user_data["state"] = UserState.ADMIN_DELETE_CONFIRM.name
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ تایید حذف کاربر",
                            callback_data=f"admin_del_user_yes_{uid}",
                        )
                    ],
                    [InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel")],
                ]
            )
            return await _admin_reply(
                update,
                f"⚠️ حذف کاربر <code>{uid}</code> را تایید می‌کنید؟",
                parse_mode="HTML",
                reply_markup=kb,
            )

        if state == UserState.ADMIN_DELETE_CONFIRM.name:
            uid = context.user_data.get("delete_user_id")
            context.user_data["state"] = UserState.ADMIN_MENU.name
            if text == "✅ تایید حذف" and isinstance(uid, int):
                ok = delete_user(uid)
                return await _admin_reply(
                    update,
                    "✅ حذف شد." if ok else "ℹ️ کاربر پیدا نشد.",
                    reply_markup=None,
                    context=context,
                )
            return await _admin_reply(update, "لغو شد.", reply_markup=None, context=context)

        if state == UserState.ADMIN_EDIT_USER_ID.name:
            raw_t = (text or "").strip()
            if raw_t == _ADMIN_KB_CANCEL:
                await _admin_exit_edit_user_wizard(context, update)
                return
            uid = _parse_int_from_text(text)
            if uid is None:
                return await _admin_reply(
                    update,
                    "❌ آیدی عددی معتبر وارد کنید یا «❌ انصراف» را بزنید.",
                    reply_markup=_inline_cancel(),
                )
            context.user_data["edit_user_id"] = uid
            context.user_data["state"] = UserState.ADMIN_EDIT_FIELD.name
            hint = f"\n\n«{_ADMIN_KB_BACK}»: آیدیٔ دیگر کاربر  ·  «{_ADMIN_KB_CANCEL}»: منوی ادمین"
            return await _admin_reply(
                update,
                "✏️ کدام فیلد را ویرایش کنیم؟" + hint,
                reply_markup=_admin_edit_user_fields_reply_kb(),
            )

        if state == UserState.ADMIN_EDIT_FIELD.name:
            raw_t = (text or "").strip()
            if raw_t == _ADMIN_KB_CANCEL:
                await _admin_exit_edit_user_wizard(context, update)
                return
            if raw_t == _ADMIN_KB_BACK:
                context.user_data.pop("edit_user_id", None)
                context.user_data.pop("edit_field", None)
                context.user_data["state"] = UserState.ADMIN_EDIT_USER_ID.name
                await _best_effort_remove_keyboard(update)
                return await _admin_reply(
                    update,
                    "✏️ آیدی عددی کاربر را وارد کنید:\n\nبرای خروج «❌ انصراف» را بزنید.",
                    reply_markup=_inline_cancel(),
                )
            field = _EDIT_FIELD_LABEL_TO_FIELD.get(text)
            if not field:
                return await _admin_reply(
                    update,
                    "❌ لطفاً یکی از دکمه‌های کیبورد را انتخاب کنید.",
                    reply_markup=_admin_edit_user_fields_reply_kb(),
                )
            context.user_data["edit_field"] = field
            context.user_data["state"] = UserState.ADMIN_EDIT_VALUE.name
            await _best_effort_remove_keyboard(update)
            return await _admin_reply(
                update,
                f"✏️ مقدار جدید را وارد کنید:\n\n"
                f"«{_ADMIN_KB_BACK}»: لیست فیلدها  ·  «{_ADMIN_KB_CANCEL}»: منوی ادمین",
                reply_markup=_admin_edit_advert_value_inline_kb(),
            )

        if state == UserState.ADMIN_EDIT_VALUE.name:
            uid = context.user_data.get("edit_user_id")
            field = context.user_data.get("edit_field")
            value = text.strip()
            if value == _ADMIN_KB_CANCEL:
                await _admin_exit_edit_user_wizard(context, update)
                return
            if value == _ADMIN_KB_BACK:
                context.user_data.pop("edit_field", None)
                context.user_data["state"] = UserState.ADMIN_EDIT_FIELD.name
                return await _admin_reply(
                    update,
                    "✏️ کدام فیلد را ویرایش کنیم؟\n\n"
                    f"«{_ADMIN_KB_BACK}»: آیدیٔ دیگر  ·  «{_ADMIN_KB_CANCEL}»: منوی ادمین",
                    reply_markup=_admin_edit_user_fields_reply_kb(),
                )
            if field == "display_name" and display_name_exists(value):
                return await _admin_reply(
                    update,
                    "❌ این نام نمایشی قبلاً استفاده شده است. یک نام دیگر وارد کنید:",
                    reply_markup=_admin_edit_advert_value_inline_kb(),
                )
            if field == "email":
                if not is_valid_email(value):
                    return await _admin_reply(
                        update,
                        "❌ آدرس ایمیل نامعتبر است. لطفاً دوباره وارد کنید:",
                        reply_markup=_admin_edit_advert_value_inline_kb(),
                    )
            if field == "phone_number":
                if not is_valid_phone(value):
                    return await _admin_reply(
                        update,
                        "❌ شماره تلفن معتبر نیست. لطفاً با + و فرمت بین‌المللی وارد کنید:",
                        reply_markup=_admin_edit_advert_value_inline_kb(),
                    )
                # If phone is new (not used by any user), verify by SMS first.
                if not get_user_by_phone(value):
                    code = generate_sms_code()
                    via_verify = False
                    if try_send_verification_sms(value, code):
                        via_verify = uses_twilio_verify()
                        hint = "📨 کد به خط موبایل پیامک شد.\n\n"
                    else:
                        hint = (
                            f"⚠️ پیامک ارسال نشد. کد: <code>{code}</code>\n"
                            "همان را وارد کنید.\n\n"
                        )
                    context.user_data["pending_phone_update"] = {
                        "uid": uid,
                        "field": field,
                        "value": value,
                        "code": code,
                        "otp_verify_twilio": via_verify,
                        "otp_telegram_sent": False,
                    }
                    context.user_data["state"] = UserState.ADMIN_EDIT_PHONE_VERIFY.name
                    return await _admin_reply(
                        update,
                        hint + "لطفاً کد را وارد کنید:\n\n"
                        f"«{_ADMIN_KB_CANCEL}»: لغو و خروج",
                        reply_markup=_inline_cancel(),
                        parse_mode="HTML",
                    )
            ok = False
            if isinstance(uid, int) and isinstance(field, str):
                ok = update_user_field(uid, field, value)
            context.user_data["state"] = UserState.ADMIN_MENU.name
            await _best_effort_remove_keyboard(update)
            return await _admin_reply(update,"✅ ذخیره شد." if ok else "❌ ذخیره نشد.", reply_markup=None, context=context)

        if state == UserState.ADMIN_EDIT_PHONE_VERIFY.name:
            pending = context.user_data.get("pending_phone_update") or {}
            input_code = text.strip()
            if input_code == _ADMIN_KB_CANCEL:
                context.user_data.pop("pending_phone_update", None)
                await _admin_exit_edit_user_wizard(context, update)
                return
            phone_chk = (pending.get("value") or "").strip()
            if not is_otp_code_valid(phone_chk, input_code, user_data=pending):
                return await _admin_reply(
                    update,
                    "❌ کد اشتباه است. دوباره وارد کنید یا «❌ انصراف» را بزنید:",
                    reply_markup=_inline_cancel(),
                )
            uid = pending.get("uid")
            field = pending.get("field")
            value = pending.get("value")
            ok = False
            if isinstance(uid, int) and isinstance(field, str):
                ok = update_user_field(uid, field, value)
            context.user_data.pop("pending_phone_update", None)
            context.user_data["state"] = UserState.ADMIN_MENU.name
            await _best_effort_remove_keyboard(update)
            return await _admin_reply(update,"✅ ذخیره شد." if ok else "❌ ذخیره نشد.", reply_markup=None, context=context)

        if state == UserState.ADMIN_ADD_USER_ID.name:
            uid = _parse_int_from_text(text)
            if uid is None:
                return await _admin_reply(update,"❌ آیدی عددی معتبر وارد کنید.")
            context.user_data["new_user_id"] = uid
            context.user_data["new_user"] = {"_step": "full_name"}
            context.user_data["state"] = UserState.ADMIN_ADD_USER_FIELD.name
            await _best_effort_remove_keyboard(update)
            return await _admin_reply(update,"👤 نام را وارد کنید:", reply_markup=_inline_cancel())

        if state == UserState.ADMIN_ADD_USER_FIELD.name:
            new_user = context.user_data.get("new_user") or {"_step": "full_name"}
            step = new_user.get("_step", "full_name")
            if step == "full_name":
                new_user["full_name"] = text.strip()
                new_user["_step"] = "last_name"
                context.user_data["new_user"] = new_user
                return await _admin_reply(update,"👤 نام خانوادگی را وارد کنید:", reply_markup=_inline_cancel())
            if step == "last_name":
                new_user["last_name"] = text.strip()
                new_user["_step"] = "display_name"
                context.user_data["new_user"] = new_user
                return await _admin_reply(update,"🏷️ نام ظاهر شده در آگهی را وارد کنید:", reply_markup=_inline_cancel())
            if step == "display_name":
                dn = text.strip()
                if display_name_exists(dn):
                    return await _admin_reply(update,"❌ این نام نمایشی قبلاً استفاده شده است. یک نام دیگر وارد کنید:", reply_markup=_inline_cancel())
                new_user["display_name"] = dn
                new_user["_step"] = "email"
                context.user_data["new_user"] = new_user
                return await _admin_reply(update,"📧 ایمیل را وارد کنید:", reply_markup=_inline_cancel())
            if step == "email":
                em = text.strip()
                if not is_valid_email(em):
                    return await _admin_reply(update,"❌ آدرس ایمیل نامعتبر است. لطفاً دوباره وارد کنید:", reply_markup=_inline_cancel())
                new_user["email"] = em
                new_user["_step"] = "address"
                context.user_data["new_user"] = new_user
                return await _admin_reply(update,"🏠 آدرس را وارد کنید:", reply_markup=_inline_cancel())
            if step == "address":
                new_user["address"] = text.strip()
                new_user["_step"] = "phone"
                context.user_data["new_user"] = new_user
                return await _admin_reply(update,"📱 شماره (با +) را وارد کنید:", reply_markup=_inline_cancel())
            if step == "phone":
                phone = text.strip()
                if not is_valid_phone(phone):
                    return await _admin_reply(update,"❌ شماره تلفن معتبر نیست. لطفاً با + و فرمت بین‌المللی وارد کنید:", reply_markup=_inline_cancel())
                new_user["phone_number"] = phone
                uid = context.user_data.get("new_user_id")
                # Validate before inserting for clear error messages
                if get_user_by_id(int(uid)):
                    context.user_data["state"] = UserState.ADMIN_MENU.name
                    context.user_data.pop("new_user", None)
                    return await _admin_reply(update,"❌ این آیدی تلگرام قبلاً ثبت شده است.", reply_markup=None, context=context)
                # Admin can reuse phone. If phone is NEW, verify by SMS code first.
                if get_user_by_phone(phone):
                    ok = True
                    try:
                        save_user(
                            user_id=int(uid),
                            full_name=new_user.get("full_name"),
                            last_name=new_user.get("last_name"),
                            email=new_user.get("email"),
                            address=new_user.get("address"),
                            phone_number=phone,
                            display_name=new_user.get("display_name"),
                            username=None,
                        )
                    except Exception as e:
                        context.user_data["state"] = UserState.ADMIN_MENU.name
                        context.user_data.pop("new_user", None)
                        return await _admin_reply(update,_friendly_db_error(e), reply_markup=None, context=context)
                    context.user_data["state"] = UserState.ADMIN_MENU.name
                    context.user_data.pop("new_user", None)
                    return await _admin_reply(update,
                        "✅ کاربر اضافه شد.",
                        reply_markup=None, context=context,
                    )

                code = generate_sms_code()
                new_user["sms_code"] = code
                new_user["_step"] = "verify_code"
                new_user["otp_verify_twilio"] = False
                context.user_data["new_user"] = new_user
                if try_send_verification_sms(phone, code):
                    new_user["otp_verify_twilio"] = uses_twilio_verify()
                    context.user_data["new_user"] = new_user
                    return await _admin_reply(
                        update,
                        "📨 کد به خط موبایل پیامک شد.\n"
                        "کدی که کاربر دریافت کرد را اینجا وارد کنید:\n\n"
                        "انصراف → بازگشت به <b>منوی ادمین</b>.",
                        reply_markup=_admin_add_user_otp_keyboard(),
                        parse_mode="HTML",
                    )
                return await _admin_reply(
                    update,
                    "⚠️ <b>پیامک ارسال نشد.</b>\n"
                    "«ارسال مجدد پیامک» یا «نمایش کد در چت» را بزنید، "
                    "سپس همان کد را اینجا وارد کنید.\n\n"
                    "انصراف → بازگشت به <b>منوی ادمین</b>.",
                    reply_markup=_admin_add_user_otp_keyboard(),
                    parse_mode="HTML",
                )

            if step == "verify_code":
                input_code = text.strip()
                phone_chk = (new_user.get("phone_number") or "").strip()
                if not is_otp_code_valid(phone_chk, input_code, user_data=new_user):
                    return await _admin_reply(
                        update,
                        "❌ کد اشتباه است. دوباره وارد کنید:",
                        reply_markup=_admin_add_user_otp_keyboard(),
                    )
                uid = context.user_data.get("new_user_id")
                try:
                    save_user(
                        user_id=int(uid),
                        full_name=new_user.get("full_name"),
                        last_name=new_user.get("last_name"),
                        email=new_user.get("email"),
                        address=new_user.get("address"),
                        phone_number=new_user.get("phone_number"),
                        display_name=new_user.get("display_name"),
                        username=None,
                    )
                except Exception as e:
                    context.user_data["state"] = UserState.ADMIN_MENU.name
                    context.user_data.pop("new_user", None)
                    return await _admin_reply(update,_friendly_db_error(e), reply_markup=None, context=context)
                context.user_data["state"] = UserState.ADMIN_MENU.name
                context.user_data.pop("new_user", None)
                return await _admin_reply(update,"✅ کاربر اضافه شد.", reply_markup=None, context=context)

        if state == UserState.ADMIN_PROXY_OFFER_ADVERT.name:
            aid = _parse_int_from_text(text)
            if aid is None:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ شماره آگهی معتبر نیست.",
                    reply_markup=_inline_cancel(),
                )
            advert = get_euro_advert_by_rowid(aid)
            if not advert:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ آگهی پیدا نشد.",
                    reply_markup=_inline_cancel(),
                )
            owner_uid = int(advert.get("user_id") or 0)
            if owner_uid == user_id:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ برای آگهی خودتان نمی‌توانید پیشنهاد نمایشی ثبت کنید.",
                    reply_markup=_inline_cancel(),
                )
            context.user_data["admin_proxy_aid"] = aid
            context.user_data["state"] = UserState.ADMIN_PROXY_OFFER_NAME.name
            _persist_admin_wizard_state(user_id, context)
            return await _admin_reply(
                update,
                f"{_RTL}2️⃣ <b>نام نمایشی</b> پیشنهاد را بفرستید (همان نامی که صاحب آگهی می‌بیند):",
                reply_markup=_inline_cancel(),
                parse_mode="HTML",
            )

        if state == UserState.ADMIN_PROXY_OFFER_NAME.name:
            alias = (text or "").strip()
            if len(alias) < 2:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ نام نمایشی را کامل‌تر وارد کنید (حداقل ۲ نویسه).",
                    reply_markup=_inline_cancel(),
                )
            if len(alias) > 120:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ نام نمایشی خیلی طولانی است (حداکثر ۱۲۰ نویسه).",
                    reply_markup=_inline_cancel(),
                )
            context.user_data["admin_proxy_alias"] = alias
            context.user_data["state"] = UserState.ADMIN_PROXY_OFFER_RATE.name
            _persist_admin_wizard_state(user_id, context)
            return await _admin_reply(
                update,
                f"{_RTL}3️⃣ نرخ پیشنهاد را به <b>تومان</b> بفرستید (فقط عدد، مثلاً ۲۱۰۰۰۰):",
                reply_markup=_inline_cancel(),
                parse_mode="HTML",
            )

        if state == UserState.ADMIN_PROXY_OFFER_RATE.name:
            rate = _parse_int_from_text(text)
            if rate is None or rate <= 0:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ نرخ معتبر نیست. فقط عدد تومان بفرستید.",
                    reply_markup=_inline_cancel(),
                )
            context.user_data["admin_proxy_rate"] = rate
            context.user_data["state"] = UserState.ADMIN_PROXY_OFFER_DESC.name
            _persist_admin_wizard_state(user_id, context)
            return await _admin_reply(
                update,
                f"{_RTL}4️⃣ <b>توضیحات پیشنهاد</b> را بنویسید (شرایط، زمان تماس، …):",
                reply_markup=_inline_cancel(),
                parse_mode="HTML",
            )

        if state == UserState.ADMIN_PROXY_OFFER_DESC.name:
            desc = (text or "").strip()
            if len(desc) < 2:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ توضیحات را کمی کامل‌تر بنویسید (حداقل ۲ نویسه).",
                    reply_markup=_inline_cancel(),
                )
            if len(desc) > 3500:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ توضیحات خیلی طولانی است. کوتاه‌تر کنید.",
                    reply_markup=_inline_cancel(),
                )
            aid = context.user_data.get("admin_proxy_aid")
            alias = context.user_data.get("admin_proxy_alias")
            rate = context.user_data.get("admin_proxy_rate")
            if not isinstance(aid, int) or not isinstance(alias, str) or not isinstance(rate, int):
                context.user_data["state"] = UserState.ADMIN_MENU.name
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ خطای فلو. از منو دوباره شروع کنید.",
                    reply_markup=None,
                    context=context,
                )
            ins = insert_advert_offer(aid, user_id, rate, desc, offer_alias_name=alias)
            if ins is None:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ ذخیره پیشنهاد انجام نشد.",
                    reply_markup=_inline_cancel(),
                )
            row_id, offer_seq = ins
            await dispatch_offer_created_notifications(
                context.bot,
                advert_rowid=aid,
                proposer_telegram_id=user_id,
                offer_row_id=row_id,
                offer_seq=int(offer_seq),
                rate_toman=rate,
                description=desc,
                public_display_name=alias.strip(),
                is_admin_proxy=True,
            )
            for k in ("admin_proxy_aid", "admin_proxy_alias", "admin_proxy_rate"):
                context.user_data.pop(k, None)
            context.user_data["state"] = UserState.ADMIN_MENU.name
            _persist_admin_wizard_state(user_id, context)
            return await _admin_reply(
                update,
                f"{_RTL}✅ پیشنهاد نمایشی ثبت شد؛ پیام برای صاحب آگهی و ادمین‌ها ارسال شد.",
                reply_markup=None,
                context=context,
            )

        if state == UserState.ADMIN_MANAGE_OFFER_ADVERT.name:
            aid = _parse_int_from_text(text)
            if aid is None:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ شماره آگهی معتبر نیست.",
                    reply_markup=_inline_cancel(),
                )
            advert = get_euro_advert_by_rowid(aid)
            if not advert:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ آگهی پیدا نشد.",
                    reply_markup=_inline_cancel(),
                )
            _admin_offer_wiz_note(context, update.message.message_id)
            await _admin_offer_show_pick_page(update, context, aid=aid, advert=advert)
            return

        if state == UserState.ADMIN_MANAGE_OFFER_SEQ.name:
            seq = _parse_int_from_text(text)
            aid = context.user_data.get("admin_offer_advert")
            if seq is None or not isinstance(aid, int):
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ شماره نامعتبر.",
                    reply_markup=_inline_cancel(),
                )
            orow = get_offer_by_advert_and_seq(aid, seq)
            if not orow:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ پیشنهاد پیدا نشد.",
                    reply_markup=_inline_cancel(),
                )
            _admin_offer_wiz_note(context, update.message.message_id)
            await _admin_offer_show_detail_page(
                update, context, aid=aid, oid=int(orow["id"])
            )
            return

        if state == UserState.ADMIN_MANAGE_OFFER_CMD.name:
            oid = context.user_data.get("admin_offer_db_id")
            aid = context.user_data.get("admin_offer_advert")
            kb = (
                _admin_offer_action_keyboard(int(oid), advert_id=int(aid))
                if isinstance(oid, int) and isinstance(aid, int)
                else _inline_cancel()
            )
            return await _admin_reply(
                update,
                f"{_RTL}از دکمه‌های زیر پیام استفاده کنید.",
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )

        if state == UserState.ADMIN_MANAGE_OFFER_RATE_INPUT.name:
            oid = context.user_data.get("admin_offer_db_id")
            aid = context.user_data.get("admin_offer_advert")
            if not isinstance(oid, int) or not isinstance(aid, int):
                context.user_data["state"] = UserState.ADMIN_MENU.name
                return await _admin_reply(
                    update, f"{_RTL}❌ خطای فلو.", reply_markup=None, context=context
                )
            rate = _parse_int_from_text(text or "")
            if rate is None or rate <= 0:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ نرخ نامعتبر. فقط عدد تومان بفرستید.",
                    reply_markup=_inline_cancel(),
                )
            if not admin_update_offer_rate(oid, rate):
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ به‌روز نشد.",
                    reply_markup=_inline_cancel(),
                )
            row_o = get_advert_offer_joined(oid)
            seq_disp = int(row_o.get("seq_in_advert") or oid) if row_o else oid
            await refresh_advert_channel_post(context.bot, aid)
            await refresh_offer_notification_cards_after_rate_change(
                context.bot,
                user_data_store,
                offer_db_id=oid,
                advert_rowid=aid,
            )
            if row_o:
                for uid in (int(row_o["owner_id"]), int(row_o["proposer_telegram_id"])):
                    try:
                        await context.bot.send_message(
                            uid,
                            f"{_RTL}نرخ پیشنهاد <b>{seq_disp}</b> برای آگهی <b>{aid}</b> توسط مدیریت به "
                            f"<b>{rate:,}</b> تومان تغییر کرد.",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass
            _admin_offer_wiz_note(context, update.message.message_id)
            await _admin_offer_finish_edit(
                update,
                context,
                aid=aid,
                oid=oid,
                success_html=(
                    f"{_RTL}✅ نرخ پیشنهاد <b>{seq_disp}</b> برای آگهی <b>{aid}</b> "
                    f"به <b>{rate:,}</b> تومان به‌روز شد."
                ),
            )
            return

        if state == UserState.ADMIN_MANAGE_OFFER_EURO_INPUT.name:
            oid = context.user_data.get("admin_offer_db_id")
            aid = context.user_data.get("admin_offer_advert")
            if not isinstance(oid, int) or not isinstance(aid, int):
                context.user_data["state"] = UserState.ADMIN_MENU.name
                return await _admin_reply(
                    update, f"{_RTL}❌ خطای فلو.", reply_markup=None, context=context
                )
            val = _parse_int_from_text(text or "")
            if val is None or val <= 0:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ مقدار یورو نامعتبر.",
                    reply_markup=_inline_cancel(),
                )
            if not admin_update_offer_proposed_euro(oid, val):
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ مقدار یورو به‌روز نشد.",
                    reply_markup=_inline_cancel(),
                )
            row_o = get_advert_offer_joined(oid)
            seq_disp = int(row_o.get("seq_in_advert") or oid) if row_o else oid
            await refresh_advert_channel_post(context.bot, aid)
            await refresh_offer_notification_cards_after_rate_change(
                context.bot,
                user_data_store,
                offer_db_id=oid,
                advert_rowid=aid,
            )
            if row_o:
                for uid in (int(row_o["owner_id"]), int(row_o["proposer_telegram_id"])):
                    try:
                        await context.bot.send_message(
                            uid,
                            f"{_RTL}مقدار یوروی پیشنهاد <b>{seq_disp}</b> برای آگهی <b>{aid}</b> "
                            f"توسط مدیریت به <b>{val:,}</b> یورو تغییر کرد.",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass
            _admin_offer_wiz_note(context, update.message.message_id)
            await _admin_offer_finish_edit(
                update,
                context,
                aid=aid,
                oid=oid,
                success_html=(
                    f"{_RTL}✅ مقدار یوروی پیشنهاد <b>{seq_disp}</b> به <b>{val:,}</b> یورو به‌روز شد."
                ),
            )
            return

        if state == UserState.ADMIN_FEE_ADVERT_ID.name:
            aid = _parse_int_from_text(text)
            if aid is None:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ شماره آگهی معتبر نیست.",
                    reply_markup=_inline_cancel(),
                )
            if not get_euro_advert_by_rowid(aid):
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ آگهی پیدا نشد.",
                    reply_markup=_inline_cancel(),
                )
            context.user_data["admin_fee_edit_rowid"] = aid
            context.user_data["state"] = UserState.ADMIN_FEE_VALUE.name
            _persist_admin_wizard_state(user_id, context)
            return await _admin_reply(
                update,
                "🧾 <b>کارمزد (هر طرف، یورو)</b>\n\n"
                "عدد را وارد کنید (مثلاً <code>6</code>).\n"
                "برای <b>کارمزد ثابت صفر</b> (بدون پلکانی، با نمایش ۰ یورو) بنویسید: <code>0</code>\n"
                "برای بازگشت به فرمول خودکار بنویسید: <b>خودکار</b>",
                parse_mode="HTML",
                reply_markup=_inline_cancel(),
            )

        if state == UserState.ADMIN_FEE_VALUE.name:
            aid = context.user_data.get("admin_fee_edit_rowid")
            if not isinstance(aid, int):
                context.user_data["state"] = UserState.ADMIN_MENU.name
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ خطا در فلو. دوباره از منو شروع کنید.",
                    reply_markup=None,
                    context=context,
                )
            low = (text or "").strip().lower()
            if low in ("خودکار", "auto", "پاک", "حذف", "-", "none", ""):
                sql_val = None
            else:
                try:
                    x = float((text or "").strip().replace(",", ".").replace("٫", "."))
                except (TypeError, ValueError):
                    return await _admin_reply(
                        update,
                        "❌ عدد معتبر یورو وارد کنید یا برای فرمول خودکار بنویسید: خودکار",
                        reply_markup=_inline_cancel(),
                    )
                if x < 0 or x > 1_000_000:
                    return await _admin_reply(
                        update,
                        "❌ مقدار باید بین ۰ تا ۱۰۰۰۰۰۰ یورو باشد یا «خودکار» برای فرمول پلکانی.",
                        reply_markup=_inline_cancel(),
                    )
                sql_val = x
            updated, note = await _admin_persist_fee_override_eur(context, aid, sql_val)
            context.user_data.pop("admin_fee_edit_rowid", None)
            context.user_data["state"] = UserState.ADMIN_MENU.name
            _persist_admin_wizard_state(user_id, context)
            msg = (
                "✅ کارمزد دستی حذف شد؛ از این پس فرمول خودکار اعمال می‌شود."
                if sql_val is None
                else (
                    "✅ کارمزد ثابت <b>۰ یورو</b> برای هر طرف ذخیره شد (در آگهی به‌صورت ۰ یورو نمایش داده می‌شود)."
                    if sql_val == 0
                    else "✅ کارمزد آگهی ذخیره شد."
                )
            )
            return await _admin_reply(
                update,
                msg
                + (" (کانال هم آپدیت شد.)" if updated else " (آپدیت کانال ناموفق بود.)")
                + note,
                parse_mode="HTML",
                reply_markup=None,
                context=context,
            )

        if state == UserState.ADMIN_RESTRICT_USER_ID.name:
            uid = _parse_int_from_text(text)
            if uid is None:
                return await _admin_reply(update, f"{_RTL}❌ آیدی عددی معتبر وارد کنید.", reply_markup=_inline_cancel())
            if not get_user(uid):
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ کاربری با این آیدی در ربات ثبت‌نام نکرده است.",
                    reply_markup=_inline_cancel(),
                )
            context.user_data.pop("restrict_uid", None)
            context.user_data["state"] = UserState.ADMIN_MENU.name
            body = f"{_RTL}کاربر <code>{uid}</code> — محدود کردن یا برداشتن محدودیت:"
            rk = admin_restrict_actions_keyboard(uid)
            edited = await _admin_edit_dashboard(
                context,
                context.bot,
                body,
                reply_markup=rk,
                parse_mode="HTML",
            )
            if edited:
                try:
                    await update.message.delete()
                except Exception:
                    pass
                return None
            sent = await _admin_reply(
                update,
                body,
                reply_markup=rk,
                parse_mode="HTML",
                context=context,
            )
            try:
                await update.message.delete()
            except Exception:
                pass
            return sent

        if state == _ADMIN_RESTRICT_DAYS_STATE_NAME:
            uid = context.user_data.get("restrict_uid")
            if not isinstance(uid, int):
                context.user_data["state"] = UserState.ADMIN_MENU.name
                context.user_data.pop("restrict_uid", None)
                return await _admin_reply(update, f"{_RTL}❌ خطا در فلو.", reply_markup=None, context=context)
            days = _parse_int_from_text(text)
            if days is None or days < 0:
                return await _admin_reply(
                    update,
                    f"{_RTL}❌ عدد روز معتبر وارد کنید (۰ یا بیشتر).",
                    reply_markup=_inline_cancel(),
                )
            until_ts = None if days == 0 else int(time.time()) + days * 86400
            ok = set_user_restriction(uid, True, until_ts=until_ts)
            context.user_data["state"] = UserState.ADMIN_MENU.name
            context.user_data.pop("restrict_uid", None)
            msg = (
                "✅ دسترسی کاربر محدود شد (دائمی)."
                if days == 0
                else f"✅ دسترسی کاربر به مدت {days} روز محدود شد."
            )
            if not ok:
                msg = "❌ انجام نشد."
            chat_id_ok = update.effective_chat.id
            edited_ok = await _admin_edit_dashboard(context, context.bot, msg)
            if not edited_ok:
                await _admin_reply(update, msg, reply_markup=None, context=context)
            else:
                await _admin_reply(
                    update,
                    f"{_RTL}{msg}\n\n{_RTL}ℹ️ همین متن روی پنل مدیریت در بالای چت هم به‌روز شد.",
                    reply_markup=None,
                    context=context,
                )
            ids_done = user_data_store.setdefault(user_id, {}).pop(_ADMIN_USER_INPUT_KEY, [])
            await cleanup_ids(context.bot, chat_id=chat_id_ok, ids=ids_done)
            return

        # Ensure we are in admin context
        if context.user_data.get("state") != UserState.ADMIN_MENU.name:
            context.user_data["state"] = UserState.ADMIN_MENU.name

        if text == "👥 لیست کاربران":
            with get_db() as conn:
                cur = conn.cursor()
                try:
                    rows = cur.execute(
                        "SELECT telegram_id, username, display_name, full_name, last_name, phone_number, email, address FROM users ORDER BY rowid DESC LIMIT 30"
                    ).fetchall()
                except Exception:
                    rows = []
            if not rows:
                return await _admin_reply(update,"ℹ️ کاربری یافت نشد.")
            enriched = []
            for r in rows:
                tg_id = int(r[0])
                uname = await _ensure_username(context, tg_id, r[1])
                rr = (r[0], uname, r[2], r[3], r[4], r[5], r[6], r[7])
                enriched.append(rr)
            blocks = "\n".join([_fmt_user_block(r, idx=i + 1) for i, r in enumerate(enriched)])
            return await _admin_reply(update,
                "👥 <b>لیست کاربران</b>\n\n" + blocks,
                parse_mode="HTML",
                reply_markup=None, context=context,
                disable_web_page_preview=True,
            )

        if text == "🔎 جستجوی کاربر":
            context.user_data["state"] = UserState.ADMIN_SEARCH_USER.name
            _persist_admin_wizard_state(user_id, context)
            return await _admin_reply(update,
                "🔎 عبارت جستجو را وارد کنید (نام نمایشی آگهی / نام / @username / شماره / آیدی):",
                reply_markup=_inline_cancel(),
            )

        if text == "🔎 جستجوی آگهی":
            context.user_data["state"] = UserState.ADMIN_SEARCH_ADVERT.name
            _persist_admin_wizard_state(user_id, context)
            return await _admin_reply(update,"🔎 شماره آگهی را وارد کنید:", reply_markup=_inline_cancel())

        if text == "🗣️ مذاکرات آگهی":
            context.user_data["state"] = UserState.ADMIN_NEG_VIEW_ADVERT.name
            _persist_admin_wizard_state(user_id, context)
            return await _admin_reply(
                update,
                f"{_RTL}🗣️ شمارهٔ آگهی (<code>rowid</code>) یا مثلاً <code>/neg_ad 74</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=_inline_cancel(),
            )

        if text == "✏️ ویرایش آگهی":
            context.user_data["state"] = UserState.ADMIN_EDIT_ADVERT_ID.name
            return await _admin_reply(
                update,
                f"{_RTL}✏️ شماره آگهی (<code>rowid</code>) را وارد کنید:\n\n"
                f"{_RTL}برای خروج دکمهٔ انصراف را بزنید.",
                parse_mode=ParseMode.HTML,
                reply_markup=_inline_cancel(),
            )

        if text == "🗑️ حذف آگهی":
            context.user_data["state"] = UserState.ADMIN_DELETE_ADVERT_ID.name
            return await _admin_reply(update,"🗑️ شماره آگهی را وارد کنید:", reply_markup=_inline_cancel())

        if text == "🗑️ حذف کاربر":
            context.user_data["state"] = UserState.ADMIN_DELETE_USER_ID.name
            return await _admin_reply(update,"🗑️ آیدی عددی کاربر را وارد کنید:", reply_markup=_inline_cancel())

        if text == "✏️ ویرایش کاربر":
            context.user_data["state"] = UserState.ADMIN_EDIT_USER_ID.name
            return await _admin_reply(
                update,
                "✏️ آیدی عددی کاربر را وارد کنید:\n\nبرای خروج «❌ انصراف» را بزنید.",
                reply_markup=_inline_cancel(),
            )

        if text == "➕ افزودن کاربر":
            context.user_data["state"] = UserState.ADMIN_ADD_USER_ID.name
            await _best_effort_remove_keyboard(update)
            return await _admin_reply(update,"➕ آیدی عددی تلگرام کاربر را وارد کنید:", reply_markup=_inline_cancel())

        if text == "🔒 محدودیت دسترسی کاربر":
            context.user_data["state"] = UserState.ADMIN_RESTRICT_USER_ID.name
            return await _admin_reply(
                update,
                f"{_RTL}🆔 آیدی عددی تلگرام کاربر را وارد کنید:",
                reply_markup=_inline_cancel(),
            )

        if text == "📋 مدیریت پیشنهاد آگهی":
            context.user_data["state"] = UserState.ADMIN_MANAGE_OFFER_ADVERT.name
            context.user_data.pop("admin_offer_advert", None)
            context.user_data.pop("admin_offer_db_id", None)
            context.user_data["admin_offer_wizard_mids"] = []
            _persist_admin_wizard_state(user_id, context)
            _admin_offer_wiz_note(context, update.message.message_id)
            sent = await _admin_reply(
                update,
                f"{_RTL}📋 مدیریت پیشنهاد\n{_RTL}1️⃣ شماره آگهی (rowid) را بفرستید:",
                reply_markup=_inline_cancel(),
            )
            _admin_offer_wiz_note(context, getattr(sent, "message_id", None))
            return sent

        if text == "🧾 ویرایش کارمزد آگهی":
            context.user_data["state"] = UserState.ADMIN_FEE_ADVERT_ID.name
            context.user_data.pop("admin_fee_edit_rowid", None)
            _persist_admin_wizard_state(user_id, context)
            return await _admin_reply(
                update,
                f"{_RTL}🧾 <b>ویرایش کارمزد (هر طرف، یورو)</b>\n\n"
                f"{_RTL}شماره آگهی (<code>rowid</code>) را بفرستید:",
                parse_mode="HTML",
                reply_markup=_inline_cancel(),
            )

        if text == "➕ ثبت آگهی":
            context.user_data["state"] = UserState.ADMIN_ADD_ADVERT.name
            context.user_data["admin_add_ad_step"] = "user_id"
            context.user_data.pop("admin_new_advert_owner_id", None)
            context.user_data.pop("admin_post_advert_for", None)
            return await _admin_reply(update,
                f"{_RTL}🆔 آیدی عددی تلگرام کاربر صاحب آگهی را وارد کنید.\n"
                f"{_RTL}اگر کاربر در ربات ثبت‌نام نکرده، یکی از این‌ها را بفرستید: ۰ ، - ، ندارد",
                reply_markup=_inline_cancel(),
            )

        if text == "📢 لیست آگهی‌ها":
            with get_db() as conn:
                cur = conn.cursor()
                try:
                    rows = cur.execute(
                        """
                        SELECT
                            a.rowid,
                            COALESCE(u.display_name, a.full_name) AS adv_name,
                            u.username,
                            a.euro_amount,
                            a.rate_toman,
                            a.operation
                        FROM euro_adverts a
                        LEFT JOIN users u ON u.telegram_id = a.user_id
                        ORDER BY a.rowid DESC
                        LIMIT 15
                        """
                    ).fetchall()
                except Exception:
                    rows = []
            if not rows:
                return await _admin_reply(update,"ℹ️ آگهی‌ای یافت نشد.")
            lines = []
            for r in rows:
                advert_id, adv_name, uname, amount, rate, op = r
                u_at = f"@{uname}" if uname else "—"
                lines.append(f"- #{advert_id} | {op} | {adv_name} | {u_at} | {amount}€ | {_fmt_thousands(rate)}")
            return await _admin_reply(update,"📢 آخرین آگهی‌ها:\n" + "\n".join(lines))

        # Fallback
        return await _admin_reply(update,"لطفاً یکی از گزینه‌های پنل مدیریت را انتخاب کنید.")
    finally:
        _persist_admin_wizard_state(user_id, context)
