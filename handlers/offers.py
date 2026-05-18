"""
handlers/offers.py — Offers on ads / پیشنهاد به آگهی

EN:
  Gate (agree/custom), rate, country, description, preview, confirm;
  owner accept/reject; channel post refresh; negotiation messages.

FA:
  گیت پیشنهاد، نرخ/کشور/توضیحات، تأیید؛ اقدام صاحب آگهی؛ به‌روزرسانی پست کانال.
"""

from __future__ import annotations

import html as html_module
import re
import time

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Message
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config.settings import (
    ADMIN_IDS,
    ADVERT_CHANNEL_ID,
    BOT_USERNAME,
    CHANNEL_USERNAME,
    DEAL_NEXT_STEPS_ADMIN,
)
from database.db import (
    delete_advert_offer_if_pending,
    delete_pending_offers_for_proposer_on_advert,
    get_advert_offer_joined,
    get_euro_advert_by_rowid,
    get_last_rejected_offer_rate_toman,
    get_user,
    insert_advert_offer,
    list_accepted_offers_for_advert,
    list_my_advert_offers,
    list_my_pending_offers_all,
    list_pending_offers_for_advert,
    list_rejected_offers_for_advert,
    negotiation_transcript_append_line,
    negotiation_transcript_list,
    proposer_offer_rate_exists,
    proposer_offer_rate_exists_other_than,
    proposer_has_pending_offer_on_advert,
    reject_other_pending_offers_for_advert,
    update_advert_offer_status,
    update_proposer_pending_offer_rate,
)
from keyboards.menus import main_menu_inline_keyboard
from models.enums import UserState
from state import user_data_store
from utils.telegram_utils import (
    cleanup_ids,
    normalize_telegram_callback_data,
    remove_main_menu_anchor_message,
    send_or_replace_main_menu,
    cleanup_transient_dm_messages,
)
from telegram.error import BadRequest

from utils.euro_fees import advert_fee_override_eur, fee_total_eur, format_fee_eur

_RTL = "\u200f"

MY_OFFERS_SENTINEL = "پیشنهادهای شما (در انتظار تأیید)"


def _is_hybrid_euro_exchange_advert(advert: dict | None) -> bool:
    """آگهی خرید/فروش با پرچم euro_exchange (معاوضهٔ یورو به یورو)."""
    if not advert:
        return False
    op = (advert.get("operation") or "").strip()
    return int(advert.get("euro_exchange") or 0) == 1 and op in ("خرید", "فروش")


def _offer_skips_toman_rate_step(advert: dict | None) -> bool:
    """
    پیشنهاد بدون گام «نرخ تومان»: معاوضهٔ قدیمی (operation=معاوضه) یا خرید/فروش با euro_exchange.
    """
    if not advert:
        return False
    op = (advert.get("operation") or "").strip()
    if op == "معاوضه":
        return True
    return _is_hybrid_euro_exchange_advert(advert)


def advert_public_link_html(advert: dict | None, aid: int) -> str:
    """لینک قابل کلیک «آگهی N» به پست کانال؛ در نبود message_id فقط <b>آگهی N</b>."""
    if advert:
        ch = (CHANNEL_USERNAME or "").strip().lstrip("@")
        mid = advert.get("channel_message_id")
        if ch and mid is not None:
            try:
                url = f"https://t.me/{ch}/{int(mid)}"
                return f'<a href="{html_module.escape(url)}">آگهی {aid}</a>'
            except (TypeError, ValueError):
                pass
    return f"<b>آگهی {aid}</b>"

_NEG_SESSIONS_KEY = "offer_negotiation_sessions"  # فقط پاکسازی دادهٔ قدیمی؛ قفل دونفره حذف شده


def _neg_offer_ids_as_set(user_data: dict) -> set[int]:
    raw = user_data.get("neg_offer_ids")
    if raw is None:
        return set()
    if isinstance(raw, set):
        return {int(x) for x in raw}
    if isinstance(raw, (list, tuple)):
        return {int(x) for x in raw}
    return set()


def _neg_offer_ids_write(user_data: dict, ids: set[int]) -> None:
    if not ids:
        user_data.pop("neg_offer_ids", None)
    else:
        user_data["neg_offer_ids"] = ids


def _discard_negotiation_offer(context: ContextTypes.DEFAULT_TYPE, offer_id: int) -> None:
    """حذف یک پیشنهاد از فهرست مذاکره‌های باز؛ اگر کانال فعال همان بود، کانال دیگری یا منو."""
    oid = int(offer_id)
    ids = _neg_offer_ids_as_set(context.user_data)
    ids.discard(oid)
    _neg_offer_ids_write(context.user_data, ids)
    context.user_data.pop("neg_gate_mid", None)
    context.user_data.pop("neg_gate_offer_id", None)
    if context.user_data.get("state") == UserState.NEGOTIATION_GATE.name:
        context.user_data["state"] = UserState.MAIN_MENU.name
    if context.user_data.get("neg_offer_id") == oid:
        context.user_data.pop("neg_offer_id", None)
        if ids:
            context.user_data["neg_offer_id"] = next(iter(ids))
        else:
            context.user_data["state"] = UserState.MAIN_MENU.name


_NEG_MAX_LINES = 40
_NEG_TRANSCRIPTS_KEY = "neg_offer_transcripts"


def register_offer_thread_message(
    store: dict, user_id: int, offer_db_id: int, message_id: int | None
) -> None:
    """ثبت پیام‌های مرتبط با یک پیشنهاد برای بعداً پاک کردن یکجا (تأیید/رد/حذف)."""
    if message_id is None:
        return
    uid = int(user_id)
    oid = int(offer_db_id)
    k = f"ot_{oid}"
    xs = store.setdefault(uid, {}).setdefault(k, [])
    mid = int(message_id)
    if mid not in xs:
        xs.append(mid)


async def purge_offer_thread_messages(
    bot,
    store: dict,
    owner_telegram_id: int,
    proposer_telegram_id: int,
    offer_db_id: int,
) -> None:
    """حذف همهٔ پیام‌های ثبت‌شدهٔ این پیشنهاد برای صاحب آگهی و پیشنهاددهنده."""
    oid = int(offer_db_id)
    k_ot = f"ot_{oid}"
    k_negp = f"negp_{oid}"
    for uid in (owner_telegram_id, proposer_telegram_id):
        if not uid:
            continue
        uid = int(uid)
        b = store.setdefault(uid, {})
        b.pop(k_negp, None)
        for mid in list(b.pop(k_ot, []) or []):
            try:
                await bot.delete_message(chat_id=uid, message_id=int(mid))
            except Exception:
                pass


def _neg_transcripts_map(app_data: dict) -> dict:
    """کش قدیمی در RAM؛ فقط برای پاکسازی پس از پایان مذاکره."""
    m = app_data.setdefault(_NEG_TRANSCRIPTS_KEY, {})
    if not isinstance(m, dict):
        m = {}
        app_data[_NEG_TRANSCRIPTS_KEY] = m
    return m


def clear_neg_transcript(app_data: dict, offer_db_id: int) -> None:
    """پاک کردن کش درون‌پردازشی؛ متن مذاکره در SQLite می‌ماند."""
    try:
        _neg_transcripts_map(app_data).pop(int(offer_db_id), None)
    except Exception:
        pass


def neg_transcript_get(app_data: dict, offer_db_id: int) -> list[dict]:
    _ = app_data
    return negotiation_transcript_list(int(offer_db_id))


def neg_transcript_append(app_data: dict, offer_db_id: int, from_role: str, text: str) -> list[dict]:
    _ = app_data
    return negotiation_transcript_append_line(
        int(offer_db_id), from_role, text, max_lines=_NEG_MAX_LINES
    )


def _negotiation_focus_keyboard(offer_db_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✏️ پاسخ همین گفتگو", callback_data=f"neg_focus|{offer_db_id}")]]
    )


def _negotiation_panel_html(row: dict, entries: list[dict], viewer_is_owner: bool) -> str:
    aid = int(row["advert_rowid"])
    seq = int(row.get("seq_in_advert") or row["id"])
    alias = (row.get("offer_alias_name") or "").strip()
    tid = int(row.get("proposer_telegram_id") or 0)
    if alias:
        pname = _esc_html(alias)
    else:
        pname = _esc_html(_public_offer_name(get_user(tid), tid))
    lines_out: list[str] = []
    for e in entries:
        fr = (e.get("from") or "").strip().lower()
        you = (fr == "owner" and viewer_is_owner) or (fr == "proposer" and not viewer_is_owner)
        label = "شما" if you else "طرف مقابل"
        line_plain = f"{label}: {str(e.get('text') or '')}"
        lines_out.append(html_module.escape(line_plain))
    box_inner = "\n".join(lines_out) if lines_out else "—"
    return (
        f"{_RTL}🗣️ <b>مذاکره</b>\n"
        f"{_RTL}📋 آگهی <code>{aid}</code> · پیشنهاد دهنده: <b>{pname}</b> · شمارهٔ پیشنهاد: <code>{seq}</code>\n"
        f"<pre>{box_inner}</pre>\n"
        f"{_RTL}<i>پیام بفرستید؛ همین کادر به‌روز می‌شود.</i>"
    )


async def _sync_negotiation_panels(
    bot,
    store: dict,
    app_data: dict,
    row: dict,
    entries: list[dict],
) -> None:
    if not entries:
        return
    oid = int(row["id"])
    owner = int(row["owner_id"])
    proposer = int(row["proposer_telegram_id"])
    kb = _negotiation_focus_keyboard(oid)
    for uid, is_owner in ((owner, True), (proposer, False)):
        eu = list(entries)
        html = _negotiation_panel_html(row, eu, is_owner)
        while len(html) > 3900 and eu:
            eu.pop(0)
            html = _negotiation_panel_html(row, eu, is_owner)
        b = store.setdefault(int(uid), {})
        k = f"negp_{oid}"
        mid = b.get(k)
        try:
            if mid:
                await bot.edit_message_text(
                    chat_id=uid,
                    message_id=int(mid),
                    text=html,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
            else:
                sent = await bot.send_message(
                    chat_id=uid,
                    text=html,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
                b[k] = sent.message_id
                register_offer_thread_message(store, uid, oid, sent.message_id)
        except Exception:
            b.pop(k, None)
            try:
                sent = await bot.send_message(
                    chat_id=uid,
                    text=html,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
                b[k] = sent.message_id
                register_offer_thread_message(store, uid, oid, sent.message_id)
            except Exception:
                pass


async def _negotiation_start_compose(
    context: ContextTypes.DEFAULT_TYPE,
    offer_id: int,
    uid: int,
    chat_id: int,
) -> None:
    oid = int(offer_id)
    ids = _neg_offer_ids_as_set(context.user_data)
    ids.add(oid)
    _neg_offer_ids_write(context.user_data, ids)
    context.user_data["neg_offer_id"] = oid
    context.user_data["state"] = UserState.NEGOTIATION.name
    prev_pm = context.user_data.pop("neg_prompt_mid", None)
    if prev_pm:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=int(prev_pm))
        except Exception:
            pass
    await remove_main_menu_anchor_message(
        context.bot, user_id=uid, store=user_data_store
    )
    try:
        prompt_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ انصراف", callback_data=f"neg_pc|{oid}")]]
        )
        pm = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"{_RTL}✏️ لطفاً <b>پیام خود را همین‌جا بنویسید</b> و ارسال کنید.\n"
                f"{_RTL}<i>بعد از ارسال این پیام پاک می‌شود و منوی اصلی دوباره نمایش داده می‌شود.</i>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=prompt_kb,
        )
        context.user_data["neg_prompt_mid"] = pm.message_id
    except Exception:
        pass


async def handle_neg_focus_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    m = re.match(r"^neg_focus\|(\d+)$", q.data or "")
    if not m:
        return
    oid = int(m.group(1))
    row = get_advert_offer_joined(oid)
    if not row:
        await q.answer("پیشنهاد پیدا نشد.", show_alert=True)
        return
    uid = q.from_user.id
    if uid not in (int(row["owner_id"]), int(row["proposer_telegram_id"])):
        await q.answer()
        return
    st = (row.get("status") or "pending").strip().lower()
    if st != "pending":
        await q.answer("این گفتگو دیگر فعال نیست.", show_alert=True)
        return
    await q.answer("پاسخ روی همین پیشنهاد فعال شد.")
    await _negotiation_start_compose(context, oid, uid, q.message.chat_id)


async def handle_neg_send_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    m = re.match(r"^neg_send\|(\d+)$", q.data or "")
    if not m:
        return
    oid = int(m.group(1))
    if context.user_data.get("neg_gate_offer_id") != oid:
        await q.answer()
        return
    if context.user_data.get("neg_gate_mid") != q.message.message_id:
        await q.answer()
        return
    if context.user_data.get("state") != UserState.NEGOTIATION_GATE.name:
        await q.answer()
        return
    row = get_advert_offer_joined(oid)
    if not row or q.from_user.id not in (
        int(row["owner_id"]),
        int(row["proposer_telegram_id"]),
    ):
        await q.answer("پیشنهاد پیدا نشد.", show_alert=True)
        return
    st = (row.get("status") or "pending").strip().lower()
    if st != "pending":
        await q.answer("این گفتگو دیگر فعال نیست.", show_alert=True)
        return
    await q.answer("ارسال پیام فعال شد.")
    context.user_data.pop("neg_gate_mid", None)
    context.user_data.pop("neg_gate_offer_id", None)
    try:
        await q.message.delete()
    except Exception:
        pass
    await _negotiation_start_compose(
        context, oid, q.from_user.id, q.message.chat_id
    )


async def handle_neg_gate_cancel_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    m = re.match(r"^neg_gc\|(\d+)$", q.data or "")
    if not m:
        return
    oid = int(m.group(1))
    if context.user_data.get("neg_gate_offer_id") != oid:
        await q.answer()
        return
    if context.user_data.get("neg_gate_mid") != q.message.message_id:
        await q.answer()
        return
    cid = q.message.chat_id
    uid = q.from_user.id
    await q.answer("انصراف")
    context.user_data.pop("neg_gate_mid", None)
    context.user_data.pop("neg_gate_offer_id", None)
    context.user_data["state"] = UserState.MAIN_MENU.name
    try:
        await q.message.delete()
    except Exception:
        pass
    await send_or_replace_main_menu(
        context.bot,
        chat_id=cid,
        user_id=uid,
        store=user_data_store,
    )


async def handle_neg_prompt_cancel_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    m = re.match(r"^neg_pc\|(\d+)$", q.data or "")
    if not m:
        return
    oid = int(m.group(1))
    if context.user_data.get("neg_offer_id") != oid:
        await q.answer()
        return
    if context.user_data.get("neg_prompt_mid") != q.message.message_id:
        await q.answer()
        return
    cid = q.message.chat_id
    uid = q.from_user.id
    await q.answer("انصراف")
    context.user_data.pop("neg_prompt_mid", None)
    context.user_data.pop("neg_offer_id", None)
    context.user_data["state"] = UserState.MAIN_MENU.name
    try:
        await q.message.delete()
    except Exception:
        pass
    await send_or_replace_main_menu(
        context.bot,
        chat_id=cid,
        user_id=uid,
        store=user_data_store,
    )


