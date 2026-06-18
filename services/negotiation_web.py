"""Negotiation messages from web API."""

from __future__ import annotations

import logging

from telegram import Bot

from database.db import get_advert_offer_joined
from handlers.offers import (
    _scrub_for_anonymous_peer,
    _sync_negotiation_panels,
    neg_transcript_append,
)
from state import user_data_store
from utils.rate_limit import check_rate_limit, negotiation_bucket

logger = logging.getLogger(__name__)


async def post_negotiation_message(
    bot: Bot | None,
    *,
    offer_id: int,
    user_id: int,
    text: str,
) -> tuple[bool, str | None]:
    raw = (text or "").strip()
    if not raw:
        return False, "متن خالی است."
    if len(raw) > 2000:
        return False, "متن حداکثر ۲۰۰۰ نویسه."

    uid = int(user_id)
    oid = int(offer_id)
    if not check_rate_limit(
        negotiation_bucket(uid, oid),
        max_events=30,
        window_sec=3600,
    ):
        return False, "تعداد پیام‌ها زیاد است — کمی بعد دوباره تلاش کنید."

    row = get_advert_offer_joined(oid)
    if not row:
        return False, "این مذاکره دیگر فعال نیست."
    st = (row.get("status") or "pending").strip().lower()
    if st != "pending":
        return False, "این پیشنهاد دیگر در وضعیت مذاکره نیست."

    owner = int(row["owner_id"])
    proposer = int(row["proposer_telegram_id"])
    if uid == owner:
        from_role = "owner"
    elif uid == proposer:
        from_role = "proposer"
    else:
        return False, "شما طرف این مذاکره نیستید."

    scrubbed = _scrub_for_anonymous_peer(raw)
    if not scrubbed:
        return False, "متن قابل ارسال نیست؛ از اشتراک شماره، آیدی یا لینک خودداری کنید."

    entries = neg_transcript_append({}, oid, from_role, scrubbed)
    if bot:
        try:
            await _sync_negotiation_panels(bot, user_data_store, {}, row, entries)
        except Exception:
            logger.exception("negotiation_web sync panels failed offer=%s", oid)

    return True, None
