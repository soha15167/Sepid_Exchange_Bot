"""
handlers/user_adverts.py — My adverts / آگهی‌های من

EN: List owner's ads; edit/delete when no active pending offers.
FA: لیست آگهی‌های کاربر؛ ویرایش/حذف فقط بدون پیشنهاد فعال.
"""

from __future__ import annotations

import html as html_module
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config.settings import ADVERT_CHANNEL_ID
from database.db import (
    delete_euro_advert_for_owner,
    get_euro_advert_by_rowid,
    get_user,
    list_euro_adverts_owned_by_user,
    update_euro_advert_field_for_owner,
    user_advert_has_active_offers,
)
from handlers.offers import advert_public_link_html, refresh_advert_channel_post
from keyboards.menus import MY_ADVERTS_REPLY_BUTTON_TEXT
from models.enums import UserState
from state import user_data_store
from utils.telegram_utils import send_or_replace_main_menu

_RTL = "\u200f"


def _owner_uid(adv: dict) -> int:
    try:
        return int(adv.get("user_id") or 0)
    except (TypeError, ValueError):
        return 0


def _fmt_advert_row_label(d: dict, *, locked: bool) -> str:
    """locked = پیشنهاد pending یا accepted روی آگهی."""
    rid = int(d["rowid"])
    op = (d.get("operation") or "—").strip()
    try:
        amt = int(d.get("euro_amount") or 0)
    except (TypeError, ValueError):
        amt = 0
    try:
        rt = int(d.get("rate_toman") or 0)
    except (TypeError, ValueError):
        rt = 0
    ex = int(d.get("euro_exchange") or 0)
    kind = "معاوضه" if ex else op
    pref = "🔒 " if locked else ""
    s = f"{pref}#{rid} {kind} {amt}€ {rt:,}"
    return s[:58] if len(s) > 58 else s


def _adverts_list_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    kb = []
    for d in rows[:28]:
        rid = int(d["rowid"])
        locked = user_advert_has_active_offers(rid)
        kb.append(
            [
                InlineKeyboardButton(
                    _fmt_advert_row_label(d, locked=locked),
                    callback_data=f"user_adv|m|{rid}",
                )
            ]
        )
    kb.append([InlineKeyboardButton("❌ بستن", callback_data="user_adv|close")])
    return InlineKeyboardMarkup(kb)


_LIST_INTRO = (
    f"{_RTL}📰 <b>آگهی‌های من</b>\n"
    f"{_RTL}🔒 = پیشنهاد <b>در انتظار</b> یا <b>پذیرفته‌شده</b> — فقط مشاهده (بدون ویرایش/حذف).\n"
    f"{_RTL}یک آگهی را باز کنید:"
)