def _scrub_for_anonymous_peer(text: str) -> str:
    """حذفٔ تقریبی آیدی/لینک/شماره از متنی که به طرف مقابل در مذاکره می‌رسد."""
    s = text or ""
    s = re.sub(r"(?i)@[a-z][a-z0-9_]{3,31}", "[…]", s)
    s = re.sub(r"(?i)https?://t\.me/\S+", "[…]", s)
    s = re.sub(r"(?i)\bt\.me/\S+", "[…]", s)
    s = re.sub(r"\d{10,}", "[…]", s)
    return s.strip()


async def _offer_flow_main_menu_anchor(
    bot, *, chat_id: int, user_id: int
) -> None:
    """بعد از ورود به فلو پیشنهاد، منوی ریپلای پایین صفحه را دوباره ست می‌کند."""
    await send_or_replace_main_menu(
        bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        text="🏠 منوی اصلی (در ادامهٔ ثبت پیشنهاد همین دکمه‌ها فعال است):",
    )


def _one_offer_rate_line(o: dict) -> str:
    hybrid = bool(o.get("skips_toman_rate_offer"))
    rt = int(o.get("rate_toman") or 0)
    if hybrid and rt == 0:
        return "معاوضهٔ یورو به یورو (بدون نرخ تومان)"
    return f"نرخ {rt:,} تومان"


def _format_my_offers_list_text(sent: list[dict] | None = None) -> str:
    """فقط پیشنهادهایی که کاربر به آگهی‌های دیگران فرستاده و هنوز pending است."""
    sent = sent or []
    lines = [f"{_RTL}📋 {MY_OFFERS_SENTINEL}", ""]

    if sent:
        lines.append(f"{_RTL}📤 پیشنهادهایی که شما فرستاده‌اید (در انتظار تأیید آگهی‌دهنده):")
        for o in sent:
            rate_part = _one_offer_rate_line(o)
            lines.append(
                f"{_RTL}• آگهی #{o['advert_rowid']} — پیشنهاد {o['seq_in_advert']} — {rate_part}"
            )
        lines.append("")
        lines.append(
            f"{_RTL}با دکمه‌ها پیشنهاد را حذف کنید؛ ویرایش نرخ فقط برای پیشنهادهای با نرخ تومان است."
        )

    if not sent:
        return f"{_RTL}📋 {MY_OFFERS_SENTINEL}\n"
    return "\n".join(lines)


def _my_offers_inline_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    keyboard = []
    for o in rows:
        aid = int(o["advert_rowid"])
        row_btns = [
            InlineKeyboardButton(
                f"🗑 آگهی {aid}",
                callback_data=f"offer_del|{o['id']}",
            ),
        ]
        if not (bool(o.get("skips_toman_rate_offer")) and int(o.get("rate_toman") or 0) == 0):
            row_btns.append(
                InlineKeyboardButton("✏️ نرخ", callback_data=f"offer_edit|{o['id']}")
            )
        keyboard.append(row_btns)
    keyboard.append([InlineKeyboardButton("✖️ بستن", callback_data="my_offers_close")])
    return InlineKeyboardMarkup(keyboard)


async def _admin_my_offers_preflight_if_needed(
    uid: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if uid not in set(ADMIN_IDS or []):
        return
    from handlers import admin as adm

    adm._admin_reset_subflow_keys(context)
    adm._clear_admin_pending(uid)
    ids = user_data_store.setdefault(uid, {}).pop(adm._ADMIN_CLEANUP_KEY, [])
    await cleanup_ids(context.bot, chat_id=chat_id, ids=ids)
    ids_u = user_data_store.setdefault(uid, {}).pop(adm._ADMIN_USER_INPUT_KEY, [])
    await cleanup_ids(context.bot, chat_id=chat_id, ids=ids_u)
    await remove_main_menu_anchor_message(context.bot, user_id=uid, store=user_data_store)
    context.user_data["state"] = UserState.MAIN_MENU.name


async def _present_my_pending_offers_list(
    bot,
    *,
    chat_id: int,
    uid: int,
    menu_inline_message: Message | None,
) -> bool:
    """اگر پیام لیست پیشنهادهای ارسالی ارسال شود True."""
    try:
        sent_rows = list_my_pending_offers_all(uid)
    except Exception:
        sent_rows = []

    if not sent_rows:
        if menu_inline_message:
            try:
                await menu_inline_message.delete()
            except Exception:
                pass
        await send_or_replace_main_menu(
            bot,
            chat_id=chat_id,
            user_id=uid,
            store=user_data_store,
            text=f"{_RTL}الان پیشنهاد ارسالی در انتظار تأیید ندارید.",
        )
        return False
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=_format_my_offers_list_text(sent_rows),
            reply_markup=_my_offers_inline_keyboard(sent_rows),
            disable_web_page_preview=True,
        )
    except Exception:
        await send_or_replace_main_menu(
            bot,
            chat_id=chat_id,
            user_id=uid,
            store=user_data_store,
            text=f"{_RTL}نمایش لیست پیشنهادها انجام نشد؛ دوباره «پیشنهادهای من» را بزنید.",
        )
        return False
    if menu_inline_message:
        try:
            await menu_inline_message.delete()
        except Exception:
            pass
    return True


async def handle_my_offers_reply_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """دکمهٔ ریپلای «پیشنهادهای من» — قبلاً هیچ مسیری آن را نمی‌گرفت."""
    m = update.message
    if not m or not update.effective_user:
        return
    uid = update.effective_user.id
    chat_id = m.chat_id
    admin_ids = set(ADMIN_IDS or [])

    if uid not in admin_ids and get_user(uid) is None:
        await m.reply_text("ابتدا ثبت‌نام کنید.")
        return

    await _admin_my_offers_preflight_if_needed(uid, chat_id, context)
    context.user_data["state"] = UserState.MAIN_MENU.name

    try:
        await m.delete()
    except Exception:
        pass

    sent_list = await _present_my_pending_offers_list(
        context.bot, chat_id=chat_id, uid=uid, menu_inline_message=None
    )
    if sent_list:
        await remove_main_menu_anchor_message(
            context.bot, user_id=uid, store=user_data_store
        )


async def handle_my_offers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    if normalize_telegram_callback_data(q.data) != "main_offers":
        return
    uid = q.from_user.id
    chat_id = q.message.chat_id if q.message else uid
    admin_ids = set(ADMIN_IDS or [])

    if uid not in admin_ids and get_user(uid) is None:
        try:
            await q.answer("ابتدا ثبت‌نام کنید.", show_alert=True)
        except Exception:
            pass
        return

    # حتماً زود answer شود تا «Loading…» در کلاینت نماند (قبل از هر I/O سنگین)
    try:
        await q.answer()
    except Exception:
        pass

    try:
        await _admin_my_offers_preflight_if_needed(uid, chat_id, context)
    except Exception:
        pass

    try:
        await _present_my_pending_offers_list(
            context.bot,
            chat_id=chat_id,
            uid=uid,
            menu_inline_message=q.message,
        )
    except Exception:
        try:
            await send_or_replace_main_menu(
                context.bot,
                chat_id=chat_id,
                user_id=uid,
                store=user_data_store,
                text=f"{_RTL}نمایش لیست پیشنهادها انجام نشد؛ دوباره «پیشنهادهای من» را بزنید.",
            )
        except Exception:
            pass


async def handle_my_offers_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    if normalize_telegram_callback_data(q.data) != "my_offers_close":
        return
    uid = q.from_user.id
    chat_id = q.message.chat_id if q.message else uid
    try:
        await q.answer()
    except Exception:
        pass
    if q.message:
        try:
            await q.message.delete()
        except Exception:
            pass
    await send_or_replace_main_menu(
        context.bot,
        chat_id=chat_id,
        user_id=uid,
        store=user_data_store,
    )


async def handle_offer_proposer_edit_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    m = re.match(r"^offer_edit\|(\d+)$", q.data or "")
    if not m:
        return
    oid = int(m.group(1))
    row = get_advert_offer_joined(oid)
    uid = q.from_user.id
    if not row or int(row.get("proposer_telegram_id") or 0) != uid:
        await q.answer("دسترسی ندارید.", show_alert=True)
        return
    st = (row.get("status") or "pending").strip().lower()
    if st != "pending":
        await q.answer("این پیشنهاد قابل ویرایش نیست.", show_alert=True)
        return
    aid = int(row.get("advert_rowid") or 0)
    advert_ed = get_euro_advert_by_rowid(aid)
    if _offer_skips_toman_rate_step(advert_ed) and int(row.get("rate_toman") or 0) == 0:
        await q.answer(
            "برای معاوضهٔ یورو به یورو نرخ تومان ثبت نمی‌شود؛ برای تغییر متن از «ارسال مجدد پیشنهاد» در کارت آگهی استفاده کنید.",
            show_alert=True,
        )
        return
    await q.answer()
    context.user_data["offer_edit_id"] = oid
    context.user_data["state"] = UserState.OFFER_EDIT_RATE.name
    seq = int(row.get("seq_in_advert") or row.get("id") or 0)
    await context.bot.send_message(
        chat_id=uid,
        text=(
            f"{_RTL}✏️ ویرایش نرخ پیشنهاد <b>{seq}</b> روی آگهی <b>{aid}</b>\n"
            f"{_RTL}نرخ جدید را به تومان بفرستید (فقط عدد).\n"
            f"{_RTL}برای انصراف: /menu"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_inline_keyboard,
    )


async def handle_offer_edit_rate_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.message:
        return
    oid_raw = context.user_data.get("offer_edit_id")
    if not isinstance(oid_raw, int):
        context.user_data.pop("offer_edit_id", None)
        context.user_data["state"] = UserState.MAIN_MENU.name
        raise ApplicationHandlerStop
    user_id = update.effective_user.id
    row = get_advert_offer_joined(oid_raw)
    if not row or int(row.get("proposer_telegram_id") or 0) != user_id:
        context.user_data.pop("offer_edit_id", None)
        context.user_data["state"] = UserState.MAIN_MENU.name
        raise ApplicationHandlerStop
    st = (row.get("status") or "pending").strip().lower()
    if st != "pending":
        context.user_data.pop("offer_edit_id", None)
        context.user_data["state"] = UserState.MAIN_MENU.name
        await update.message.reply_text(
            f"{_RTL}این پیشنهاد دیگر قابل ویرایش نیست.",
            reply_markup=main_menu_inline_keyboard,
        )
        raise ApplicationHandlerStop
    aid_advert = int(row["advert_rowid"])
    advert_h = get_euro_advert_by_rowid(aid_advert)
    if _offer_skips_toman_rate_step(advert_h) and int(row.get("rate_toman") or 0) == 0:
        context.user_data.pop("offer_edit_id", None)
        context.user_data["state"] = UserState.MAIN_MENU.name
        await update.message.reply_text(
            f"{_RTL}برای معاوضهٔ یورو به یورو نرخ تومان ویرایش نمی‌شود.",
            reply_markup=main_menu_inline_keyboard,
        )
        raise ApplicationHandlerStop
    rate = _parse_int_toman(update.message.text or "")
    if rate is None or rate <= 0:
        await update.message.reply_text(
            f"{_RTL}❌ لطفاً یک عدد تومان معتبر (بزرگ‌تر از صفر) بفرستید.",
            reply_markup=main_menu_inline_keyboard,
        )
        raise ApplicationHandlerStop
    cur_rate = int(row.get("rate_toman") or 0)
    if cur_rate != rate and proposer_offer_rate_exists_other_than(
        oid_raw, aid_advert, user_id, rate
    ):
        await update.message.reply_text(
            f"{_RTL}❌ با این نرخ پیشنهاد دیگری برای همین آگهی دارید. نرخ دیگری وارد کنید.",
            reply_markup=main_menu_inline_keyboard,
        )
        raise ApplicationHandlerStop
    advert_edit = get_euro_advert_by_rowid(aid_advert)
    if advert_edit:
        rej_err = _offer_rate_after_rejection_error(
            advert_edit, rate, proposer_telegram_id=user_id
        )
        if rej_err:
            await update.message.reply_text(
                rej_err,
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_inline_keyboard,
            )
            raise ApplicationHandlerStop
    adv_id = update_proposer_pending_offer_rate(oid_raw, user_id, rate)
    if not adv_id:
        context.user_data.pop("offer_edit_id", None)
        context.user_data["state"] = UserState.MAIN_MENU.name
        await update.message.reply_text(
            f"{_RTL}❌ ویرایش انجام نشد.",
            reply_markup=main_menu_inline_keyboard,
        )
        raise ApplicationHandlerStop
    try:
        await update.message.delete()
    except Exception:
        pass
    context.user_data.pop("offer_edit_id", None)
    context.user_data["state"] = UserState.MAIN_MENU.name
    await refresh_advert_channel_post(context.bot, adv_id)
    await refresh_offer_notification_cards_after_rate_change(
        context.bot,
        user_data_store,
        offer_db_id=oid_raw,
        advert_rowid=adv_id,
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            f"{_RTL}✅ نرخ پیشنهاد به <b>{rate:,}</b> تومان به‌روز شد.\n"
            f"{_RTL}لیست را از «📋 پیشنهادهای من» ببینید."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_inline_keyboard,
    )
    raise ApplicationHandlerStop

# جلوگیری از پیام دوباره وقتی کلاینت تلگرام دو بار پشت‌سرهم /start با همان payload می‌فرستد.
_OWNER_BLOCK_DEDUP_TTL_SEC = 4.0
_BOT_DATA_OWNER_BLOCK_DEDUP = "_offer_owner_block_dedup_monotonic"


def _should_skip_duplicate_owner_block(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, advert_id: int
) -> bool:
    bucket: dict[tuple[int, int], float] = context.application.bot_data.setdefault(
        _BOT_DATA_OWNER_BLOCK_DEDUP, {}
    )
    now = time.monotonic()
    key = (int(user_id), int(advert_id))
    expired = [k for k, t in bucket.items() if now - t > _OWNER_BLOCK_DEDUP_TTL_SEC]
    for k in expired:
        bucket.pop(k, None)
    prev = bucket.get(key)
    if prev is not None and (now - prev) < _OWNER_BLOCK_DEDUP_TTL_SEC:
        return True
    bucket[key] = now
    return False


def _esc_html(s: str) -> str:
    return html_module.escape(s or "", quote=False)


def _offer_requires_proposer_bank_country(advert: dict) -> bool:
    """معاوضه و حالت Euro-to-Euro بدون این مرحله؛ خرید/فروش معمولی با کشور حساب در آگهی."""
    op = (advert.get("operation") or "").strip()
    if op == "معاوضه":
        return False
    if int(advert.get("euro_exchange") or 0) == 1:
        return False
    return True


def _offer_country_display_text(raw) -> str:
    c = (raw or "").strip()
    if not c or c in ("—", "-", "–"):
        return "—"
    return _esc_html(c)


def _offer_euro_buyer_seller_country_texts(
    advert: dict, proposer_bank_country: str | None
) -> tuple[str, str]:
    """
    کشور حساب بانکی خریدار و فروشندهٔ یورو.
    آگهی «خرید» → آگهی‌دهنده خریدار، پیشنهاددهنده فروشنده.
    آگهی «فروش» → برعکس.
    """
    op = (advert.get("operation") or "").strip()
    adv = _offer_country_display_text(advert.get("account_country"))
    prop = _offer_country_display_text(proposer_bank_country)
    if op == "خرید":
        return adv, prop
    if op == "فروش":
        return prop, adv
    return prop, adv


def _offer_bank_country_lines_html(advert: dict, proposer_bank_country: str | None) -> str:
    if not _offer_requires_proposer_bank_country(advert):
        return ""
    buyer, seller = _offer_euro_buyer_seller_country_texts(advert, proposer_bank_country)
    return (
        f"{_RTL}🏦 <b>کشور حساب بانکی خریدار یورو:</b> {buyer}\n"
        f"{_RTL}🏦 <b>کشور حساب بانکی فروشنده یورو:</b> {seller}\n\n"
    )


def _ltr_rate_toman_html(rate: int) -> str:
    """نرخ و «تومان» در بلوک چپ‌به‌راست تا در متن راست‌به‌چپ کنار هم بمانند."""
    return f"\u202a<b>{int(rate):,}</b> تومان\u202c"


def _offer_rate_after_rejection_error(
    advert: dict | None, new_rate: int, *, proposer_telegram_id: int | None = None
) -> str | None:
    """برای آگهی خرید/فروش با نرخ تومان: محدودیت نسبت به آخرین پیشنهاد رد‌شده (معاوضهٔ یورو به یورو مستثنی). ادمین بدون خطا."""
    if not advert:
        return None
    if _offer_skips_toman_rate_step(advert):
        return None
    if proposer_telegram_id is not None and int(proposer_telegram_id) in set(ADMIN_IDS or []):
        return None
    op = (advert.get("operation") or "").strip()
    if op not in ("خرید", "فروش"):
        return None
    try:
        aid = int(advert.get("rowid") or advert.get("id") or 0)
    except (TypeError, ValueError):
        return None
    if aid <= 0:
        return None
    if proposer_telegram_id is None:
        return None
    last = get_last_rejected_offer_rate_toman(
        aid, proposer_telegram_id=int(proposer_telegram_id)
    )
    if last is None:
        return None
    # فروش: نرخ پایین‌تر از پیشنهاد رد‌شده مجاز نیست (حداقل همان سطح یا بالاتر).
    if op == "فروش":
        if new_rate < last:
            return (
                f"{_RTL}❌ برای آگهی <b>فروش</b>، بعد از رد پیشنهاد، نرخ پیشنهادی باید "
                f"حداقل <b>{last:,}</b> تومان باشد؛ نرخ <b>پایین‌تر</b> از این مجاز نیست."
            )
        return None
    # خرید: باید از آخرین نرخ رد‌شدهٔ همین پیشنهاددهنده پایین‌تر باشد (مساوی هم مجاز نیست).
    if new_rate >= last:
        return (
            f"{_RTL}❌ برای آگهی <b>خرید</b>، بعد از رد پیشنهاد، نرخ جدید باید "
            f"از <b>{last:,}</b> تومان <b>پایین‌تر</b> باشد (نرخ مساوی یا بالاتر مجاز نیست)."
        )
    return None


def _owner_offer_card_and_kb(
    advert: dict,
    *,
    aid: int,
    offer_row_id: int,
    seq: int,
    rate: int,
    description: str,
    public_display_name: str,
    proposer_fallback_id: int,
    proposer_bank_country: str | None = None,
    proposed_euro_amount: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    eur_amt = _offer_effective_euro_amount(advert, proposed_euro_amount)
    owner_fin, _ = _financial_blocks_html(advert, rate, eur_amt)
    dsc = (description or "").strip()
    pname = (public_display_name or "").strip() or str(int(proposer_fallback_id))
    esc_name = _esc_html(pname)
    esc_dsc = _esc_html(dsc)
    bank_lines = _offer_bank_country_lines_html(advert, proposer_bank_country)
    ad_tag = advert_public_link_html(advert, aid)
    amt_line = _offer_amount_line_html(advert, proposed_euro_amount)
    counter = (
        proposed_euro_amount is not None
        and _advert_euro_amount_int(advert) > 0
        and _offer_effective_euro_amount(advert, proposed_euro_amount)
        != _advert_euro_amount_int(advert)
    )
    if _offer_skips_toman_rate_step(advert) and int(rate) == 0:
        head = (
            f"❇️ پیشنهاد <b>معاوضهٔ یورو به یورو</b> توسط <b>{esc_name}</b> "
            f"برای {ad_tag} درخواست شده است.\n\n"
        )
    elif counter:
        head = (
            f"❇️ پیشنهاد با <b>مقدار/شرایط جدید</b> توسط <b>{esc_name}</b> "
            f"برای {ad_tag} درخواست شده است.\n\n"
        )
    else:
        head = (
            f"❇️ پیشنهاد جدید با قیمت {_ltr_rate_toman_html(rate)} توسط <b>{esc_name}</b> "
            f"برای {ad_tag} درخواست شده است.\n\n"
        )
    owner_text = (
        f"{head}"
        f"{amt_line}"
        f"📌 شماره پیشنهاد (در این آگهی): <b>{seq}</b>\n"
        f"📩 پیام پیشنهاد دهنده (محرمانه — فقط برای شما):\n{esc_dsc}\n\n"
        f"{bank_lines}"
        f"{owner_fin}"
        f"لطفاً پاسخ مورد نظر خود را انتخاب نمایید:"
    )
    owner_kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ موافقم", callback_data=f"adv_o|ok|{offer_row_id}")],
            [InlineKeyboardButton("⭕️ مخالفم", callback_data=f"adv_o|no|{offer_row_id}")],
            [InlineKeyboardButton("🗣️ مذاکره", callback_data=f"adv_o|neg|{offer_row_id}")],
            [
                InlineKeyboardButton(
                    "✅ دریافت پیام از پیشنهاد دهنده:",
                    callback_data=f"adv_o|msg|{offer_row_id}",
                )
            ],
        ]
    )
    return owner_text, owner_kb


