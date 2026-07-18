"""
utils/deal_outbound.py — لاگ پیام‌های ربات به طرفین معامله

EN:
  deal_bot_send_message / deal_bot_send_photo log to offer_bot_outbound_log.
  deal_admin_replay_outbound replays for admin (adm|outlog|).
  Buyer euro receipt copy is sent WITHOUT this log (discreet).

FA:
  هر پیام رسمی ربات به خریدار/فروشنده ذخیره می‌شود؛ ادمین می‌تواند بازپخش کند.
  کپی فیش یورو برای تأیید «نشستن» عمداً لاگ نمی‌شود.
"""

from __future__ import annotations

import html as html_module
import logging

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

from database.db import bot_outbound_log_insert, bot_outbound_log_list

logger = logging.getLogger(__name__)

_RTL = "\u200f"
_PARTY_FA = {"buyer": "خریدار", "seller": "فروشنده", "user": "کاربر"}


def party_for_uid(gate: dict | None, uid: int) -> str:
    if not gate:
        return "user"
    try:
        u = int(uid)
        if u == int(gate.get("buyer_telegram_id") or 0):
            return "buyer"
        if u == int(gate.get("seller_telegram_id") or 0):
            return "seller"
    except (TypeError, ValueError):
        pass
    return "user"


def deal_bot_log_text(
    offer_id: int,
    recipient_telegram_id: int,
    party: str,
    tag: str,
    body_html: str,
) -> None:
    if not (body_html or "").strip():
        return
    try:
        bot_outbound_log_insert(
            offer_id,
            recipient_telegram_id,
            party,
            tag,
            msg_type="text",
            body_html=body_html,
        )
    except Exception:
        logger.exception("deal_outbound: log text failed offer=%s", offer_id)


def _caption_fit(meta: str, body: str, *, limit: int = 1024) -> str:
    cap = meta + (body or "")
    if len(cap) <= limit:
        return cap
    return cap[: limit - 1] + "…"


async def deal_bot_send_message(
    bot: Bot,
    *,
    offer_id: int,
    chat_id: int,
    party: str,
    tag: str,
    text: str,
    parse_mode: str | None = ParseMode.HTML,
    reply_markup=None,
    disable_web_page_preview: bool | None = None,
):
    kwargs: dict = {
        "chat_id": int(chat_id),
        "text": text,
        "parse_mode": parse_mode,
        "reply_markup": reply_markup,
    }
    if disable_web_page_preview is not None:
        kwargs["disable_web_page_preview"] = disable_web_page_preview
    sent = await bot.send_message(**kwargs)
    deal_bot_log_text(offer_id, chat_id, party, tag, text)
    return sent


async def deal_bot_send_photo(
    bot: Bot,
    *,
    offer_id: int,
    chat_id: int,
    party: str,
    tag: str,
    photo_file_id: str,
    caption: str | None = None,
    parse_mode: str | None = ParseMode.HTML,
    reply_markup=None,
):
    sent = await bot.send_photo(
        int(chat_id),
        photo_file_id,
        caption=caption,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
    try:
        bot_outbound_log_insert(
            offer_id,
            chat_id,
            party,
            tag,
            msg_type="photo",
            caption_html=caption or "",
            photo_file_id=photo_file_id,
        )
    except Exception:
        logger.exception("deal_outbound: log photo failed offer=%s", offer_id)
    return sent


async def deal_bot_send_document(
    bot: Bot,
    *,
    offer_id: int,
    chat_id: int,
    party: str,
    tag: str,
    document_file_id: str,
    caption: str | None = None,
    parse_mode: str | None = ParseMode.HTML,
    reply_markup=None,
):
    sent = await bot.send_document(
        int(chat_id),
        document_file_id,
        caption=caption,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
    try:
        bot_outbound_log_insert(
            offer_id,
            chat_id,
            party,
            tag,
            msg_type="document",
            caption_html=caption or "",
            photo_file_id=document_file_id,
        )
    except Exception:
        logger.exception("deal_outbound: log document failed offer=%s", offer_id)
    return sent


async def deal_admin_replay_outbound(
    bot: Bot,
    admin_chat_id: int,
    offer_id: int,
) -> bool:
    """بازپخش پیام‌های ذخیره‌شده برای ادمین (همان متن/عکس)."""
    rows = bot_outbound_log_list(offer_id)
    if not rows:
        return False
    intro = (
        f"{_RTL}📋 <b>پیام‌های ارسالی ربات به طرفین</b>\n"
        f"{_RTL}offer <code>{int(offer_id)}</code> · "
        f"<b>{len(rows)}</b> مورد\n"
        f"{_RTL}<i>ترتیب زمانی ارسال</i>\n"
    )
    try:
        await bot.send_message(
            admin_chat_id,
            intro,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramError as e:
        logger.warning("deal_outbound replay intro failed: %s", e)
        return False

    for i, row in enumerate(rows, start=1):
        party = (row.get("party") or "user").strip().lower()
        party_fa = _PARTY_FA.get(party, party)
        rid = int(row.get("recipient_telegram_id") or 0)
        tag = html_module.escape((row.get("tag") or "پیام").strip())
        meta = (
            f"{_RTL}#{i} → <b>{party_fa}</b> · "
            f"<code>{rid}</code>\n"
            f"🏷 {tag}\n\n"
        )
        mt = (row.get("msg_type") or "text").strip().lower()
        body = (row.get("body_html") or row.get("caption_html") or "").strip()
        if not body:
            body = "—"
        try:
            if mt == "photo" and (row.get("photo_file_id") or "").strip():
                cap = _caption_fit(
                    meta,
                    (row.get("caption_html") or row.get("body_html") or ""),
                )
                await bot.send_photo(
                    admin_chat_id,
                    (row.get("photo_file_id") or "").strip(),
                    caption=cap,
                    parse_mode=ParseMode.HTML,
                )
            else:
                await bot.send_message(
                    admin_chat_id,
                    meta + body,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
        except BadRequest:
            plain = html_module.unescape(
                meta + (body if mt != "photo" else (row.get("caption_html") or ""))
            )
            try:
                await bot.send_message(admin_chat_id, plain[:4096])
            except TelegramError as e2:
                logger.warning("deal_outbound replay item %s failed: %s", i, e2)
        except TelegramError as e:
            logger.warning("deal_outbound replay item %s failed: %s", i, e)
    return True