async def _send_my_adverts_list(bot, chat_id: int, user_id: int) -> None:
    rows = list_euro_adverts_owned_by_user(user_id)
    if not rows:
        await bot.send_message(
            chat_id,
            f"{_RTL}📭 هنوز آگهی ثبت نکرده‌اید.\n"
            f"{_RTL}بعد از انتشار آگهی در کانال، اینجا فهرست می‌شود.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ بستن", callback_data="user_adv|close")]]
            ),
            disable_web_page_preview=True,
        )
        return
    await bot.send_message(
        chat_id,
        _LIST_INTRO,
        reply_markup=_adverts_list_keyboard(rows),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def handle_main_my_adverts_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """همان منوی «آگهی‌های من» از دکمهٔ ریپلای (نه اینلاین)."""
    if not update.message:
        return
    uid = update.effective_user.id
    if not get_user(uid):
        await update.message.reply_text("ابتدا ثبت‌نام کنید.")
        return
    context.user_data.pop("user_edit_advert_id", None)
    context.user_data.pop("user_edit_advert_field", None)
    context.user_data["state"] = UserState.MAIN_MENU.name
    try:
        await update.message.delete()
    except Exception:
        pass
    await _send_my_adverts_list(context.bot, update.effective_chat.id, uid)


async def handle_main_my_adverts_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    uid = q.from_user.id
    if not get_user(uid):
        await q.answer("ابتدا ثبت‌نام کنید.", show_alert=True)
        return
    await q.answer()
    rows = list_euro_adverts_owned_by_user(uid)
    if not rows:
        empty_text = (
            f"{_RTL}📭 هنوز آگهی ثبت نکرده‌اید.\n"
            f"{_RTL}بعد از انتشار آگهی در کانال، اینجا فهرست می‌شود."
        )
        empty_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ بستن", callback_data="user_adv|close")]]
        )
        try:
            await q.edit_message_text(
                empty_text,
                reply_markup=empty_kb,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            try:
                await q.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                q.message.chat_id,
                empty_text,
                reply_markup=empty_kb,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        return
    try:
        await q.edit_message_text(
            _LIST_INTRO,
            reply_markup=_adverts_list_keyboard(rows),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        await _send_my_adverts_list(context.bot, q.message.chat_id, uid)


def _detail_keyboard(rid: int, *, locked: bool) -> InlineKeyboardMarkup:
    rows_btn: list[list[InlineKeyboardButton]] = []
    if not locked:
        rows_btn.extend(
            [
                [
                    InlineKeyboardButton(
                        "ویرایش نرخ 💰", callback_data=f"user_adv|e_rate|{rid}"
                    ),
                    InlineKeyboardButton(
                        "ویرایش یورو 💶", callback_data=f"user_adv|e_amt|{rid}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "ویرایش توضیحات 📝",
                        callback_data=f"user_adv|e_desc|{rid}",
                    )
                ],
                [InlineKeyboardButton("حذف آگهی 🗑", callback_data=f"user_adv|del|{rid}")],
            ]
        )
    rows_btn.append([InlineKeyboardButton("🔙 لیست", callback_data="main_my_adverts")])
    return InlineKeyboardMarkup(rows_btn)


async def handle_user_adv_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.message or not q.from_user:
        return
    uid = q.from_user.id
    data = q.data or ""
    if not data.startswith("user_adv|"):
        return
    parts = data.split("|")
    if len(parts) < 2:
        return
    action = parts[1]

    if action == "close":
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        await send_or_replace_main_menu(
            context.bot,
            chat_id=q.message.chat_id,
            user_id=uid,
            store=user_data_store,
        )
        return

    if not get_user(uid):
        await q.answer("ثبت‌نام کنید.", show_alert=True)
        return

    if action == "m" and len(parts) >= 3:
        rid = int(parts[2])
        adv = get_euro_advert_by_rowid(rid)
        if not adv or _owner_uid(adv) != uid:
            await q.answer("آگهی نامعتبر است.", show_alert=True)
            return
        locked = user_advert_has_active_offers(rid)
        await q.answer()
        desc = (adv.get("description") or "—")[:500]
        try:
            rt = int(adv.get("rate_toman") or 0)
            amt = int(adv.get("euro_amount") or 0)
        except (TypeError, ValueError):
            rt = amt = 0
        op = (adv.get("operation") or "—").strip()
        lock_note = (
            f"{_RTL}🔒 <b>پیشنهاد فعال دارید</b> (در انتظار یا پذیرفته‌شده)؛ "
            f"تا پایان مذاکره ویرایش/حذف از اینجا ممکن نیست. "
            f"ویرایش اجباری فقط از <b>پنل ادمین</b>.\n\n"
            if locked
            else ""
        )
        body = (
            f"{lock_note}"
            f"{_RTL}🧾 <b>آگهی #{rid}</b>\n"
            f"{_RTL}نوع: <b>{html_module.escape(op)}</b>\n"
            f"{_RTL}مقدار یورو: <b>{amt:,}</b>\n"
            f"{_RTL}نرخ: <b>{rt:,}</b> تومان\n"
            f"{_RTL}توضیحات:\n<code>{html_module.escape(desc)}</code>\n"
        )
        try:
            await q.edit_message_text(
                body,
                parse_mode=ParseMode.HTML,
                reply_markup=_detail_keyboard(rid, locked=locked),
                disable_web_page_preview=True,
            )
        except Exception:
            pass
        return

    if action == "del" and len(parts) >= 3:
        rid = int(parts[2])
        adv = get_euro_advert_by_rowid(rid)
        if not adv or _owner_uid(adv) != uid:
            await q.answer()
            return
        if user_advert_has_active_offers(rid):
            await q.answer("پیشنهاد فعال دارید.", show_alert=True)
            return
        await q.answer()
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ حذف قطعی", callback_data=f"user_adv|dely|{rid}"
                    ),
                    InlineKeyboardButton("❌ خیر", callback_data=f"user_adv|m|{rid}"),
                ]
            ]
        )
        try:
            await q.edit_message_text(
                f"{_RTL}⚠️ آگهی <b>#{rid}</b> از کانال و ربات حذف شود؟",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        except Exception:
            pass
        return

    if action == "dely" and len(parts) >= 3:
        rid = int(parts[2])
        ok, mid, ccid = delete_euro_advert_for_owner(rid, uid)
        await q.answer("حذف شد." if ok else "حذف نشد.", show_alert=not ok)
        if ok:
            cid = ccid if ccid is not None else ADVERT_CHANNEL_ID
            if cid and mid:
                try:
                    await context.bot.delete_message(
                        chat_id=int(cid), message_id=int(mid)
                    )
                except Exception:
                    pass
            try:
                await q.message.delete()
            except Exception:
                pass
            await send_or_replace_main_menu(
                context.bot,
                chat_id=q.message.chat_id,
                user_id=uid,
                store=user_data_store,
            )
        return

    if action in ("e_rate", "e_amt", "e_desc") and len(parts) >= 3:
        rid = int(parts[2])
        adv = get_euro_advert_by_rowid(rid)
        if not adv or _owner_uid(adv) != uid:
            await q.answer()
            return
        if user_advert_has_active_offers(rid):
            await q.answer("پیشنهاد فعال دارید.", show_alert=True)
            return
        field_map = {"e_rate": "rate_toman", "e_amt": "euro_amount", "e_desc": "description"}
        field = field_map[action]
        await q.answer()
        context.user_data["state"] = UserState.USER_EDIT_OWN_ADVERT.name
        context.user_data["user_edit_advert_id"] = rid
        context.user_data["user_edit_advert_field"] = field
        prompts = {
            "rate_toman": "نرخ جدید را به تومان (فقط عدد) بفرستید:",
            "euro_amount": "مقدار یورو جدید را بفرستید:",
            "description": "توضیحات جدید را بفرستید (۲ تا ۳۵۰۰ نویسه):",
        }
        try:
            await q.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            q.message.chat_id,
            f"{_RTL}{prompts[field]}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ انصراف", callback_data="user_adv|editcancel")]]
            ),
        )
        return

    if action == "editcancel":
        await q.answer()
        context.user_data.pop("user_edit_advert_id", None)
        context.user_data.pop("user_edit_advert_field", None)
        context.user_data["state"] = UserState.MAIN_MENU.name
        try:
            await q.message.delete()
        except Exception:
            pass
        await _send_my_adverts_list(context.bot, q.message.chat_id, uid)
        return