def _proposer_recv_card_and_kb(
    advert: dict,
    *,
    aid: int,
    offer_row_id: int,
    seq: int,
    rate: int,
    description: str,
    is_admin_proxy: bool,
    esc_name: str,
    esc_dsc: str,
    proposer_bank_country: str | None = None,
    proposed_euro_amount: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    eur_amt = _offer_effective_euro_amount(advert, proposed_euro_amount)
    _, prop_fin = _financial_blocks_html(advert, rate, eur_amt)
    bank_lines = _offer_bank_country_lines_html(advert, proposer_bank_country)
    recv_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "ارسال مجدد پیشنهاد 🔄",
                    callback_data=f"offer_again|{aid}",
                ),
                InlineKeyboardButton(
                    "حذف پیشنهاد ❌",
                    callback_data=f"offer_del|{offer_row_id}",
                ),
            ],
            [InlineKeyboardButton("مذاکره ✉️", callback_data=f"offer_neg|{offer_row_id}")],
        ]
    )
    rate_show = (
        f"{_RTL}معاوضهٔ یورو به یورو (بدون نرخ تومان)"
        if _offer_skips_toman_rate_step(advert) and int(rate) == 0
        else _ltr_rate_toman_html(rate)
    )
    if is_admin_proxy:
        recv_text = (
            f"{_RTL}✅ <b>پیشنهاد نمایشی</b> ثبت شد.\n"
            f"{_RTL}نام نمایشی برای صاحب آگهی: <b>{esc_name}</b>\n"
            f"{_RTL}📌 شماره پیشنهاد (در این آگهی): <b>{seq}</b>\n"
            f"{_RTL}🧾 آگهی: <b>{aid}</b>\n"
            f"{_RTL}💰 نرخ: {rate_show}\n"
            f"{_RTL}📍 توضیحات: {esc_dsc}\n"
            f"{bank_lines}"
            f"{prop_fin}"
        ).rstrip()
    else:
        amt_line = _offer_amount_line_html(advert, proposed_euro_amount)
        recv_text = (
            f"📌 شماره پیشنهاد (در این آگهی): <b>{seq}</b>\n"
            f"🧾 پیشنهاد شما برای این حواله <b>{aid}</b> ارسال شد:\n"
            f"{amt_line}"
            f"💰 نرخ پیشنهادی شما: {rate_show}\n"
            f"📍 توضیحات: {esc_dsc}\n"
            f"{bank_lines}"
            f"{prop_fin}"
        ).rstrip()
    return recv_text, recv_kb


