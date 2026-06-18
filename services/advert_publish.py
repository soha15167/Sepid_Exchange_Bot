"""
services/advert_publish.py — Publish/update euro adverts to Telegram channel from web API.

EN: Reuses bot formatting; does not modify bot handlers.
FA: انتشار آگهی از وب با همان قالب کانال.
"""

from __future__ import annotations

import logging

from telegram import Bot
from telegram.constants import ParseMode

from config.settings import ADVERT_CHANNEL_ID, BOT_TOKEN, CHANNEL_USERNAME
from database.db import get_db, get_euro_advert_by_rowid
from handlers.offers import channel_ad_reply_markup
from utils.channel_format import format_channel_ad_footer
from utils.channel_format import format_payment_methods_rtl as _format_methods_rtl
from utils.euro_fees import format_fee_eur as _format_fee_eur

logger = logging.getLogger(__name__)


def _format_optional_line(label: str, value: str | None) -> str:
    if not value:
        return ""
    return f"{label} {value}\n"


def _channel_country_html(country_raw: str | None, *, operation: str = "") -> str:
    from utils.channel_format import format_country_display_line

    return format_country_display_line(
        country_raw,
        operation=operation,
        euro_exchange=False,
        html=True,
    )


async def publish_euro_advert_to_channel(advert_rowid: int) -> dict:
    """
    Insert is already done; post to channel and persist channel_message_id.
    Returns {ok, channel_message_id, channel_link}.
    """
    advert = get_euro_advert_by_rowid(advert_rowid)
    if not advert:
        return {"ok": False, "error": "advert_not_found"}
    operation = (advert.get("operation") or "").strip()
    if operation == "معاوضه":
        return await _publish_exchange_advert_to_channel(advert_rowid, advert)
    if not ADVERT_CHANNEL_ID or not BOT_TOKEN:
        return {"ok": False, "error": "channel_not_configured"}

    bot = Bot(token=BOT_TOKEN)
    me = await bot.get_me()
    bot_uname = (me.username or "").strip().lstrip("@")

    operation = (advert.get("operation") or "").strip()
    amount = int(advert.get("euro_amount") or 0)
    rate = int(advert.get("rate_toman") or 0)
    desc = advert.get("description") or "—"
    methods = (advert.get("methods") or "").split(",")
    methods_text = _format_methods_rtl([m.strip() for m in methods if m.strip()])
    full_name = advert.get("full_name") or "—"
    account_country = advert.get("account_country")
    instant_transfer = advert.get("instant_transfer")

    placeholder_link = f"https://t.me/{CHANNEL_USERNAME}/..."
    methods_label_ch = "روش‌های دریافت" if operation == "خرید" else "روش‌های پرداخت"
    methods_block_ch = f"💳 <b>{methods_label_ch}:</b>\n{methods_text}\n\n"

    ad_text = (
        f"📋 <b><a href=\"{placeholder_link}\">آگهی شماره {advert_rowid}</a></b>\n\n"
        f"👤 <b>آگهی‌دهنده:</b> {full_name}\n"
        f"🏷️ <b>نوع آگهی:</b> {'خرید یورو' if operation == 'خرید' else 'فروش یورو'}\n"
        f"{methods_block_ch}"
        f"💶 <b>مقدار:</b> {amount:,} یورو\n"
        f"💰 <b>نرخ:</b> {rate:,} تومان\n"
        f"🧾 <b>کارمزد معامله:</b> {_format_fee_eur(amount)}\n\n"
        f"{_channel_country_html(account_country, operation=operation)}"
        f"{_format_optional_line('⚡ <b>امکان واریز آنی:</b>', instant_transfer)}"
        f"📄 <b>توضیحات:</b> {desc}"
        f"{format_channel_ad_footer(bot_username=bot_uname)}"
    )

    sent_msg = await bot.send_message(
        chat_id=int(ADVERT_CHANNEL_ID),
        text=ad_text,
        parse_mode=ParseMode.HTML,
        reply_markup=channel_ad_reply_markup(int(advert_rowid), bot_uname),
        disable_web_page_preview=True,
    )
    real_link = f"https://t.me/{CHANNEL_USERNAME}/{sent_msg.message_id}"
    updated_text = ad_text.replace(placeholder_link, real_link)
    await bot.edit_message_text(
        chat_id=int(ADVERT_CHANNEL_ID),
        message_id=sent_msg.message_id,
        text=updated_text,
        parse_mode=ParseMode.HTML,
        reply_markup=sent_msg.reply_markup,
        disable_web_page_preview=True,
    )

    with get_db() as conn:
        conn.execute(
            """
            UPDATE euro_adverts
            SET channel_chat_id = ?, channel_message_id = ?, status = 'فعال'
            WHERE rowid = ?
            """,
            (str(ADVERT_CHANNEL_ID), int(sent_msg.message_id), int(advert_rowid)),
        )

    return {
        "ok": True,
        "channel_message_id": sent_msg.message_id,
        "channel_link": real_link,
    }


