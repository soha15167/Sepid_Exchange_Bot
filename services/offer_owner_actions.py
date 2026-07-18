"""Owner/proposer offer actions from web — mirrors handlers/offers.py accept/reject/withdraw."""

from __future__ import annotations

import logging
from collections import defaultdict
from types import SimpleNamespace

from telegram import Bot
from telegram.constants import ParseMode

from database.db import (
    delete_advert_offer_if_pending,
    get_advert_offer_joined,
    get_euro_advert_by_rowid,
    get_user,
    reject_other_pending_offers_for_advert,
    update_advert_offer_status,
    update_proposer_pending_offer_rate,
)
from handlers.offers import (
    neg_transcript_append,
    negotiation_cleanup_for_offer,
    purge_offer_thread_messages,
)
from services.advert_publish import refresh_advert_on_channel
from state import user_data_store

logger = logging.getLogger(__name__)
_RTL = "\u200f"


class WebBotContext:
    """Minimal telegram Context for deal_gate from web API."""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.application = SimpleNamespace(
            bot_data={},
            user_data=defaultdict(dict),
            job_queue=None,
        )
        self.user_data = {}


def _offer_public_name(meta: dict) -> str:
    alias = (meta.get("offer_alias_name") or "").strip()
    if alias:
        return alias
    uid = int(meta.get("proposer_telegram_id") or 0)
    u = get_user(uid) if uid else None
    if u:
        dn = (u.get("display_name") or "").strip()
        if dn:
            return dn
    return f"کاربر {uid}" if uid else "—"