async def refresh_offer_notification_cards_after_rate_change(
    bot,
    store: dict,
    *,
    offer_db_id: int,
    advert_rowid: int,
) -> None:
    """
    پس از تغییر نرخ (ادمین یا پیشنهاددهنده): متن کارت اول صاحب آگهی و پیشنهاددهنده
    را با نرخ جدید و همان دکمه‌ها به‌روز می‌کند (اگر پیام هنوز در حافظهٔ ربات ثبت شده باشد).
    """
    oid = int(offer_db_id)
    aid = int(advert_rowid)
    row = get_advert_offer_joined(oid)
    if not row:
        return
    st = (row.get("status") or "pending").strip().lower()
    if st != "pending":
        return
    advert = get_euro_advert_by_rowid(aid)
    if not advert:
        return
    rate = int(row.get("rate_toman") or 0)
    if rate <= 0 and not _offer_skips_toman_rate_step(advert):
        return
    seq = int(row.get("seq_in_advert") or row.get("id") or oid)
    dsc = (row.get("description") or "").strip()
    proposer_id = int(row.get("proposer_telegram_id") or 0)
    alias = (row.get("offer_alias_name") or "").strip()
    if alias:
        pname_owner = alias
    else:
        pname_owner = _display_name_for_channel(get_user(proposer_id), proposer_id)
    esc_name = _esc_html((pname_owner or "").strip() or str(proposer_id))
    esc_dsc = _esc_html(dsc)
    pcb = (row.get("proposer_account_country") or "").strip() or None
    try:
        pe = int(row.get("proposed_euro_amount") or 0)
    except (TypeError, ValueError):
        pe = 0
    pe_kw = pe if pe > 0 else None

    owner_text, owner_kb = _owner_offer_card_and_kb(
        advert,
        aid=aid,
        offer_row_id=oid,
        seq=seq,
        rate=rate,
        description=dsc,
        public_display_name=pname_owner,
        proposer_fallback_id=proposer_id,
        proposer_bank_country=pcb,
        proposed_euro_amount=pe_kw,
    )
    owner_id = int(advert.get("user_id") or 0)
    if owner_id:
        mids = list(store.setdefault(owner_id, {}).get(f"ot_{oid}", []) or [])
        if mids:
            try:
                await bot.edit_message_text(
                    chat_id=owner_id,
                    message_id=int(mids[0]),
                    text=owner_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=owner_kb,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

    if proposer_id:
        recv_text, recv_kb = _proposer_recv_card_and_kb(
            advert,
            aid=aid,
            offer_row_id=oid,
            seq=seq,
            rate=rate,
            description=dsc,
            is_admin_proxy=bool(alias),
            esc_name=esc_name,
            esc_dsc=esc_dsc,
            proposer_bank_country=pcb,
            proposed_euro_amount=pe_kw,
        )
        pmids = list(store.setdefault(proposer_id, {}).get(f"ot_{oid}", []) or [])
        if pmids:
            try:
                await bot.edit_message_text(
                    chat_id=proposer_id,
                    message_id=int(pmids[0]),
                    text=recv_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=recv_kb,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass


def _pop_offer_draft_keys(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in (
        "offer_advert_id",
        "offer_draft_rate",
        "offer_draft_description",
        "offer_draft_account_country",
        "offer_draft_euro_amount",
        "offer_counter_mode",
    ):
        context.user_data.pop(k, None)


def _clear_offer_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    _pop_offer_draft_keys(context)
    context.user_data.pop("offer_flow_mids", None)
    context.user_data.pop("offer_flow_input_mids", None)
    context.user_data.pop("offer_flow_active_mid", None)
    context.user_data.pop("offer_flow_prompt_mid", None)
    context.user_data.pop("offer_flow_step", None)
    context.user_data["state"] = UserState.MAIN_MENU.name


_OFFER_FLOW_STEP_STATE: dict[str, UserState] = {
    "gate": UserState.OFFER_ADVERT_ID,
    "counter_euro": UserState.OFFER_COUNTER_EURO,
    "rate": UserState.OFFER_RATE,
    "account_country": UserState.OFFER_ACCOUNT_COUNTRY,
    "description": UserState.OFFER_DESCRIPTION,
    "preview": UserState.OFFER_PREVIEW,
}


async def _finish_offer_flow_abort(
    context: ContextTypes.DEFAULT_TYPE,
    bot,
    *,
    chat_id: int,
    user_id: int,
    preview_message_id: int | None = None,
    menu_text: str = "🏠 بازگشت به منوی اصلی:",
) -> None:
    """پاک کردن رد پیشنهاد، پیام‌های ردیابی‌شدهٔ فلوهای دیگر، state و نمایش منوی اصلی."""
    extra: list[int] = []
    if preview_message_id is not None:
        extra.append(int(preview_message_id))
    await cleanup_transient_dm_messages(
        bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        context_user_data=context.user_data,
        extra_message_ids=extra,
    )
    _clear_offer_flow(context)
    await send_or_replace_main_menu(
        bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        text=menu_text,
    )


def _offer_flow_track(context: ContextTypes.DEFAULT_TYPE, message_id: int | None) -> None:
    if not message_id:
        return
    xs = context.user_data.setdefault("offer_flow_mids", [])
    mid = int(message_id)
    if mid not in xs:
        xs.append(mid)
    context.user_data["offer_flow_active_mid"] = mid


def _offer_flow_track_input(
    context: ContextTypes.DEFAULT_TYPE, message_id: int | None
) -> None:
    """پیام متنی کاربر در هر مرحله — تا تأیید/انصراف در چت می‌ماند."""
    if not message_id:
        return
    xs = context.user_data.setdefault("offer_flow_input_mids", [])
    mid = int(message_id)
    if mid not in xs:
        xs.append(mid)


_OFFER_STEP_CANCEL_LABEL = "❌ انصراف از این مرحله"


async def _offer_flow_clear_prompt(
    context: ContextTypes.DEFAULT_TYPE, bot, *, chat_id: int
) -> None:
    mid = context.user_data.pop("offer_flow_prompt_mid", None)
    if not mid:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=int(mid))
    except Exception:
        pass


async def _offer_flow_ack(
    context: ContextTypes.DEFAULT_TYPE,
    bot,
    *,
    chat_id: int,
    text: str,
) -> None:
    sent = await bot.send_message(chat_id=chat_id, text=text)
    _offer_flow_track(context, sent.message_id)


async def _offer_flow_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    bot,
    *,
    chat_id: int,
    step: str,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """سؤال مرحلهٔ بعد — پرامپت قبلی حذف می‌شود."""
    st = _OFFER_FLOW_STEP_STATE.get(step)
    if st:
        context.user_data["offer_flow_step"] = step
        context.user_data["state"] = st.name
    await _offer_flow_clear_prompt(context, bot, chat_id=chat_id)
    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    _offer_flow_track(context, sent.message_id)
    context.user_data["offer_flow_prompt_mid"] = sent.message_id


async def _offer_flow_advance(
    context: ContextTypes.DEFAULT_TYPE,
    bot,
    *,
    chat_id: int,
    ack_text: str,
    step: str,
    prompt_html: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """پس از ورودی کاربر: تأیید ✅، سپس سؤال مرحلهٔ بعد."""
    await _offer_flow_clear_prompt(context, bot, chat_id=chat_id)
    await _offer_flow_ack(context, bot, chat_id=chat_id, text=ack_text)
    await _offer_flow_prompt(
        context,
        bot,
        chat_id=chat_id,
        step=step,
        text=prompt_html,
        reply_markup=reply_markup,
    )


async def _offer_flow_mark_gate_message(
    query,
    advert: dict,
    *,
    footer_html: str,
) -> None:
    """نمونه آگهی در بالا بماند؛ دکمه‌ها برداشته و وضعیت انتخاب ثبت شود."""
    if not query.message:
        return
    try:
        await query.edit_message_text(
            f"{build_offer_gate_html(advert)}\n\n{footer_html}",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except Exception:
        pass


def _build_offer_rate_prompt_html(
    advert: dict, proposer_id: int, *, counter_mode: bool
) -> str:
    if counter_mode:
        return build_offer_counter_rate_step_html(advert)
    return build_offer_rate_step_html(advert, proposer_id)


def _proposer_same_rate_blocked(
    advert_rowid: int, proposer_telegram_id: int, rate_toman: int
) -> bool:
    """همان نرخ فقط اگر پیشنهاد pending دیگری برای جایگزینی نیست ممنوع است."""
    if not proposer_offer_rate_exists(advert_rowid, proposer_telegram_id, rate_toman):
        return False
    return not proposer_has_pending_offer_on_advert(advert_rowid, proposer_telegram_id)


def _message_looks_like_toman_amount(text: str) -> bool:
    raw = (text or "").strip().replace(",", "").replace(" ", "")
    if not raw or not raw.isdigit():
        return False
    try:
        return int(raw) > 0
    except ValueError:
        return False


def _fmt_fee_eur_display(fee_eur: float) -> str:
    s = f"{fee_eur:.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _fee_eur_line_for_party(fee_eur: float, advert: dict | None = None) -> str:
    ov = advert_fee_override_eur(advert) if advert else None
    if fee_eur <= 0:
        if ov is not None and abs(float(ov)) < 1e-12:
            return f"• کارمزد شما (یورو): <b>{_fmt_fee_eur_display(0.0)}</b> یورو\n"
        return ""
    return f"• کارمزد شما (یورو): <b>{_fmt_fee_eur_display(fee_eur)}</b> یورو\n"


def _financial_blocks_html(advert: dict, rate: int, eur_amt: int) -> tuple[str, str]:
    """خلاصهٔ مالی؛ کارمزد یورو/تومان برای هر طرف برابر مبلغ پلکانی/دستی (بدون نصف کردن)."""
    op = (advert.get("operation") or "").strip()
    try:
        ea = int(eur_amt)
    except (TypeError, ValueError):
        ea = 0
    try:
        rt = int(rate)
    except (TypeError, ValueError):
        rt = 0
    euro_ex = int(advert.get("euro_exchange") or 0) == 1
    if (op == "معاوضه" or (euro_ex and op in ("خرید", "فروش"))) and rt == 0 and ea > 0:
        ov = advert_fee_override_eur(advert)
        fee_eur = fee_total_eur(ea, ov)
        eur_ln = _fee_eur_line_for_party(fee_eur, advert)
        blk = (
            f"🧮 <b>خلاصه (معاوضهٔ یورو به یورو — بدون نرخ تومان ثابت)</b>\n"
            f"• مقدار یورو: <b>{ea:,}</b>\n"
            f"{eur_ln}"
            f"• نرخ تومانی برای این معاوضه در پیشنهاد ثبت نشد؛ شرایط در توضیحات آمده است.\n\n"
        )
        return blk, blk
    base = int(eur_amt) * int(rate)
    ov = advert_fee_override_eur(advert)
    fee_eur = fee_total_eur(eur_amt, ov)
    fee_party_toman = int(round(fee_eur * float(rate)))
    eur_ln = _fee_eur_line_for_party(fee_eur, advert)
    if op not in ("خرید", "فروش") or eur_amt <= 0:
        gen = (
            f"🧮 نرخ: <b>{rate:,}</b> تومان — مقدار یورو: <b>{eur_amt:,}</b> — "
            f"جمع پایه: <b>{base:,}</b> تومان\n"
        )
        if fee_eur > 0:
            gen += f"🧾 کارمزد شما (یورو): <b>{_fmt_fee_eur_display(fee_eur)}</b> (تقریبی)\n"
            gen += f"🧾 کارمزد شما (تومان، تقریبی): <b>{fee_party_toman:,}</b> تومان\n"
        elif ov is not None and fee_eur == 0:
            gen += f"🧾 کارمزد شما (یورو): <b>0</b> (ثابت ادمین)\n"
            gen += f"🧾 کارمزد شما (تومان، تقریبی): <b>{fee_party_toman:,}</b> تومان\n"
        return gen + "\n", gen + "\n"

    if op == "فروش":
        owner_net = base - fee_party_toman
        buyer_pay = base + fee_party_toman
        owner_blk = (
            f"🧮 <b>خلاصه برای شما (آگهی‌دهنده — فروشنده یورو):</b>\n"
            f"• نرخ: <b>{rate:,}</b> تومان\n"
            f"• مقدار یورو: <b>{eur_amt:,}</b>\n"
            f"{eur_ln}"
            f"• کارمزد شما (تومان): <b>{fee_party_toman:,}</b> تومان\n"
            f"• مبلغ نهایی (بعد از کسر کارمزد — مبلغی که برای شما واریز می‌شود): "
            f"<b>{owner_net:,}</b> تومان\n\n"
        )
        prop_blk = (
            f"🧮 <b>خلاصه برای شما (پیشنهاد دهنده — خریدار یورو):</b>\n"
            f"• نرخ: <b>{rate:,}</b> تومان\n"
            f"• مقدار یورو: <b>{eur_amt:,}</b>\n"
            f"{eur_ln}"
            f"• کارمزد شما (تومان): <b>{fee_party_toman:,}</b> تومان\n"
            f"• مبلغ نهایی (با جمع کارمزد — مبلغ واریز شما): "
            f"<b>{buyer_pay:,}</b> تومان\n\n"
        )
        return owner_blk, prop_blk

    buyer_pay = base + fee_party_toman
    seller_recv = base - fee_party_toman
    owner_blk = (
        f"🧮 <b>خلاصه برای شما (آگهی‌دهنده — خریدار یورو):</b>\n"
        f"• نرخ: <b>{rate:,}</b> تومان\n"
        f"• مقدار یورو: <b>{eur_amt:,}</b>\n"
        f"{eur_ln}"
        f"• کارمزد شما (تومان): <b>{fee_party_toman:,}</b> تومان\n"
        f"• مبلغ نهایی (با جمع کارمزد — مبلغ واریز شما): "
        f"<b>{buyer_pay:,}</b> تومان\n\n"
    )
    prop_blk = (
        f"🧮 <b>خلاصه برای شما (پیشنهاد دهنده — فروشنده یورو):</b>\n"
        f"• نرخ: <b>{rate:,}</b> تومان\n"
        f"• مقدار یورو: <b>{eur_amt:,}</b>\n"
        f"{eur_ln}"
        f"• کارمزد شما (تومان): <b>{fee_party_toman:,}</b> تومان\n"
        f"• مبلغ نهایی (بعد از کسر کارمزد — مبلغی که برای شما واریز می‌شود): "
        f"<b>{seller_recv:,}</b> تومان\n\n"
    )
    return owner_blk, prop_blk


def _post_acceptance_account_context_html(
    advert: dict, row: dict, viewer_telegram_id: int
) -> str:
    """کشور حساب و روش‌های پرداخت/دریافت؛ متن بر اساس خریدار یا فروشنده بودن بیننده."""
    op = (advert.get("operation") or "").strip()
    owner_id = int(row["owner_id"])
    proposer_id = int(row["proposer_telegram_id"])
    vid = int(viewer_telegram_id)
    is_owner = vid == owner_id

    adv_ct = (advert.get("account_country") or "").strip() or "—"
    prop_ct = (row.get("proposer_account_country") or "").strip() or "—"
    methods_raw = (advert.get("methods") or "").strip() or "—"

    adv_ct_e = html_module.escape(adv_ct)
    prop_ct_e = html_module.escape(prop_ct)
    methods_e = html_module.escape(methods_raw)

    lines: list[str] = [f"{_RTL}📋 <b>کشور حساب و روش‌های مرتبط با معامله</b>\n"]

    if op == "فروش":
        if is_owner:
            lines.append(
                f"{_RTL}شما <b>آگهی‌دهنده و فروشندهٔ یورو</b> هستید.\n"
                f"{_RTL}🏦 کشور حساب شما (ثبت در آگهی): <b>{adv_ct_e}</b>\n"
                f"{_RTL}💳 روش‌های دریافت وجه (ثبت در آگهی):\n<code>{methods_e}</code>\n"
            )
        else:
            lines.append(
                f"{_RTL}شما <b>پیشنهاددهنده و خریدار یورو</b> هستید.\n"
                f"{_RTL}🏦 کشور حساب شما (ثبت در پیشنهاد): <b>{prop_ct_e}</b>\n"
                f"{_RTL}🌍 کشور حساب <b>آگهی‌دهنده</b> (فروشنده): <b>{adv_ct_e}</b>\n"
                f"{_RTL}💳 برای پرداخت به فروشنده، از <b>روش‌های ثبت‌شده در آگهی</b> استفاده کنید:\n"
                f"<code>{methods_e}</code>\n"
            )
    elif op == "خرید":
        if is_owner:
            lines.append(
                f"{_RTL}شما <b>آگهی‌دهنده و خریدار یورو</b> هستید.\n"
                f"{_RTL}🏦 کشور حساب شما (ثبت در آگهی): <b>{adv_ct_e}</b>\n"
                f"{_RTL}💳 روش‌های پرداخت شما به فروشنده (ثبت در آگهی):\n<code>{methods_e}</code>\n"
            )
        else:
            lines.append(
                f"{_RTL}شما <b>پیشنهاددهنده و فروشندهٔ یورو</b> هستید.\n"
                f"{_RTL}🏦 کشور حساب شما (ثبت در پیشنهاد): <b>{prop_ct_e}</b>\n"
                f"{_RTL}🌍 کشور حساب <b>آگهی‌دهنده</b> (خریدار): <b>{adv_ct_e}</b>\n"
                f"{_RTL}💳 دریافت وجه از خریدار طبق <b>روش‌های ثبت‌شده در آگهی</b> اوست:\n"
                f"<code>{methods_e}</code>\n"
            )
    else:
        who = "آگهی‌دهنده" if is_owner else "پیشنهاددهنده"
        lines.append(
            f"{_RTL}نقش شما در این آگهی: <b>{who}</b> (نوع آگهی: <b>{html_module.escape(op or '—')}</b>)\n"
            f"{_RTL}🏦 کشور حساب آگهی‌دهنده (آگهی): <b>{adv_ct_e}</b>\n"
            f"{_RTL}🏦 کشور حساب پیشنهاددهنده (پیشنهاد): <b>{prop_ct_e}</b>\n"
            f"{_RTL}💳 روش‌ها در آگهی:\n<code>{methods_e}</code>\n"
        )

    return "\n".join(lines) + "\n"


def _post_acceptance_message_html(
    advert: dict,
    row: dict,
    viewer_telegram_id: int,
    seq: int,
    aid: int,
) -> str:
    """پیام تأیید معامله برای آگهی‌دهنده یا پیشنهاددهنده با خلاصه مالی و راهنمای ادمین."""
    rate = int(row["rate_toman"])
    try:
        eur_amt = int(advert.get("euro_amount") or 0)
    except (TypeError, ValueError):
        eur_amt = 0
    owner_id = int(row["owner_id"])
    proposer_id = int(row["proposer_telegram_id"])
    owner_blk, prop_blk = _financial_blocks_html(advert, rate, eur_amt)
    if int(viewer_telegram_id) == owner_id:
        fin = owner_blk
    else:
        fin = prop_blk
    ov = advert_fee_override_eur(advert)
    if ov is not None:
        fee_summary = (
            f"{_RTL}🧾 <b>خلاصهٔ مالی برای شما</b> "
            f"(کارمزد هر طرف، <b>ثابت (تنظیم ادمین)</b>: {format_fee_eur(eur_amt, ov)}):\n"
        )
    else:
        pe = fee_total_eur(eur_amt, None)
        fee_num = f"{pe:.2f}".rstrip("0").rstrip(".") if pe else "0"
        fee_summary = (
            f"{_RTL}🧾 <b>خلاصهٔ مالی برای شما</b> "
            f"(کارمزد تقریبی از <b>فرمول پلکانی</b>؛ حدود <b>{fee_num}</b> یورو):\n"
        )
    ad_link = advert_public_link_html(advert, aid)
    hdr = f"{_RTL}✅ پیشنهاد شماره <b>{seq}</b> برای {ad_link} تأیید شد.\n\n{fee_summary}"
    acct = _post_acceptance_account_context_html(advert, row, viewer_telegram_id)
    adm = (DEAL_NEXT_STEPS_ADMIN or "").strip()
    if adm:
        adm_esc = html_module.escape(adm)
        foot = (
            f"\n{_RTL}⚠️ بدون هماهنگی مدیریت <b>هیچ مبلغی</b> پرداخت یا واریز نکنید.\n"
            f"{_RTL}برای مراحل بعدی، <b>همین پیام را برای ادمین فوروارد کنید</b>: "
            f"<b>{adm_esc}</b>\n"
            f"{_RTL}📌 شناسهٔ حساب شما در تلگرام: <code>{viewer_telegram_id}</code>"
        )
    else:
        foot = (
            f"\n{_RTL}⚠️ بدون هماهنگی مدیریت <b>هیچ مبلغی</b> پرداخت یا واریز نکنید.\n"
            f"{_RTL}برای مراحل بعدی، همین پیام را به <b>ادمین</b> ارسال کنید.\n"
            f"{_RTL}📌 شناسهٔ حساب شما در تلگرام: <code>{viewer_telegram_id}</code>"
        )
    return hdr + fin + acct + foot


def _post_acceptance_reply_markup(advert: dict | None) -> InlineKeyboardMarkup | None:
    """دکمه‌های اینلاین بعد از تأیید پیشنهاد (لینک کانال + ورود به ربات)."""
    if not advert:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    ch = (CHANNEL_USERNAME or "").strip().lstrip("@")
    mid = advert.get("channel_message_id")
    if ch and mid is not None:
        try:
            rows.append(
                [
                    InlineKeyboardButton(
                        "📌 مشاهدهٔ آگهی در کانال",
                        url=f"https://t.me/{ch}/{int(mid)}",
                    )
                ]
            )
        except (TypeError, ValueError):
            pass
    un = (BOT_USERNAME or "").strip().lstrip("@")
    if un:
        rows.append([InlineKeyboardButton("🤖 باز کردن ربات", url=f"https://t.me/{un}")])
    return InlineKeyboardMarkup(rows) if rows else None


def _display_name_for_channel(user_row: dict | None, telegram_id: int) -> str:
    if user_row:
        dn = (user_row.get("display_name") or "").strip()
        if dn:
            return dn
    return _public_offer_name(user_row, telegram_id)


def _public_offer_name(user_row: dict | None, telegram_id: int) -> str:
    if not user_row:
        return str(telegram_id)
    for key in ("display_name", "full_name", "username"):
        v = user_row.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return str(telegram_id)


async def dispatch_offer_created_notifications(
    bot,
    *,
    advert_rowid: int,
    proposer_telegram_id: int,
    offer_row_id: int,
    offer_seq: int,
    rate_toman: int,
    description: str,
    public_display_name: str,
    is_admin_proxy: bool,
    proposer_account_country: str | None = None,
    skip_main_menu_refresh_for_proposer: bool = False,
) -> None:
    """
    پس از insert موفق پیشنهاد: نوتیف صاحب آگهی، تأیید برای پیشنهاددهنده (ادمین در حالت proxy)،
    و در حالت proxy اطلاع‌رسانی به سایر ادمین‌ها. به‌روزرسانی پست کانال.
    اگر skip_main_menu_refresh_for_proposer=True باشد (مثلاً بلافاصله قبل از _finish_offer_flow_abort)،
    برای پیشنهاددهندهٔ غیرادمین منوی اصلی دوباره ارسال نمی‌شود تا از دوباره‌کاری جلوگیری شود.
    """
    aid = int(advert_rowid)
    uid = int(proposer_telegram_id)
    rate = int(rate_toman)
    row_id = int(offer_row_id)
    seq = int(offer_seq)
    dsc = (description or "").strip()
    pname = (public_display_name or "").strip() or str(uid)
    advert = get_euro_advert_by_rowid(aid)
    if not advert:
        return
    owner_id = int(advert.get("user_id") or 0)
    esc_name = _esc_html(pname)
    esc_dsc = _esc_html(dsc)
    pcb = (proposer_account_country or "").strip() or None
    row_meta = get_advert_offer_joined(row_id)
    try:
        pe = int((row_meta or {}).get("proposed_euro_amount") or 0)
    except (TypeError, ValueError):
        pe = 0
    pe_kw = pe if pe > 0 else None

    owner_text, owner_kb = _owner_offer_card_and_kb(
        advert,
        aid=aid,
        offer_row_id=row_id,
        seq=seq,
        rate=rate,
        description=dsc,
        public_display_name=pname,
        proposer_fallback_id=uid,
        proposer_bank_country=pcb,
        proposed_euro_amount=pe_kw,
    )
    recv_text, recv_kb = _proposer_recv_card_and_kb(
        advert,
        aid=aid,
        offer_row_id=row_id,
        seq=seq,
        rate=rate,
        description=dsc,
        is_admin_proxy=is_admin_proxy,
        esc_name=esc_name,
        esc_dsc=esc_dsc,
        proposer_bank_country=pcb,
        proposed_euro_amount=pe_kw,
    )

    if owner_id:
        try:
            om = await bot.send_message(
                chat_id=owner_id,
                text=owner_text,
                parse_mode=ParseMode.HTML,
                reply_markup=owner_kb,
                disable_web_page_preview=True,
            )
            register_offer_thread_message(
                user_data_store, owner_id, row_id, om.message_id
            )
        except Exception:
            pass
        try:
            await send_or_replace_main_menu(
                bot,
                chat_id=owner_id,
                user_id=owner_id,
                store=user_data_store,
            )
        except Exception:
            pass

    if is_admin_proxy:
        rate_audit = (
            f"{_RTL}معاوضهٔ یورو به یورو (بدون نرخ تومان)"
            if _offer_skips_toman_rate_step(advert) and rate == 0
            else _ltr_rate_toman_html(rate)
        )
        audit = (
            f"{_RTL}ℹ️ <b>پیشنهاد نمایشی</b> توسط ادمین <code>{uid}</code> ثبت شد.\n"
            f"{_RTL}آگهی: <b>{aid}</b> — شماره پیشنهاد: <b>{seq}</b>\n"
            f"{_RTL}نام نمایشی (برای صاحب آگهی): <b>{esc_name}</b>\n"
            f"{_RTL}💰 نرخ: {rate_audit}\n"
            f"{_RTL}📍 توضیحات:\n{esc_dsc}"
        )
        try:
            pm = await bot.send_message(
                chat_id=uid,
                text=recv_text,
                parse_mode=ParseMode.HTML,
                reply_markup=recv_kb,
                disable_web_page_preview=True,
            )
            register_offer_thread_message(user_data_store, uid, row_id, pm.message_id)
        except Exception:
            pass
        try:
            await send_or_replace_main_menu(
                bot, chat_id=uid, user_id=uid, store=user_data_store
            )
        except Exception:
            pass
        admins = {int(x) for x in (ADMIN_IDS or []) if x is not None}
        admins.discard(uid)
        for other in admins:
            try:
                await bot.send_message(
                    chat_id=other,
                    text=audit,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
    else:
        try:
            pm = await bot.send_message(
                chat_id=uid,
                text=recv_text,
                parse_mode=ParseMode.HTML,
                reply_markup=recv_kb,
                disable_web_page_preview=True,
            )
            register_offer_thread_message(user_data_store, uid, row_id, pm.message_id)
        except Exception:
            pass
        if not skip_main_menu_refresh_for_proposer:
            try:
                await send_or_replace_main_menu(
                    bot, chat_id=uid, user_id=uid, store=user_data_store
                )
            except Exception:
                pass

    await refresh_advert_channel_post(bot, aid)


def _channel_offer_line_rtl(inner_html: str) -> str:
    """راست‌چین و جهت RTL برای خطوط پیشنهاد زیر آگهی در کانال (تلگرام HTML)."""
    return f"\u202b{_RTL}{inner_html}\u202c"


def _strip_channel_offer_block(html: str) -> str:
    """اگر متن کانال قبلاً بلوک وضعیت/پیشنهاد دارد، قبل از بازسازی حذفش کن (جلوگیری از ماندهٔ قدیمی)."""
    marker = "⚙️ <b>وضعیت:</b>"
    bot = "🤖 <b>ربات:"
    if marker not in html:
        return html
    i = html.find(marker)
    j = html.find(bot, i)
    if j < 0:
        return html
    before = html[:i].rstrip()
    tail = html[j:]
    return f"{before}\n{tail}" if before else tail


def append_offer_lists_to_channel_html(base_html: str, advert_rowid: int) -> str:
    base_html = _strip_channel_offer_block(base_html)
    advert_for_list = get_euro_advert_by_rowid(advert_rowid)
    hybrid_list = _offer_skips_toman_rate_step(advert_for_list)
    pending = list_pending_offers_for_advert(advert_rowid)
    rejected = list_rejected_offers_for_advert(advert_rowid)
    accepted = list_accepted_offers_for_advert(advert_rowid)
    agreement_html = ""
    if accepted:
        try:
            rt = int(accepted[0]["rate_toman"])
        except (TypeError, ValueError, KeyError):
            rt = 0
        if rt > 0:
            agreement_html = (
                f"{_RTL}✅ این آگهی با نرخ پیشنهادی {_ltr_rate_toman_html(rt)} به توافق رسیده است.\n\n"
            )
        elif hybrid_list and rt == 0:
            agreement_html = (
                f"{_RTL}✅ این آگهی با توافق «معاوضهٔ یورو به یورو» (بدون نرخ تومان ثابت در پیشنهاد) "
                f"به نتیجه رسیده است.\n\n"
            )
    merged: list[tuple[str, dict]] = (
        [("pending", r) for r in pending]
        + [("rejected", r) for r in rejected]
        + [("accepted", r) for r in accepted]
    )
    merged.sort(key=lambda t: int(t[1]["id"]))
    lines: list[str] = []
    for st, r in merged:
        rate = int(r["rate_toman"])
        seq = int(r.get("seq_in_advert") or r["id"])
        alias = (r.get("offer_alias_name") or "").strip()
        if alias:
            label = _esc_html(alias)
        else:
            u = get_user(int(r["proposer_telegram_id"]))
            label = _esc_html(_display_name_for_channel(u, int(r["proposer_telegram_id"])))
        pe = int(r.get("proposed_euro_amount") or 0)
        if st == "pending":
            prefix = "• "
        elif st == "rejected":
            prefix = "❌ "
        else:
            prefix = "✅ "
        inner = _channel_offer_line_inner_html(
            seq=seq,
            label=label,
            rate=rate,
            proposed_euro=pe,
            advert=advert_for_list,
            hybrid=hybrid_list,
            status_prefix=prefix,
        )
        lines.append(_channel_offer_line_rtl(inner))
    offers_body = "\n".join(lines) if lines else ""
    header = "📋 <b>پیشنهاد های ارسال شده</b>"
    parts: list[str] = []
    if offers_body:
        parts.append(f"{header}\n\n{offers_body}")
    if agreement_html:
        parts.append(agreement_html.rstrip())
    block = "\n\n".join(parts) if parts else ""
    if not block:
        return base_html
    status_needle = "⚙️ <b>وضعیت:</b>"
    if status_needle in base_html:
        idx = base_html.find(status_needle)
        line_end = base_html.find("\n", idx)
        if line_end < 0:
            line_end = len(base_html)
        rest = base_html[line_end + 1 :].lstrip("\n")
        return base_html[:idx].rstrip() + "\n\n" + block + "\n\n" + rest
    bot_m = "🤖 <b>ربات:"
    bi = base_html.rfind(bot_m)
    if bi >= 0:
        return base_html[:bi].rstrip() + "\n\n" + block + "\n\n" + base_html[bi:]
    return base_html + "\n\n" + block


async def refresh_advert_channel_post(bot, advert_rowid: int) -> None:
    from handlers import admin

    advert = get_euro_advert_by_rowid(advert_rowid)
    if not advert:
        return
    mid = advert.get("channel_message_id")
    cid = advert.get("channel_chat_id") or ADVERT_CHANNEL_ID
    if mid is None or cid is None:
        return
    try:
        cid_i = int(cid)
        mid_i = int(mid)
    except (TypeError, ValueError):
        return
    try:
        me = await bot.get_me()
        uname = (me.username or "").strip().lstrip("@")
        advert["bot_username"] = uname
        base = admin._build_channel_ad_text(advert)
        full = append_offer_lists_to_channel_html(base, advert_rowid)
        if list_accepted_offers_for_advert(int(advert_rowid)):
            rk = InlineKeyboardMarkup([])
        else:
            rk = InlineKeyboardMarkup(
                [[offer_proposal_inline_button(int(advert_rowid), uname)]]
            )
        await bot.edit_message_text(
            chat_id=cid_i,
            message_id=mid_i,
            text=full,
            parse_mode=ParseMode.HTML,
            reply_markup=rk,
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        try:
            await bot.edit_message_reply_markup(
                chat_id=cid_i,
                message_id=mid_i,
                reply_markup=rk,
            )
        except Exception:
            pass
    except Exception:
        pass


def parse_offer_start_payload(args: list[str]) -> int | None:
    """آرگومان /start مثلاً offer_69 → ۶۹."""
    if not args:
        return None
    m = re.match(r"^offer_(\d+)$", (args[0] or "").strip(), re.I)
    return int(m.group(1)) if m else None


def offer_proposal_inline_button(advert_id: int, bot_username: str | None = None) -> InlineKeyboardButton:
    """
    دکمه زیر آگهی در کانال: با URL تلگرام مستقیم چت ربات را باز می‌کند (deep link).
    اگر یوزرنیم نبود، fallback به callback برای پست‌های قدیمی.
    """
    label = "📨 پیشنهاد به آگهی"
    uname = (bot_username or "").strip().lstrip("@") or (BOT_USERNAME or "").strip().lstrip("@")
    if uname:
        return InlineKeyboardButton(
            label,
            url=f"https://t.me/{uname}?start=offer_{int(advert_id)}",
        )
    return InlineKeyboardButton(label, callback_data=f"offer_{int(advert_id)}")


async def deliver_offer_proposal_gate(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    advert_id: int,
) -> None:
    """ارسال پیام گیت پیشنهاد در چت خصوصی (مشترک برای deep link و callback)."""
    bot = context.bot
    if not get_user(user_id):
        from utils.telegram_utils import send_registration_welcome

        await send_registration_welcome(
            bot,
            chat_id=user_id,
            user_id=user_id,
            store=user_data_store,
            context=context,
        )
        return

    advert = get_euro_advert_by_rowid(advert_id)
    if not advert:
        await bot.send_message(
            chat_id=user_id,
            text=f"{_RTL}این آگهی دیگر موجود نیست یا پیدا نشد.",
        )
        return
    if list_accepted_offers_for_advert(advert_id):
        await bot.send_message(
            chat_id=user_id,
            text=f"{_RTL}برای این آگهی پیشنهادی پذیرفته شده؛ ارسال پیشنهاد جدید ممکن نیست.",
        )
        return

    owner_id = int(advert.get("user_id") or 0)
    if owner_id == user_id:
        if _should_skip_duplicate_owner_block(context, user_id, advert_id):
            return
        await bot.send_message(
            chat_id=user_id,
            text=f"{_RTL}نمی‌توانید به آگهی خودتان پیشنهاد دهید.",
        )
        return

    raw_mids = context.user_data.get("offer_flow_mids")
    if isinstance(raw_mids, list) and raw_mids:
        try:
            await cleanup_ids(
                bot,
                chat_id=user_id,
                ids=[int(x) for x in raw_mids if x is not None],
            )
        except Exception:
            pass

    context.user_data.pop("offer_draft_rate", None)
    context.user_data.pop("offer_draft_description", None)
    context.user_data.pop("offer_draft_account_country", None)
    raw_in = context.user_data.get("offer_flow_input_mids")
    if isinstance(raw_in, list) and raw_in:
        try:
            await cleanup_ids(
                bot,
                chat_id=user_id,
                ids=[int(x) for x in raw_in if x is not None],
            )
        except Exception:
            pass

    context.user_data.pop("offer_flow_mids", None)
    context.user_data.pop("offer_flow_input_mids", None)
    context.user_data.pop("offer_flow_active_mid", None)
    context.user_data.pop("offer_flow_step", None)

    await _offer_flow_main_menu_anchor(bot, chat_id=user_id, user_id=user_id)

    text = build_offer_gate_html(advert)
    sent = await bot.send_message(
        chat_id=user_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=_offer_gate_keyboard(advert_id),
        disable_web_page_preview=True,
    )
    _offer_flow_track(context, sent.message_id)
    context.user_data["offer_advert_id"] = advert_id
    context.user_data["offer_flow_step"] = "gate"
    context.user_data["state"] = UserState.OFFER_ADVERT_ID.name


def _format_eur_amount(amount) -> str:
    try:
        if amount is None:
            return "—"
        return f"{int(amount):,}"
    except (TypeError, ValueError):
        return str(amount)


def _offer_gate_ad_sample_html(advert: dict) -> str:
    """متن آگهی همان‌طور که در کانال دیده می‌شود (بدون خط ربات)."""
    from handlers import admin

    body = admin._build_channel_ad_text(advert)
    lines = [
        ln
        for ln in body.splitlines()
        if ln.strip() and not ln.strip().startswith("🤖")
    ]
    return "\n".join(lines).strip()


def build_offer_gate_html(advert: dict) -> str:
    """
    گام اول: نمونه آگهی + سؤال پذیرش شرایط.
    """
    aid = int(advert.get("rowid") or 0)
    sample = _offer_gate_ad_sample_html(advert)
    op = (advert.get("operation") or "").strip()
    amt = _format_eur_amount(advert.get("euro_amount"))
    country_raw = (advert.get("account_country") or "").strip()
    country = country_raw if country_raw and country_raw != "—" else "ذکرشده در آگهی"
    euro_ex = int(advert.get("euro_exchange") or 0) == 1
    is_exchange = op == "معاوضه" or euro_ex

    if is_exchange and op == "معاوضه":
        q = (
            f"آیا برای معاوضهٔ {amt} یورو طبق شرایط این آگهی "
            f"(در {country}) مایل به ادامه هستید؟"
        )
    elif is_exchange and op in ("خرید", "فروش"):
        side_lbl = "خرید" if op == "خرید" else "فروش"
        q = (
            f"آیا برای معاوضهٔ Euro به Euro ({side_lbl} یورو) به مقدار {amt} یورو "
            f"طبق این آگهی (کشور آگهی‌دهنده: {country}) مایل به ادامه هستید؟"
        )
    elif op == "خرید" and not is_exchange:
        q = (
            f"آیا شما فروشندهٔ یورو هستید و امکان پرداخت {amt} یورو "
            f"به حساب بانکی شخصی در کشور {country} را دارید؟"
        )
    elif op == "فروش" and not is_exchange:
        rate = advert.get("rate_toman")
        try:
            rate_s = (
                f"{int(rate):,}"
                if rate is not None and str(rate).strip() != ""
                else "ذکرشده در آگهی"
            )
        except (TypeError, ValueError):
            rate_s = str(rate)
        q = (
            f"آیا شما قصد <b>فروش</b> {amt} یورو به صورت حواله بانکی را دارید؟\n"
            f"(نرخ در آگهی: {rate_s} تومان)"
        )
    else:
        q = f"آیا مایلید برای این آگهی به مقدار {amt} یورو پیشنهاد خود را ادامه دهید؟"

    return (
        f"{_RTL}📋 <b>نمونه آگهی</b> — حواله <b>{aid}</b>\n\n"
        f"{sample}\n\n"
        f"{_RTL}❓ <b>سؤال:</b> {q}\n\n"
        f"{_RTL}یک گزینه را انتخاب کنید:"
    )


def _parse_int_toman(text: str) -> int | None:
    raw = (text or "").strip()
    if not raw:
        return None
    raw = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    digits = re.findall(r"\d+", raw)
    if not digits:
        return None
    try:
        return int("".join(digits))
    except ValueError:
        return None


def _parse_int_euro_amount(text: str) -> int | None:
    """همان منطق پارس عدد برای مقدار یورو."""
    return _parse_int_toman(text)


def _advert_euro_amount_int(advert: dict | None) -> int:
    if not advert:
        return 0
    try:
        return int(advert.get("euro_amount") or 0)
    except (TypeError, ValueError):
        return 0


def _advert_rate_toman_int(advert: dict | None) -> int | None:
    if not advert:
        return None
    try:
        rate = advert.get("rate_toman")
        if rate is None or str(rate).strip() == "":
            return None
        return int(rate)
    except (TypeError, ValueError):
        return None


def _offer_effective_euro_amount(advert: dict, proposed_euro_amount: int | None = None) -> int:
    try:
        pe = int(proposed_euro_amount or 0)
    except (TypeError, ValueError):
        pe = 0
    if pe > 0:
        return pe
    return _advert_euro_amount_int(advert)


def _offer_amount_line_html(advert: dict, proposed_euro_amount: int | None = None) -> str:
    adv_e = _advert_euro_amount_int(advert)
    eff = _offer_effective_euro_amount(advert, proposed_euro_amount)
    if eff > 0 and adv_e > 0 and eff != adv_e:
        return (
            f"{_RTL}💶 مقدار پیشنهادی: <b>{eff:,}</b> یورو "
            f"(در آگهی: <b>{adv_e:,}</b> یورو)\n"
        )
    if eff > 0:
        return f"{_RTL}💶 مقدار: <b>{eff:,}</b> یورو\n"
    return ""


def _channel_offer_line_inner_html(
    *,
    seq: int,
    label: str,
    rate: int,
    proposed_euro: int,
    advert: dict | None,
    hybrid: bool,
    status_prefix: str,
) -> str:
    """یک خط پیشنهاد برای کانال: نرخ+تومان قبل از نام (جلوگیری از چسبیدن «تومان» به نام در RTL)."""
    adv = advert or {}
    adv_e = _advert_euro_amount_int(adv)
    pe = int(proposed_euro or 0)
    eff_e = pe if pe > 0 else adv_e
    if hybrid and int(rate) == 0:
        rate_seg = "معاوضهٔ یورو به یورو"
    else:
        rate_seg = f"\u202a<b>{int(rate):,}</b> تومان\u202c"
    euro_seg = ""
    if eff_e > 0 and pe > 0 and pe != adv_e:
        euro_seg = f" — <b>{eff_e:,}</b> یورو"
    return f"{status_prefix}<b>{seq}</b> — {rate_seg}{euro_seg} — {label}"


def _advert_requester_rate_line_html(advert: dict) -> str:
    op = (advert.get("operation") or "").strip()
    euro_ex = int(advert.get("euro_exchange") or 0) == 1
    if op == "معاوضه" or euro_ex:
        return f"{_RTL}نرخ تومانی ثابت در این آگهی (معاوضه) تعریف نشده است."
    rate = advert.get("rate_toman")
    try:
        r = int(rate) if rate is not None and str(rate).strip() != "" else None
    except (TypeError, ValueError):
        r = None
    if r is None or r <= 0:
        return f"{_RTL}نرخ تومانی در این آگهی ثبت نشده است."
    return f"{_RTL}نرخ پیشنهادی درخواست‌کننده در آگهی: <b>{r:,}</b> تومان"


def _my_sent_offers_block_html(advert_rowid: int, proposer_id: int) -> str:
    rows = list_my_advert_offers(advert_rowid, proposer_id)
    adv = get_euro_advert_by_rowid(int(advert_rowid))
    ex = _offer_skips_toman_rate_step(adv) if adv else False
    if not rows:
        return f"{_RTL}❌ هنوز پیشنهادی ارسال نشده است."
    lines = [f"{_RTL}<b>پیشنهادهای ارسال‌شده توسط شما برای این آگهی</b> (قدیمی‌تر بالا، جدیدتر پایین):"]
    for row in rows:
        _oid, rt, ct = row[0], row[1], row[2]
        des = row[3] if len(row) > 3 else None
        st_raw = row[4] if len(row) > 4 else "pending"
        st = str(st_raw or "pending").strip().lower()
        seq_disp = int(_oid)
        if len(row) > 5:
            seq_disp = int(row[5])
        ts = (ct or "").replace("T", " ").replace("-", "/")[:16]
        tail = ""
        if des:
            short = (des[:35] + "…") if len(des) > 35 else des
            tail = f" — {_esc_html(short)}"
        if st == "accepted":
            st_lbl = "✅ <b>پذیرفته</b>"
        elif st == "rejected":
            st_lbl = "❌ <b>رد شده</b>"
        else:
            st_lbl = "⏳ <b>در انتظار</b>"
        rt_i = int(rt)
        if ex and rt_i == 0:
            rate_disp = f"{_RTL}معاوضهٔ یورو به یورو (بدون نرخ تومان)"
        else:
            rate_disp = f"<b>{rt_i:,}</b> تومان"
        lines.append(
            f"{_RTL}• پیشنهاد <b>{seq_disp}</b> — {st_lbl} — {rate_disp} — <i>{ts}</i>{tail}"
        )
    return "\n".join(lines)


def build_offer_rate_step_html(advert: dict, proposer_id: int) -> str:
    _ = proposer_id
    rate_hint = _advert_requester_rate_line_html(advert)
    return (
        f"{_RTL}💰 لطفاً <b>نرخ پیشنهادی</b> را به تومان وارد کنید (فقط عدد):\n"
        f"{_RTL}<i>مثال: 210000</i>\n\n"
        f"{rate_hint}"
    )


def build_offer_exchange_description_step_html(advert: dict, proposer_id: int) -> str:
    """گام توضیحات برای معاوضهٔ یورو به یورو (بدون نرخ تومان)."""
    _ = proposer_id
    return (
        f"{_RTL}📝 لطفاً <b>توضیحات پیشنهاد</b> (شرایط معاوضه، زمان هماهنگی و …) را بنویسید:\n"
        f"{_RTL}<i>برای این آگهی نرخ تومان ثبت نمی‌شود.</i>"
    )


def _offer_rate_cancel_keyboard(advert_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(_OFFER_STEP_CANCEL_LABEL, callback_data=f"offer_rate_cancel|{advert_id}")]]
    )


def _offer_desc_cancel_keyboard(advert_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(_OFFER_STEP_CANCEL_LABEL, callback_data=f"offer_desc_cancel|{advert_id}")]]
    )


def _offer_country_cancel_keyboard(advert_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(_OFFER_STEP_CANCEL_LABEL, callback_data=f"offer_country_cancel|{advert_id}")]]
    )


