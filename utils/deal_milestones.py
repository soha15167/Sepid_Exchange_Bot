"""
utils/deal_milestones.py — اعلان وضعیت معامله به طرف مربوط

EN: Short milestone messages (toman settled, euro settled, deal closed).
    Logged via deal_bot_send_message; duplicate sends skipped by outbound tag.

FA: پیام کوتاه «منتظر باشید» فقط برای طرف درست؛ بدون تکرار با tag لاگ.
"""

from __future__ import annotations

import html as html_module
import logging

from telegram import Bot
from telegram.error import Forbidden, TelegramError

from database.db import bot_outbound_log_list, get_advert_offer_joined
from utils.deal_outbound import deal_bot_send_message, party_for_uid

logger = logging.getLogger(__name__)

_RTL = "\u200f"

MILESTONE_TOMAN_SETTLED_BUYER = "وضعیت: تومان نشست (خریدار)"
MILESTONE_EURO_SETTLED_SELLER = "وضعیت: یورو نشست (فروشنده)"
MILESTONE_TOMAN_TO_SELLER_BUYER = "وضعیت: فیش تومان به فروشنده (خریدار)"
MILESTONE_DEAL_CLOSED = "وضعیت: بستن معامله"
MILESTONE_SELLER_STOM_REMINDER = "یادآوری: تأیید پایان فروشنده"

_TAG_BY_MILESTONE = {
    "toman_settled_buyer": MILESTONE_TOMAN_SETTLED_BUYER,
    "euro_settled_seller": MILESTONE_EURO_SETTLED_SELLER,
    "toman_to_seller_buyer": MILESTONE_TOMAN_TO_SELLER_BUYER,
    "deal_closed": MILESTONE_DEAL_CLOSED,
}


def outbound_milestone_sent(offer_id: int, recipient_id: int, tag: str) -> bool:
    rid = int(recipient_id)
    t = (tag or "").strip()
    if rid <= 0 or not t:
        return False
    for row in bot_outbound_log_list(int(offer_id)):
        if int(row.get("recipient_telegram_id") or 0) != rid:
            continue
        if (row.get("tag") or "").strip() == t:
            return True
    return False


def _ids_line(aid: int, seq: int) -> str:
    return f"{_RTL}آگهی <b>{aid}</b> · پیشنهاد <b>{seq}</b>\n"


def _resolve_ids(
    offer_id: int,
    gate: dict,
    aid: int | None = None,
    seq: int | None = None,
) -> tuple[int, int]:
    row = get_advert_offer_joined(int(offer_id))
    if aid is None:
        aid = int(gate.get("advert_rowid") or (row or {}).get("advert_rowid") or 0)
    if seq is None:
        seq = int((row or {}).get("seq_in_advert") or offer_id)
    return int(aid), int(seq)


def _milestone_text(
    milestone: str,
    *,
    aid: int,
    seq: int,
    who_fa: str = "",
) -> str | None:
    if milestone == "toman_settled_buyer":
        return (
            f"{_RTL}✅ <b>تومان نشست</b>\n\n"
            f"{_ids_line(aid, seq)}"
            f"{_RTL}ادمین تأیید کرد مبلغ تومان شما نزد کانال امانت مانده است.\n"
            f"{_RTL}منتظر <b>واریز یورو از طرف فروشنده</b> به حساب شما هستیم."
        )
    if milestone == "euro_settled_seller":
        conf = (who_fa or "خریدار").strip()
        return (
            f"{_RTL}✅ <b>یورو نشست</b>\n\n"
            f"{_ids_line(aid, seq)}"
            f"{_RTL}{conf} تأیید کرد مبلغ یورو به حساب خریدار واریز شده است.\n"
            f"{_RTL}منتظر <b>واریز تومان از ادمین</b> به حساب شما باشید."
        )
    if milestone == "toman_to_seller_buyer":
        return (
            f"{_RTL}✅ <b>تومان به فروشنده واریز شد</b>\n\n"
            f"{_ids_line(aid, seq)}"
            f"{_RTL}ادمین فیش واریز تومان را برای فروشنده ارسال کرد.\n"
            f"{_RTL}فروشنده پس از دریافت تومان، <b>پایان معامله</b> را تأیید می‌کند."
        )
    if milestone == "deal_closed":
        who = (who_fa or "ادمین").strip()
        return (
            f"{_RTL}⛔ <b>معامله بسته شد</b>\n\n"
            f"{_ids_line(aid, seq)}"
            f"{_RTL}این معامله و آگهی توسط <b>{html_module.escape(who)}</b> بسته شد."
        )
    return None