_RTL = "\u200f"


def _exch_country_line(raw) -> str:
    c = (raw or "").strip()
    if not c or c in ("-", "—", "–"):
        return ""
    return f"🗺️ <b>کشور (خارج از ایران):</b> {c}\n"


async def _publish_exchange_advert_to_channel(advert_rowid: int, advert: dict) -> dict:
    if not ADVERT_CHANNEL_ID or not BOT_TOKEN:
        return {"ok": False, "error": "channel_not_configured"}

    bot = Bot(token=BOT_TOKEN)
    me = await bot.get_me()
    bot_uname = (me.username or "").strip().lstrip("@")

    method = (advert.get("methods") or "-").strip()
    amount = int(advert.get("euro_amount") or 0)
    city_ir = (advert.get("city_ir") or "-").strip()
    country_int = (advert.get("account_country") or "-").strip()
    city_int = (advert.get("city_int") or "-").strip()
    desc = advert.get("description") or "-"
    full_name = advert.get("full_name") or "—"
    instant = advert.get("instant_transfer")

    in_person = method in ("دریافت حضوری", "تحویل حضوری")
    side = "خرید" if method.startswith("امکان دریافت") or method == "دریافت حضوری" else "فروش"
    show_instant = side == "فروش" and method == "امکان واریز به حساب دارم"
    show_foreign_city = in_person

    placeholder_link = f"https://t.me/{CHANNEL_USERNAME}/..."
    rtl_city_ir_line = f"{_RTL}🏙️ <b>شهر ایران:</b> {city_ir}"
    instant_line = f"⚡ <b>امکان واریز آنی:</b> {instant}\n" if (show_instant and instant) else ""
    foreign_city_line = f"🌆 <b>شهر خارج:</b> {city_int}\n" if show_foreign_city else ""
    foreign_country_line = _exch_country_line(country_int)

    ad_text = (
        f"📋 <b><a href=\"{placeholder_link}\">آگهی شماره {advert_rowid}</a></b>\n\n"
        f"👤 <b>آگهی‌دهنده:</b> {full_name}\n"
        f"🏷️ <b>نوع آگهی:</b> {'خرید یورو' if side == 'خرید' else 'فروش یورو'}\n"
        "🔀 <b>روش معاوضه:</b>\n"
        f"{_RTL}یورو به یورو\n\n"
        f"💶 <b>مقدار:</b> {amount:,} یورو\n"
        f"🧾 <b>کارمزد (هر طرف):</b> {_format_fee_eur(amount)}\n\n"
        f"{foreign_country_line}"
        f"{foreign_city_line}"
        f"{rtl_city_ir_line}\n\n"
        f"📦 <b>{'روش دریافت' if side == 'خرید' else 'روش تحویل'}:</b> {method}\n"
        f"{instant_line}\n"
        f"📄 <b>توضیحات:</b> {desc}"
        f"{format_channel_ad_footer(bot_username=bot_uname, euro_exchange_no_rate=True)}"
    )

    try:
        sent_msg = await bot.send_message(
            chat_id=int(ADVERT_CHANNEL_ID),
            text=ad_text,
            parse_mode=ParseMode.HTML,
            reply_markup=channel_ad_reply_markup(int(advert_rowid), bot_uname),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.exception("publish exchange advert failed: %s", exc)
        return {"ok": False, "error": "channel_send_failed"}

    real_link = f"https://t.me/{CHANNEL_USERNAME}/{sent_msg.message_id}"
    updated_text = ad_text.replace(placeholder_link, real_link)
    await bot.edit_message_text(
        chat_id=int(ADVERT_CHANNEL_ID),
        message_id=sent_msg.message_id,
        text=updated_text,
        parse_mode=ParseMode.HTML,
        reply_markup=sent_msg.reply_markup,
        disable_web_page_preview=True,
    )

    with get_db() as conn:
        conn.execute(
            """
            UPDATE euro_adverts
            SET channel_chat_id = ?, channel_message_id = ?, status = 'فعال'
            WHERE rowid = ?
            """,
            (str(ADVERT_CHANNEL_ID), int(sent_msg.message_id), int(advert_rowid)),
        )

    return {
        "ok": True,
        "channel_message_id": sent_msg.message_id,
        "channel_link": real_link,
    }


async def refresh_advert_on_channel(advert_rowid: int) -> bool:
    from handlers.offers import refresh_advert_channel_post

    if not BOT_TOKEN:
        return False
    bot = Bot(token=BOT_TOKEN)
    await refresh_advert_channel_post(bot, int(advert_rowid))
    return True


async def delete_advert_channel_message(channel_chat_id: int | str | None, channel_message_id: int | None) -> None:
    if not BOT_TOKEN or channel_message_id is None or channel_chat_id is None:
        return
    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.delete_message(chat_id=int(channel_chat_id), message_id=int(channel_message_id))
    except Exception as exc:
        logger.warning("delete_advert_channel_message failed: %s", exc)