def _offer_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تأیید و ارسال", callback_data="offer_final_confirm"),
                InlineKeyboardButton("❌ انصراف", callback_data="offer_final_cancel"),
            ],
        ]
    )


def build_offer_preview_html(
    advert_id: int,
    rate: int,
    description: str,
    *,
    proposer_bank_country: str | None = None,
    advert: dict | None = None,
    proposed_euro_amount: int | None = None,
    counter_mode: bool = False,
) -> str:
    adv = advert if advert is not None else get_euro_advert_by_rowid(int(advert_id))
    bank = _offer_bank_country_lines_html(adv, proposer_bank_country) if adv else ""
    amt_block = _offer_amount_line_html(adv, proposed_euro_amount) if adv else ""
    if adv and _offer_skips_toman_rate_step(adv) and int(rate) == 0:
        rate_block = f"{_RTL}💱 <b>معاوضهٔ یورو به یورو</b> (بدون نرخ تومان در پیشنهاد)\n\n"
    else:
        rate_block = f"{_RTL}💰 <b>نرخ پیشنهادی شما:</b> <b>{int(rate):,}</b> تومان\n\n"
    title = (
        f"{_RTL}📋 <b>پیش‌نمایش پیشنهاد (مقدار/شرایط جدید)</b>\n"
        if counter_mode
        else f"{_RTL}📋 <b>پیش‌نمایش پیشنهاد شما</b>\n"
    )
    return (
        f"{title}"
        f"{_RTL}━━━━━━━━━━━━━━━━━━\n\n"
        f"{_RTL}🆔 <b>شماره آگهی:</b> <code>{int(advert_id)}</code>\n"
        f"{amt_block}"
        f"{rate_block}"
        f"{bank}"
        f"{_RTL}📝 <b>توضیحات:</b>\n{_esc_html(description)}\n\n"
        f"{_RTL}یکی از گزینه‌ها را بزنید:"
    )