async def _notify(bot: Bot, chat_id: int, text: str) -> None:
    if not chat_id:
        return
    try:
        await bot.send_message(
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await bot.send_message(
                chat_id,
                text.replace("<b>", "").replace("</b>", ""),
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("notify failed chat=%s", chat_id)


async def accept_offer_as_owner(
    bot: Bot,
    *,
    offer_id: int,
    owner_id: int,
) -> tuple[bool, str | None]:
    row = get_advert_offer_joined(offer_id)
    if not row:
        return False, "پیشنهاد پیدا نشد."
    if int(row.get("owner_id") or 0) != int(owner_id):
        return False, "فقط صاحب آگهی می‌تواند پیشنهاد را بپذیرد."

    st = (row.get("status") or "pending").strip().lower()
    if st == "accepted":
        return False, "این پیشنهاد قبلاً پذیرفته شده است."
    if st == "rejected":
        return False, "این پیشنهاد قبلاً رد شده است."
    if not update_advert_offer_status(offer_id, "accepted"):
        return False, "ذخیره نشد."

    proposer_id = int(row["proposer_telegram_id"])
    aid = int(row["advert_rowid"])
    seq = int(row.get("seq_in_advert") or offer_id)

    for other_oid in reject_other_pending_offers_for_advert(aid, offer_id):
        ometa = get_advert_offer_joined(other_oid)
        if not ometa:
            continue
        await purge_offer_thread_messages(
            bot, user_data_store, int(ometa["owner_id"]), int(ometa["proposer_telegram_id"]), other_oid
        )
        negotiation_cleanup_for_offer({}, other_oid)
        opid = int(ometa["proposer_telegram_id"])
        oseq = int(ometa.get("seq_in_advert") or other_oid)
        if opid and opid != proposer_id:
            await _notify(
                bot,
                opid,
                f"{_RTL}پیشنهاد شماره <b>{oseq}</b> برای آگهی <b>{aid}</b> "
                f"<b>رد شد</b> (پیشنهاد دیگری پذیرفته شد).",
            )

    advert = get_euro_advert_by_rowid(aid)
    app_data: dict = {}
    neg_transcript_append(app_data, offer_id, "owner", f"صاحب آگهی پیشنهاد #{seq} را پذیرفت (آگهی #{aid})")
    neg_transcript_append(
        app_data,
        offer_id,
        "system",
        "پیام‌های چت این پیشنهاد پاک شد؛ ادامه در تأیید نهایی",
    )
    await purge_offer_thread_messages(bot, user_data_store, int(owner_id), proposer_id, offer_id)
    negotiation_cleanup_for_offer(app_data, offer_id)
    await refresh_advert_on_channel(aid)

    if advert:
        from handlers.deal_gate import start_deal_final_gate

        ctx = WebBotContext(bot)
        ctx.application.bot_data = app_data
        await start_deal_final_gate(ctx, offer_id=offer_id, row=row, advert=advert)
    else:
        await _notify(
            bot,
            owner_id,
            f"{_RTL}✅ <b>پیشنهاد {seq}</b> برای آگهی <b>{aid}</b> پذیرفته شد.",
        )
        await _notify(
            bot,
            proposer_id,
            f"{_RTL}✅ صاحب آگهی، پیشنهاد شماره <b>{seq}</b> (آگهی <b>{aid}</b>) را پذیرفت.",
        )

    return True, None


async def reject_offer_as_owner(
    bot: Bot,
    *,
    offer_id: int,
    owner_id: int,
) -> tuple[bool, str | None]:
    row = get_advert_offer_joined(offer_id)
    if not row:
        return False, "پیشنهاد پیدا نشد."
    if int(row.get("owner_id") or 0) != int(owner_id):
        return False, "فقط صاحب آگهی می‌تواند پیشنهاد را رد کند."

    st = (row.get("status") or "pending").strip().lower()
    if st == "accepted":
        return False, "پیشنهاد قبلاً پذیرفته شده؛ نمی‌توان رد کرد."

    update_advert_offer_status(offer_id, "rejected")
    proposer_id = int(row["proposer_telegram_id"])
    aid = int(row["advert_rowid"])
    seq = int(row.get("seq_in_advert") or offer_id)

    await purge_offer_thread_messages(
        bot, user_data_store, int(owner_id), proposer_id, offer_id
    )
    negotiation_cleanup_for_offer({}, offer_id)
    await refresh_advert_on_channel(aid)

    await _notify(
        bot,
        int(owner_id),
        f"{_RTL}⭕️ پیشنهاد <b>{seq}</b> برای آگهی <b>{aid}</b> رد شد.",
    )
    await _notify(
        bot,
        proposer_id,
        f"{_RTL}⭕️ پیشنهاد شماره <b>{seq}</b> برای آگهی <b>{aid}</b> توسط آگهی‌دهنده رد شد.",
    )
    return True, None


async def withdraw_offer_as_proposer(
    bot: Bot | None,
    *,
    offer_id: int,
    proposer_id: int,
) -> tuple[bool, str | None]:
    ok, advert_rowid = delete_advert_offer_if_pending(offer_id, proposer_id)
    if not ok:
        return False, "پیشنهاد قابل حذف نیست (پذیرفته شده یا نامعتبر)."
    if advert_rowid:
        await refresh_advert_on_channel(advert_rowid)
    return True, None


async def update_offer_rate_as_proposer(
    bot: Bot | None,
    *,
    offer_id: int,
    proposer_id: int,
    rate_toman: int,
) -> tuple[bool, str | None]:
    advert_rowid = update_proposer_pending_offer_rate(offer_id, proposer_id, rate_toman)
    if advert_rowid is None:
        return False, "ویرایش نرخ ممکن نیست."
    await refresh_advert_on_channel(advert_rowid)
    return True, None


def enrich_offer_row(row: dict) -> dict:
    meta = row if "owner_id" in row else get_advert_offer_joined(int(row.get("id") or 0)) or row
    proposer_id = int(meta.get("proposer_telegram_id") or 0)
    return {
        "id": meta.get("id"),
        "advert_id": meta.get("advert_rowid"),
        "seq": meta.get("seq_in_advert"),
        "rate_toman": meta.get("rate_toman"),
        "description": meta.get("description"),
        "status": meta.get("status") or "pending",
        "proposed_euro_amount": meta.get("proposed_euro_amount"),
        "proposer_account_country": meta.get("proposer_account_country"),
        "proposer_id": proposer_id,
        "proposer_name": _offer_public_name(meta),
        "owner_id": meta.get("owner_id"),
    }