async def handle_user_own_advert_edit_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.message:
        return
    if context.user_data.get("state") != UserState.USER_EDIT_OWN_ADVERT.name:
        return
    rid = context.user_data.get("user_edit_advert_id")
    field = context.user_data.get("user_edit_advert_field")
    uid = update.effective_user.id
    if not isinstance(rid, int) or field not in ("rate_toman", "euro_amount", "description"):
        return
    if user_advert_has_active_offers(rid):
        await update.message.reply_text(
            f"{_RTL}با پیشنهاد <b>فعال</b> (در انتظار یا پذیرفته‌شده) نمی‌توانید آگهی را ویرایش کنید.",
            parse_mode=ParseMode.HTML,
        )
        return
    raw = (update.message.text or "").strip()
    if field in ("rate_toman", "euro_amount"):
        digits = re.findall(r"\d+", raw)
        if not digits:
            await update.message.reply_text(f"{_RTL}عدد معتبر وارد کنید.")
            return
        val = str(int("".join(digits)))
    else:
        val = raw
    if not update_euro_advert_field_for_owner(rid, uid, field, val):
        await update.message.reply_text(
            f"{_RTL}ذخیره نشد (مقدار نامعتبر یا آگهی دیگر قابل ویرایش نیست)."
        )
        return
    try:
        await update.message.delete()
    except Exception:
        pass
    await refresh_advert_channel_post(context.bot, rid)
    context.user_data.pop("user_edit_advert_id", None)
    context.user_data.pop("user_edit_advert_field", None)
    context.user_data["state"] = UserState.MAIN_MENU.name
    adv_after = get_euro_advert_by_rowid(rid)
    link_html = advert_public_link_html(adv_after, rid)
    await update.effective_chat.send_message(
        f"{_RTL}✅ ذخیره شد.\n{_RTL}🔗 {link_html}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(MY_ADVERTS_REPLY_BUTTON_TEXT, callback_data="main_my_adverts")]]
        ),
        disable_web_page_preview=True,
    )