def _offer_gate_keyboard(advert_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ با شرایط و مقدار موافقم",
                    callback_data=f"offer_gate_agree|{advert_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ شرایط یا مقدار جدید",
                    callback_data=f"offer_gate_custom|{advert_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو پیشنهاد",
                    callback_data=f"offer_gate_back|{advert_id}",
                )
            ],
        ]
    )


def build_offer_counter_amount_step_html(advert: dict) -> str:
    adv_amt = _format_eur_amount(advert.get("euro_amount"))
    return (
        f"{_RTL}💶 لطفاً <b>مقدار یورو</b> پیشنهادی را وارد کنید (فقط عدد):\n"
        f"{_RTL}<i>مقدار در آگهی: {adv_amt} — مثال: 900</i>"
    )


def build_offer_counter_rate_step_html(advert: dict) -> str:
    rate_line = _advert_requester_rate_line_html(advert)
    return (
        f"{_RTL}💰 لطفاً <b>نرخ پیشنهادی</b> را به تومان وارد کنید (فقط عدد):\n"
        f"{_RTL}<i>مثال: 210000</i>\n\n"
        f"{rate_line}"
    )


def build_offer_account_country_step_html() -> str:
    return (
        f"{_RTL}🌍 لطفاً <b>کشور حساب بانکی</b> خود را وارد کنید:\n"
        f"{_RTL}<i>مثال: آلمان</i>"
    )


def build_offer_description_only_step_html(*, saved_rate: int | None = None) -> str:
    rate_line = ""
    if isinstance(saved_rate, int) and saved_rate > 0:
        rate_line = f"{_RTL}<i>نرخ ثبت‌شده: {saved_rate:,} تومان</i>\n\n"
    return (
        f"{rate_line}"
        f"{_RTL}📝 لطفاً <b>توضیحات پیشنهاد</b> را بنویسید:\n"
        f"{_RTL}<i>شرایط، زمان هماهنگی، روش تماس…</i>"
    )


def _offer_ack_description_text(desc: str) -> str:
    d = (desc or "").strip()
    if len(d) <= 100:
        return f"✅ توضیحات: {d}"
    return f"✅ توضیحات: {d[:97]}…"


async def handle_offer_advert_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پست‌های قدیمی با callback_data: offer_<rowid> (بدون دکمهٔ url)."""
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()
    m = re.match(r"^offer_(\d+)$", query.data or "")
    if not m:
        return
    await deliver_offer_proposal_gate(context, query.from_user.id, int(m.group(1)))


async def handle_offer_gate_agree(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()
    m = re.match(r"^offer_gate_agree\|(\d+)$", query.data or "")
    if not m:
        return
    advert_id = int(m.group(1))
    user_id = query.from_user.id
    if not get_user(user_id):
        if query.message:
            try:
                await query.edit_message_text(
                    f"{_RTL}برای ادامه ابتدا در ربات ثبت‌نام کنید:\n/start",
                    reply_markup=None,
                )
            except Exception:
                pass
        return
    advert = get_euro_advert_by_rowid(advert_id)
    if not advert:
        if query.message:
            try:
                await query.edit_message_text(f"{_RTL}این آگهی دیگر موجود نیست.", reply_markup=None)
            except Exception:
                pass
        return
    if int(advert.get("user_id") or 0) == user_id:
        if query.message:
            try:
                await query.edit_message_text(f"{_RTL}نمی‌توانید به آگهی خودتان پیشنهاد دهید.", reply_markup=None)
            except Exception:
                pass
        return
    context.user_data.pop("offer_draft_rate", None)
    context.user_data.pop("offer_draft_description", None)
    context.user_data.pop("offer_draft_account_country", None)
    context.user_data.pop("offer_draft_euro_amount", None)
    context.user_data["offer_counter_mode"] = False
    if _offer_skips_toman_rate_step(advert):
        context.user_data["offer_draft_rate"] = 0
        text = build_offer_exchange_description_step_html(advert, user_id)
        kb = _offer_desc_cancel_keyboard(advert_id)
        st_next = UserState.OFFER_DESCRIPTION.name
    else:
        text = build_offer_rate_step_html(advert, user_id)
        kb = _offer_rate_cancel_keyboard(advert_id)
        st_next = UserState.OFFER_RATE.name
    context.user_data["offer_advert_id"] = advert_id
    flow_step = "description" if st_next == UserState.OFFER_DESCRIPTION.name else "rate"
    await _offer_flow_mark_gate_message(
        query,
        advert,
        footer_html=f"{_RTL}✅ <b>انتخاب شما ثبت شد.</b>",
    )
    await _offer_flow_ack(
        context, context.bot, chat_id=user_id, text="✅ با شرایط و مقدار آگهی موافقم"
    )
    await _offer_flow_prompt(
        context,
        context.bot,
        chat_id=user_id,
        step=flow_step,
        text=text,
        reply_markup=kb,
    )


async def handle_offer_gate_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پیشنهاد با مقدار/نرخ/شرایط متفاوت از آگهی."""
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()
    m = re.match(r"^offer_gate_custom\|(\d+)$", query.data or "")
    if not m:
        return
    advert_id = int(m.group(1))
    user_id = query.from_user.id
    if not get_user(user_id):
        if query.message:
            try:
                await query.edit_message_text(
                    f"{_RTL}برای ادامه ابتدا در ربات ثبت‌نام کنید:\n/start",
                    reply_markup=None,
                )
            except Exception:
                pass
        return
    advert = get_euro_advert_by_rowid(advert_id)
    if not advert:
        if query.message:
            try:
                await query.edit_message_text(f"{_RTL}این آگهی دیگر موجود نیست.", reply_markup=None)
            except Exception:
                pass
        return
    if int(advert.get("user_id") or 0) == user_id:
        if query.message:
            try:
                await query.edit_message_text(
                    f"{_RTL}نمی‌توانید به آگهی خودتان پیشنهاد دهید.",
                    reply_markup=None,
                )
            except Exception:
                pass
        return
    context.user_data.pop("offer_draft_rate", None)
    context.user_data.pop("offer_draft_description", None)
    context.user_data.pop("offer_draft_account_country", None)
    context.user_data.pop("offer_draft_euro_amount", None)
    context.user_data["offer_counter_mode"] = True
    context.user_data["offer_advert_id"] = advert_id
    text = build_offer_counter_amount_step_html(advert)
    kb = _offer_rate_cancel_keyboard(advert_id)
    await _offer_flow_mark_gate_message(
        query,
        advert,
        footer_html=f"{_RTL}✅ <b>انتخاب شما ثبت شد.</b>",
    )
    await _offer_flow_ack(
        context, context.bot, chat_id=user_id, text="✅ پیشنهاد با مقدار/شرایط جدید"
    )
    await _offer_flow_prompt(
        context,
        context.bot,
        chat_id=user_id,
        step="counter_euro",
        text=text,
        reply_markup=kb,
    )