async def notify_deal_milestone(
    bot: Bot,
    *,
    offer_id: int,
    gate: dict,
    milestone: str,
    recipient_id: int,
    party: str | None = None,
    aid: int | None = None,
    seq: int | None = None,
    who_fa: str = "",
) -> bool:
    rid = int(recipient_id)
    if rid <= 0:
        return False
    tag = _TAG_BY_MILESTONE.get(milestone, "")
    if not tag:
        logger.warning("notify_deal_milestone: unknown milestone %s", milestone)
        return False
    if outbound_milestone_sent(offer_id, rid, tag):
        return False
    aid, seq = _resolve_ids(offer_id, gate, aid, seq)
    text = _milestone_text(milestone, aid=aid, seq=seq, who_fa=who_fa)
    if not text:
        return False
    p = party or party_for_uid(gate, rid)
    try:
        await deal_bot_send_message(
            bot,
            offer_id=int(offer_id),
            chat_id=rid,
            party=p,
            tag=tag,
            text=text,
            disable_web_page_preview=True,
        )
        return True
    except Forbidden:
        logger.warning(
            "deal_milestone: blocked uid=%s offer=%s %s",
            rid,
            offer_id,
            milestone,
        )
    except TelegramError as e:
        logger.warning(
            "deal_milestone: send failed uid=%s offer=%s %s: %s",
            rid,
            offer_id,
            milestone,
            e,
        )
    return False


async def notify_toman_settled_buyer(
    bot: Bot, *, offer_id: int, gate: dict
) -> bool:
    buyer_id = int(gate.get("buyer_telegram_id") or 0)
    return await notify_deal_milestone(
        bot,
        offer_id=int(offer_id),
        gate=gate,
        milestone="toman_settled_buyer",
        recipient_id=buyer_id,
        party="buyer",
    )


async def notify_euro_settled_seller(
    bot: Bot,
    *,
    offer_id: int,
    gate: dict,
    who_fa: str = "خریدار",
) -> bool:
    seller_id = int(gate.get("seller_telegram_id") or 0)
    return await notify_deal_milestone(
        bot,
        offer_id=int(offer_id),
        gate=gate,
        milestone="euro_settled_seller",
        recipient_id=seller_id,
        party="seller",
        who_fa=who_fa,
    )


async def notify_toman_to_seller_buyer(
    bot: Bot, *, offer_id: int, gate: dict
) -> bool:
    buyer_id = int(gate.get("buyer_telegram_id") or 0)
    return await notify_deal_milestone(
        bot,
        offer_id=int(offer_id),
        gate=gate,
        milestone="toman_to_seller_buyer",
        recipient_id=buyer_id,
        party="buyer",
    )


async def notify_deal_closed_parties(
    bot: Bot,
    *,
    offer_id: int,
    gate: dict,
    aid: int | None = None,
    closed_by_fa: str = "ادمین",
) -> None:
    for uid_key, party in (
        ("buyer_telegram_id", "buyer"),
        ("seller_telegram_id", "seller"),
    ):
        uid = int(gate.get(uid_key) or 0)
        if uid:
            await notify_deal_milestone(
                bot,
                offer_id=int(offer_id),
                gate=gate,
                milestone="deal_closed",
                recipient_id=uid,
                party=party,
                aid=aid,
                who_fa=closed_by_fa,
            )


async def notify_admins_deal_closed_by_seller(
    bot: Bot,
    *,
    offer_id: int,
    gate: dict,
    aid: int | None = None,
) -> None:
    """اعلان کوتاه به ادمین‌ها وقتی فروشنده معامله را می‌بندد."""
    from telegram.constants import ParseMode

    from handlers.offers import _deal_admin_recipient_ids

    aid, seq = _resolve_ids(offer_id, gate, aid, None)
    text = (
        f"{_RTL}✅ <b>فروشنده معامله را بست</b>\n\n"
        f"{_ids_line(aid, seq)}"
        f"{_RTL}فروشنده تأیید کرد تومان نشسته و معامله بسته شد."
    )
    for admin_id in _deal_admin_recipient_ids():
        try:
            await bot.send_message(
                int(admin_id),
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Forbidden:
            logger.warning(
                "deal_milestone: admin notify blocked uid=%s offer=%s",
                admin_id,
                offer_id,
            )
        except TelegramError as e:
            logger.warning(
                "deal_milestone: admin notify failed uid=%s offer=%s: %s",
                admin_id,
                offer_id,
                e,
            )