async def handle_offer_counter_amount_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    advert_id = context.user_data.get("offer_advert_id")
    if not isinstance(advert_id, int):
        return
    if not context.user_data.get("offer_counter_mode"):
        return
    amt = _parse_int_euro_amount(update.message.text or "")
    if amt is None or amt <= 0:
        await update.message.reply_text(
            f"{_RTL}❌ لطفاً یک مقدار یورو معتبر (بزرگ‌تر از صفر) وارد کنید.",
        )
        return
    advert = get_euro_advert_by_rowid(advert_id)
    if not advert:
        _clear_offer_flow(context)
        await update.message.reply_text(f"{_RTL}آگهی پیدا نشد.")
        return
    chat_id = update.effective_chat.id
    _offer_flow_track_input(context, update.message.message_id)
    context.user_data["offer_draft_euro_amount"] = amt
    if _offer_skips_toman_rate_step(advert):
        context.user_data["offer_draft_rate"] = 0
        await _offer_flow_advance(
            context,
            context.bot,
            chat_id=chat_id,
            ack_text=f"✅ مقدار یورو: {amt:,}",
            step="description",
            prompt_html=build_offer_exchange_description_step_html(advert, user_id),
            reply_markup=_offer_desc_cancel_keyboard(advert_id),
        )
        return
    await _offer_flow_advance(
        context,
        context.bot,
        chat_id=chat_id,
        ack_text=f"✅ مقدار یورو: {amt:,}",
        step="rate",
        prompt_html=build_offer_counter_rate_step_html(advert),
        reply_markup=_offer_rate_cancel_keyboard(advert_id),
    )


async def handle_offer_rate_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    advert_id = context.user_data.get("offer_advert_id")
    if not isinstance(advert_id, int):
        return
    if context.user_data.get("offer_flow_step") == "description":
        return await handle_offer_description_message(update, context)
    advert_chk = get_euro_advert_by_rowid(advert_id)
    if advert_chk and _offer_skips_toman_rate_step(advert_chk):
        return
    chat_id = update.effective_chat.id
    rate = _parse_int_toman(update.message.text or "")

    async def _rate_step_error(err_html: str) -> None:
        _offer_flow_track_input(context, update.message.message_id)
        err_msg = await update.message.reply_text(
            err_html,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        _offer_flow_track(context, err_msg.message_id)

    if rate is None or rate <= 0:
        await _rate_step_error(f"{_RTL}❌ لطفاً یک عدد تومان معتبر (بزرگ‌تر از صفر) وارد کنید.")
        return
    if _proposer_same_rate_blocked(advert_id, user_id, rate):
        await _rate_step_error(
            f"{_RTL}❌ با این نرخ قبلاً برای همین آگهی پیشنهاد داده‌اید. نرخ متفاوتی وارد کنید."
        )
        return
    advert = get_euro_advert_by_rowid(advert_id)
    if not advert:
        _clear_offer_flow(context)
        await update.message.reply_text(f"{_RTL}آگهی پیدا نشد.")
        return
    rej_err = _offer_rate_after_rejection_error(
        advert, rate, proposer_telegram_id=user_id
    )
    if rej_err:
        await _rate_step_error(rej_err)
        return
    _offer_flow_track_input(context, update.message.message_id)
    context.user_data["offer_draft_rate"] = rate
    if _offer_requires_proposer_bank_country(advert):
        await _offer_flow_advance(
            context,
            context.bot,
            chat_id=chat_id,
            ack_text=f"✅ نرخ: {rate:,} تومان",
            step="account_country",
            prompt_html=build_offer_account_country_step_html(),
            reply_markup=_offer_country_cancel_keyboard(advert_id),
        )
        return
    await _offer_flow_advance(
        context,
        context.bot,
        chat_id=chat_id,
        ack_text=f"✅ نرخ: {rate:,} تومان",
        step="description",
        prompt_html=build_offer_description_only_step_html(saved_rate=rate),
        reply_markup=_offer_desc_cancel_keyboard(advert_id),
    )


async def handle_offer_account_country_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    advert_id = context.user_data.get("offer_advert_id")
    rate = context.user_data.get("offer_draft_rate")
    if not isinstance(advert_id, int) or not isinstance(rate, int):
        return
    advert = get_euro_advert_by_rowid(advert_id)
    if not advert or not _offer_requires_proposer_bank_country(advert):
        return
    raw = (update.message.text or "").strip()
    if _message_looks_like_toman_amount(raw) or raw.isdigit():
        _offer_flow_track_input(context, update.message.message_id)
        err = await update.message.reply_text(
            f"{_RTL}❌ لطفاً <b>نام کشور</b> بنویسید (مثلاً آلمان) — عدد نرخ نیست.",
            parse_mode=ParseMode.HTML,
        )
        _offer_flow_track(context, err.message_id)
        return
    if len(raw) < 2:
        await update.message.reply_text(
            f"{_RTL}❌ نام کشور را واضح‌تر بنویسید (حداقل ۲ نویسه).",
        )
        return
    if len(raw) > 120:
        await update.message.reply_text(f"{_RTL}❌ متن کوتاه‌تر وارد کنید (حداکثر ۱۲۰ نویسه).")
        return
    chat_id = update.effective_chat.id
    _offer_flow_track_input(context, update.message.message_id)
    context.user_data["offer_draft_account_country"] = raw
    saved_rate = context.user_data.get("offer_draft_rate")
    sr = int(saved_rate) if isinstance(saved_rate, int) else None
    await _offer_flow_advance(
        context,
        context.bot,
        chat_id=chat_id,
        ack_text=f"✅ کشور حساب بانکی: {raw}",
        step="description",
        prompt_html=build_offer_description_only_step_html(saved_rate=sr),
        reply_markup=_offer_desc_cancel_keyboard(advert_id),
    )


async def handle_offer_description_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if context.user_data.get("offer_flow_step") == "rate":
        return await handle_offer_rate_message(update, context)
    advert_id = context.user_data.get("offer_advert_id")
    rate = context.user_data.get("offer_draft_rate")
    chat_id = update.effective_chat.id
    if not isinstance(advert_id, int):
        return
    if not isinstance(rate, int):
        await _offer_flow_prompt(
            context,
            context.bot,
            chat_id=chat_id,
            step="rate",
            text=_build_offer_rate_prompt_html(
                get_euro_advert_by_rowid(advert_id) or {},
                update.effective_user.id,
                counter_mode=bool(context.user_data.get("offer_counter_mode")),
            ),
            reply_markup=_offer_rate_cancel_keyboard(advert_id),
        )
        return
    advert = get_euro_advert_by_rowid(advert_id)
    if not advert:
        return
    if _offer_requires_proposer_bank_country(advert):
        cc = context.user_data.get("offer_draft_account_country")
        if not isinstance(cc, str) or len(cc.strip()) < 2:
            await update.message.reply_text(
                f"{_RTL}ابتدا کشور حساب بانکی را در مرحلهٔ قبل وارد کنید یا با /menu از اول شروع کنید.",
            )
            return
    raw = (update.message.text or "").strip()
    if _message_looks_like_toman_amount(raw):
        _offer_flow_track_input(context, update.message.message_id)
        err = await update.message.reply_text(
            f"{_RTL}❌ این مرحله فقط <b>متن توضیحات</b> است.\n"
            f"{_RTL}نرخ شما قبلاً ثبت شده: <b>{int(rate):,}</b> تومان.",
            parse_mode=ParseMode.HTML,
        )
        _offer_flow_track(context, err.message_id)
        return
    desc = raw
    if len(desc) < 2:
        await update.message.reply_text(
            f"{_RTL}❌ توضیحات را کمی کامل‌تر بنویسید (حداقل ۲ نویسه).",
        )
        return
    if len(desc) > 3500:
        await update.message.reply_text(f"{_RTL}❌ توضیحات خیلی طولانی است. کوتاه‌تر کنید.")
        return
    _offer_flow_track_input(context, update.message.message_id)
    context.user_data["offer_draft_description"] = desc
    pbc = None
    if _offer_requires_proposer_bank_country(advert):
        pbc = (context.user_data.get("offer_draft_account_country") or "").strip()
    counter = bool(context.user_data.get("offer_counter_mode"))
    draft_euro = context.user_data.get("offer_draft_euro_amount")
    pe_kw = int(draft_euro) if isinstance(draft_euro, int) and draft_euro > 0 else None
    preview = build_offer_preview_html(
        advert_id,
        rate,
        desc,
        proposer_bank_country=pbc,
        advert=advert,
        proposed_euro_amount=pe_kw,
        counter_mode=counter,
    )
    await _offer_flow_clear_prompt(context, context.bot, chat_id=chat_id)
    await _offer_flow_ack(
        context, context.bot, chat_id=chat_id, text=_offer_ack_description_text(desc)
    )
    await _offer_flow_prompt(
        context,
        context.bot,
        chat_id=chat_id,
        step="preview",
        text=preview,
        reply_markup=_offer_preview_keyboard(),
    )


async def handle_offer_preview_idle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            f"{_RTL}لطفاً با دکمه‌های «تایید پیشنهاد» یا «انصراف» ادامه دهید.",
        )


async def handle_offer_final_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    if (query.data or "") != "offer_final_confirm":
        return
    if context.user_data.get("state") != UserState.OFFER_PREVIEW.name:
        await query.answer()
        return
    uid = query.from_user.id
    aid = context.user_data.get("offer_advert_id")
    rate = context.user_data.get("offer_draft_rate")
    desc = context.user_data.get("offer_draft_description")
    if not isinstance(aid, int) or not isinstance(rate, int) or not isinstance(desc, str):
        await query.answer()
        return
    advert = get_euro_advert_by_rowid(aid)
    if not advert or int(advert.get("user_id") or 0) == uid:
        await query.answer("آگهی نامعتبر است.", show_alert=True)
        return
    if list_accepted_offers_for_advert(aid):
        await query.answer(
            "برای این آگهی پیشنهادی پذیرفته شده؛ امکان پیشنهاد جدید نیست.",
            show_alert=True,
        )
        return
    if _proposer_same_rate_blocked(aid, uid, rate):
        if rate == 0 and _offer_skips_toman_rate_step(advert):
            await query.answer(
                "برای این آگهی هنوز پیشنهاد شما در انتظار تأیید است.",
                show_alert=True,
            )
        else:
            await query.answer(
                "با این نرخ قبلاً برای این آگهی پیشنهاد داده‌اید. نرخ دیگری انتخاب کنید.",
                show_alert=True,
            )
        return
    deleted_pending = delete_pending_offers_for_proposer_on_advert(aid, uid)
    for meta in deleted_pending:
        await purge_offer_thread_messages(
            context.bot,
            user_data_store,
            int(meta["owner_id"]),
            int(meta["proposer_telegram_id"]),
            int(meta["id"]),
        )
        negotiation_cleanup_for_offer(context.application.bot_data, int(meta["id"]))
    if deleted_pending:
        await refresh_advert_channel_post(context.bot, aid)
    rej_err = _offer_rate_after_rejection_error(advert, rate, proposer_telegram_id=uid)
    if rej_err:
        plain = re.sub(r"<[^>]+>", "", rej_err).replace("\u200f", "").strip()
        if len(plain) > 180:
            plain = plain[:177] + "…"
        await query.answer(plain, show_alert=True)
        return
    prop_ctry = None
    if _offer_requires_proposer_bank_country(advert):
        raw_c = context.user_data.get("offer_draft_account_country")
        if not isinstance(raw_c, str) or len(raw_c.strip()) < 2:
            await query.answer("کشور حساب بانکی ثبت نشده است.", show_alert=True)
            return
        prop_ctry = raw_c.strip()
    counter = bool(context.user_data.get("offer_counter_mode"))
    proposed_euro: int | None = None
    if counter:
        adv_amt = _advert_euro_amount_int(advert)
        draft_amt = context.user_data.get("offer_draft_euro_amount")
        adv_rate = _advert_rate_toman_int(advert)
        amount_changed = isinstance(draft_amt, int) and draft_amt > 0 and draft_amt != adv_amt
        rate_changed = False
        if not _offer_skips_toman_rate_step(advert):
            rate_changed = adv_rate is None or int(rate) != int(adv_rate)
        desc_ok = len((desc or "").strip()) >= 2
        if not (amount_changed or rate_changed or desc_ok):
            await query.answer(
                "حداقل مقدار یورو، نرخ تومان، یا توضیحات/شرایط را نسبت به آگهی تغییر دهید.",
                show_alert=True,
            )
            return
        if amount_changed:
            proposed_euro = int(draft_amt)
    ins = insert_advert_offer(
        aid,
        uid,
        rate,
        desc,
        proposer_account_country=prop_ctry,
        proposed_euro_amount=proposed_euro,
    )
    if ins is None:
        await query.answer("ذخیره نشد.", show_alert=True)
        return
    row_id, offer_seq = ins
    await query.answer()

    proposer_row = get_user(uid)
    pname = _public_offer_name(proposer_row, uid)
    dsc = (desc or "").strip()
    await dispatch_offer_created_notifications(
        context.bot,
        advert_rowid=int(aid),
        proposer_telegram_id=uid,
        offer_row_id=row_id,
        offer_seq=int(offer_seq),
        rate_toman=int(rate),
        description=dsc,
        public_display_name=pname,
        is_admin_proxy=False,
        proposer_account_country=prop_ctry,
        skip_main_menu_refresh_for_proposer=True,
    )

    chat_id = query.message.chat_id if query.message else uid
    pmid = query.message.message_id if query.message else None
    await _finish_offer_flow_abort(
        context,
        context.bot,
        chat_id=chat_id,
        user_id=uid,
        preview_message_id=pmid,
        menu_text="🏠 منوی اصلی:",
    )


async def handle_offer_final_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if (query.data or "") != "offer_final_cancel":
        return
    uid = query.from_user.id
    cid = query.message.chat_id if query.message else uid
    pmid = query.message.message_id if query.message else None
    await _finish_offer_flow_abort(
        context, context.bot, chat_id=cid, user_id=uid, preview_message_id=pmid
    )


async def handle_offer_desc_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not re.match(r"^offer_desc_cancel\|\d+$", query.data or ""):
        return
    uid = query.from_user.id
    cid = query.message.chat_id if query.message else uid
    pmid = query.message.message_id if query.message else None
    await _finish_offer_flow_abort(
        context, context.bot, chat_id=cid, user_id=uid, preview_message_id=pmid
    )


async def handle_offer_country_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not re.match(r"^offer_country_cancel\|\d+$", query.data or ""):
        return
    uid = query.from_user.id
    cid = query.message.chat_id if query.message else uid
    pmid = query.message.message_id if query.message else None
    await _finish_offer_flow_abort(
        context, context.bot, chat_id=cid, user_id=uid, preview_message_id=pmid
    )


async def handle_offer_rate_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not re.match(r"^offer_rate_cancel\|\d+$", query.data or ""):
        return
    uid = query.from_user.id
    cid = query.message.chat_id if query.message else uid
    pmid = query.message.message_id if query.message else None
    await _finish_offer_flow_abort(
        context, context.bot, chat_id=cid, user_id=uid, preview_message_id=pmid
    )


async def handle_offer_gate_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    uid = query.from_user.id
    cid = query.message.chat_id if query.message else update.effective_chat.id
    pmid = query.message.message_id if query.message else None
    await _finish_offer_flow_abort(
        context, context.bot, chat_id=cid, user_id=uid, preview_message_id=pmid
    )


async def handle_advert_owner_offer_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    m = re.match(r"^adv_o\|(ok|no|neg|msg)\|(\d+)$", query.data or "")
    if not m:
        return
    action = m.group(1)
    offer_id = int(m.group(2))
    row = get_advert_offer_joined(offer_id)
    if not row:
        await query.answer("پیشنهاد پیدا نشد.", show_alert=True)
        return
    if int(query.from_user.id) != int(row.get("owner_id") or 0):
        await query.answer()
        return

    st = (row.get("status") or "pending").strip().lower()

    if action == "ok":
        if st == "accepted":
            await query.answer("این پیشنهاد قبلاً پذیرفته شده است.")
            return
        if st == "rejected":
            await query.answer("این پیشنهاد قبلاً رد شده است.", show_alert=True)
            return
        if not update_advert_offer_status(offer_id, "accepted"):
            await query.answer("ذخیره نشد.", show_alert=True)
            return
        await query.answer("پیشنهاد پذیرفته شد.")
        owner_id = int(row["owner_id"])
        proposer_id = int(row["proposer_telegram_id"])
        aid = int(row["advert_rowid"])
        seq = int(row.get("seq_in_advert") or offer_id)
        for other_oid in reject_other_pending_offers_for_advert(aid, offer_id):
            ometa = get_advert_offer_joined(other_oid)
            if ometa:
                await purge_offer_thread_messages(
                    context.bot,
                    user_data_store,
                    int(ometa["owner_id"]),
                    int(ometa["proposer_telegram_id"]),
                    other_oid,
                )
                negotiation_cleanup_for_offer(
                    context.application.bot_data, other_oid
                )
                opid = int(ometa["proposer_telegram_id"])
                oseq = int(ometa.get("seq_in_advert") or other_oid)
                if opid and opid != proposer_id:
                    try:
                        await context.bot.send_message(
                            opid,
                            f"{_RTL}پیشنهاد شماره <b>{oseq}</b> برای آگهی <b>{aid}</b> به‌صورت خودکار رد شد "
                            f"(پیشنهاد دیگری برای این آگهی پذیرفته شد).",
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    except Exception:
                        pass
                    try:
                        await send_or_replace_main_menu(
                            context.bot,
                            chat_id=opid,
                            user_id=opid,
                            store=user_data_store,
                        )
                    except Exception:
                        pass
        advert = get_euro_advert_by_rowid(aid)
        await purge_offer_thread_messages(
            context.bot,
            user_data_store,
            owner_id,
            proposer_id,
            offer_id,
        )
        negotiation_cleanup_for_offer(context.application.bot_data, offer_id)
        await refresh_advert_channel_post(context.bot, aid)
        if advert:
            acc_owner = _post_acceptance_message_html(
                advert, row, owner_id, seq, aid
            )
            acc_prop = _post_acceptance_message_html(
                advert, row, proposer_id, seq, aid
            )
        else:
            acc_owner = (
                f"{_RTL}✅ <b>پیشنهاد {seq}</b> برای آگهی <b>{aid}</b> پذیرفته شد.\n"
                f"{_RTL}پیام‌های مربوط به این پیشنهاد از چت پاک شد."
            )
            acc_prop = (
                f"{_RTL}✅ صاحب آگهی، پیشنهاد شماره <b>{seq}</b> (آگهی <b>{aid}</b>) را پذیرفت."
            )
        acc_kb = _post_acceptance_reply_markup(advert) if advert else None
        for u, txt in ((owner_id, acc_owner), (proposer_id, acc_prop)):
            if not u:
                continue
            try:
                await context.bot.send_message(
                    u,
                    txt,
                    parse_mode=ParseMode.HTML,
                    reply_markup=acc_kb,
                    disable_web_page_preview=True,
                )
            except Exception:
                await context.bot.send_message(
                    u,
                    txt.replace("<b>", "").replace("</b>", ""),
                    reply_markup=acc_kb,
                    disable_web_page_preview=True,
                )
            try:
                await send_or_replace_main_menu(
                    context.bot,
                    chat_id=u,
                    user_id=u,
                    store=user_data_store,
                )
            except Exception:
                pass
        return

    if action == "no":
        if st == "accepted":
            await query.answer("پیشنهاد قبلاً پذیرفته شده؛ نمی‌توان رد کرد.", show_alert=True)
            return
        update_advert_offer_status(offer_id, "rejected")
        await query.answer("پیشنهاد رد شد.")
        owner_id = int(row["owner_id"])
        proposer_id = int(row["proposer_telegram_id"])
        aid = int(row["advert_rowid"])
        seq = int(row.get("seq_in_advert") or offer_id)
        await purge_offer_thread_messages(
            context.bot,
            user_data_store,
            owner_id,
            proposer_id,
            offer_id,
        )
        negotiation_cleanup_for_offer(context.application.bot_data, offer_id)
        await refresh_advert_channel_post(context.bot, aid)
        rej_owner = f"{_RTL}⭕️ پیشنهاد <b>{seq}</b> برای آگهی <b>{aid}</b> رد شد."
        rej_prop = (
            f"{_RTL}⭕️ پیشنهاد شماره <b>{seq}</b> برای آگهی <b>{aid}</b> توسط آگهی‌دهنده رد شد."
        )
        for u, txt in ((owner_id, rej_owner), (proposer_id, rej_prop)):
            if not u:
                continue
            try:
                await context.bot.send_message(
                    u, txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
            except Exception:
                await context.bot.send_message(
                    u,
                    txt.replace("<b>", "").replace("</b>", ""),
                    disable_web_page_preview=True,
                )
            try:
                await send_or_replace_main_menu(
                    context.bot,
                    chat_id=u,
                    user_id=u,
                    store=user_data_store,
                )
            except Exception:
                pass
        return

    if action == "msg":
        await query.answer("این بخش به‌زودی فعال می‌شود.")
        return

    if action == "neg":
        await query.answer()
        if not query.message:
            return
        await _negotiation_show_gate(
            context,
            offer_id,
            query.from_user.id,
            query.message.chat_id,
        )
        return


def negotiation_cleanup_for_offer(application_bot_data: dict, offer_id: int) -> None:
    bucket = application_bot_data.get(_NEG_SESSIONS_KEY)
    if isinstance(bucket, dict):
        bucket.pop(int(offer_id), None)
    clear_neg_transcript(application_bot_data, int(offer_id))


async def _negotiation_show_gate(
    context: ContextTypes.DEFAULT_TYPE, offer_id: int, uid: int, chat_id: int
) -> None:
    row = get_advert_offer_joined(offer_id)
    if not row:
        return
    st = (row.get("status") or "pending").strip().lower()
    if st != "pending":
        try:
            await context.bot.send_message(
                uid,
                f"{_RTL}مذاکره فقط برای پیشنهاد «در انتظار تأیید» ممکن است.",
            )
        except Exception:
            pass
        return
    owner = int(row["owner_id"])
    proposer = int(row["proposer_telegram_id"])
    if uid not in (owner, proposer):
        return
    oid = int(offer_id)
    old_mid = context.user_data.pop("neg_gate_mid", None)
    if old_mid:
        try:
            await context.bot.delete_message(
                chat_id=chat_id, message_id=int(old_mid)
            )
        except Exception:
            pass
    context.user_data["state"] = UserState.NEGOTIATION_GATE.name
    context.user_data["neg_gate_offer_id"] = oid
    gate_kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📤 ارسال پیام", callback_data=f"neg_send|{oid}")],
            [InlineKeyboardButton("❌ انصراف", callback_data=f"neg_gc|{oid}")],
        ]
    )
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"{_RTL}🗣️ برای مذاکره دربارهٔ این پیشنهاد یکی را انتخاب کنید:\n"
            f"{_RTL}<i>تا وقتی پیامی نفرستید، طرف مقابل مطلع نمی‌شود.</i>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=gate_kb,
    )
    context.user_data["neg_gate_mid"] = sent.message_id


async def handle_negotiation_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.message:
        return
    raw = (update.message.text or "").strip()
    if not raw or raw.startswith("/"):
        return
    oid = context.user_data.get("neg_offer_id")
    if not isinstance(oid, int):
        return
    ids = _neg_offer_ids_as_set(context.user_data)
    if oid not in ids:
        ids.add(oid)
        _neg_offer_ids_write(context.user_data, ids)
    try:
        await update.message.delete()
    except Exception:
        pass
    pmid = context.user_data.pop("neg_prompt_mid", None)
    if pmid:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id, message_id=int(pmid)
            )
        except Exception:
            pass
    row = get_advert_offer_joined(oid)
    if not row:
        await context.bot.send_message(
            update.effective_chat.id, f"{_RTL}این مذاکره دیگر فعال نیست."
        )
        _discard_negotiation_offer(context, oid)
        await send_or_replace_main_menu(
            context.bot,
            chat_id=update.effective_chat.id,
            user_id=update.effective_user.id,
            store=user_data_store,
        )
        raise ApplicationHandlerStop
    st = (row.get("status") or "pending").strip().lower()
    if st != "pending":
        await context.bot.send_message(
            update.effective_chat.id,
            f"{_RTL}این پیشنهاد دیگر در وضعیت مذاکره نیست.",
        )
        _discard_negotiation_offer(context, oid)
        await send_or_replace_main_menu(
            context.bot,
            chat_id=update.effective_chat.id,
            user_id=update.effective_user.id,
            store=user_data_store,
        )
        raise ApplicationHandlerStop
    uid = int(update.effective_user.id)
    owner = int(row["owner_id"])
    proposer = int(row["proposer_telegram_id"])
    if uid == owner:
        from_role = "owner"
    elif uid == proposer:
        from_role = "proposer"
    else:
        return
    scrubbed = _scrub_for_anonymous_peer(raw)
    if not scrubbed:
        await context.bot.send_message(
            update.effective_chat.id,
            f"{_RTL}متن قابل ارسال نیست؛ از اشتراک شماره، آیدی یا لینک خودداری کنید.",
        )
        raise ApplicationHandlerStop
    entries = neg_transcript_append(
        context.application.bot_data, oid, from_role, scrubbed
    )
    await _sync_negotiation_panels(
        context.bot,
        user_data_store,
        context.application.bot_data,
        row,
        entries,
    )
    context.user_data.pop("neg_offer_id", None)
    context.user_data["state"] = UserState.MAIN_MENU.name
    await send_or_replace_main_menu(
        context.bot,
        chat_id=update.effective_chat.id,
        user_id=uid,
        store=user_data_store,
    )
    raise ApplicationHandlerStop


async def handle_offer_proposer_delete(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    m = re.match(r"^offer_del\|(\d+)$", query.data or "")
    if not m:
        return
    oid = int(m.group(1))
    row = get_advert_offer_joined(oid)
    ok, adv_rid = delete_advert_offer_if_pending(oid, query.from_user.id)
    await query.answer("پیشنهاد حذف شد." if ok else "این پیشنهاد قابل حذف نیست.", show_alert=not ok)
    if ok:
        if row:
            await purge_offer_thread_messages(
                context.bot,
                user_data_store,
                int(row["owner_id"]),
                int(row["proposer_telegram_id"]),
                oid,
            )
        negotiation_cleanup_for_offer(context.application.bot_data, oid)
    if ok and adv_rid is not None:
        await refresh_advert_channel_post(context.bot, adv_rid)
    if ok and query.message:
        txt = query.message.text or query.message.caption or ""
        if MY_OFFERS_SENTINEL in txt:
            uid = query.from_user.id
            try:
                sent = list_my_pending_offers_all(uid)
            except Exception:
                sent = []
            if not sent:
                try:
                    await query.message.edit_text(
                        f"{_RTL}دیگر پیشنهاد فعالی در انتظار تأیید ندارید.",
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton("✖️ بستن", callback_data="my_offers_close")]]
                        ),
                    )
                except Exception:
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
            else:
                try:
                    await query.message.edit_text(
                        _format_my_offers_list_text(sent),
                        reply_markup=_my_offers_inline_keyboard(sent),
                    )
                except Exception:
                    pass
        else:
            try:
                await query.message.delete()
            except Exception:
                pass


async def handle_offer_proposer_again(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    m = re.match(r"^offer_again\|(\d+)$", query.data or "")
    if not m:
        return
    aid = int(m.group(1))
    uid = query.from_user.id
    if not get_user(uid):
        await query.answer("ابتدا ثبت‌نام کنید.", show_alert=True)
        return
    advert = get_euro_advert_by_rowid(aid)
    if not advert:
        await query.answer("آگهی پیدا نشد.", show_alert=True)
        return
    if int(advert.get("user_id") or 0) == uid:
        await query.answer("به آگهی خودتان پیشنهاد نمی‌دهید.", show_alert=True)
        return
    if list_accepted_offers_for_advert(aid):
        await query.answer(
            "برای این آگهی پیشنهادی پذیرفته شده؛ امکان پیشنهاد جدید نیست.",
            show_alert=True,
        )
        return
    deleted = delete_pending_offers_for_proposer_on_advert(aid, uid)
    for meta in deleted:
        await purge_offer_thread_messages(
            context.bot,
            user_data_store,
            int(meta["owner_id"]),
            int(meta["proposer_telegram_id"]),
            int(meta["id"]),
        )
        negotiation_cleanup_for_offer(context.application.bot_data, int(meta["id"]))
    if deleted:
        await refresh_advert_channel_post(context.bot, aid)
    await query.answer("پیشنهاد قبلی لغو شد؛ مرحلهٔ تازه را دنبال کنید.")
    try:
        if query.message:
            await query.message.delete()
    except Exception:
        pass
    await deliver_offer_proposal_gate(context, uid, aid)


async def handle_offer_negotiate_stub(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    m = re.match(r"^offer_neg\|(\d+)$", query.data or "")
    if not m:
        return
    oid = int(m.group(1))
    row = get_advert_offer_joined(oid)
    if not row or int(row.get("proposer_telegram_id") or 0) != int(query.from_user.id):
        await query.answer()
        return
    await query.answer()
    if not query.message:
        return
    await _negotiation_show_gate(
        context, oid, query.from_user.id, query.message.chat_id
    )
