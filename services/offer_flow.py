"""Web offer flow — same rules as handlers/offers.py (Telegram bot)."""

from __future__ import annotations

from database.db import (
    delete_pending_offers_for_proposer_on_advert,
    effective_offer_euro_amount_for_advert,
    get_euro_advert_by_rowid,
    get_user,
    insert_advert_offer,
    list_accepted_offers_for_advert,
    proposer_has_pending_offer_on_advert,
    rejected_offer_same_rate_and_euro,
)
from handlers.offers import (
    _advert_euro_amount_int,
    _advert_rate_toman_int,
    _offer_rate_after_rejection_error,
    _offer_requires_proposer_bank_country,
    _offer_requires_proposer_country_step,
    _offer_requires_proposer_recipient_country,
    _offer_skips_toman_rate_step,
    _proposer_same_rate_blocked,
    dispatch_offer_created_notifications,
    _public_offer_name,
)
from utils.rate_limit import check_rate_limit, offer_bucket
from messages.user_errors import RATE_LIMIT_OFFER


def get_offer_flow_config(advert_id: int, user_id: int) -> dict | None:
    adv = get_euro_advert_by_rowid(advert_id)
    if not adv:
        return None

    uid = int(user_id)
    owner_id = int(adv.get("user_id") or 0)
    skips_rate = _offer_skips_toman_rate_step(adv)
    needs_country = _offer_requires_proposer_country_step(adv)
    recipient = _offer_requires_proposer_recipient_country(adv)
    bank = _offer_requires_proposer_bank_country(adv)

    blocked: str | None = None
    if owner_id == uid:
        blocked = "نمی‌توانید به آگهی خود پیشنهاد دهید."
    elif list_accepted_offers_for_advert(advert_id):
        blocked = "برای این آگهی پیشنهادی پذیرفته شده؛ پیشنهاد جدید ممکن نیست."
    elif not get_user(uid):
        blocked = "ابتدا ثبت‌نام کنید."

    if recipient:
        country_label = "کشور حساب دریافت‌کننده"
        country_hint = (
            "آگهی‌دهنده می‌تواند یورو را به حساب شما واریز کند — کشور حساب دریافت را بنویسید."
        )
    elif bank:
        country_label = "کشور حساب بانکی"
        country_hint = "کشور حساب بانکی خود را وارد کنید (مثال: آلمان)."
    else:
        country_label = ""
        country_hint = ""

    try:
        adv_eur = int(adv.get("euro_amount") or 0)
    except (TypeError, ValueError):
        adv_eur = 0
    try:
        adv_rate = int(adv.get("rate_toman") or 0)
    except (TypeError, ValueError):
        adv_rate = 0

    return {
        "advert_id": int(advert_id),
        "advert_euro_amount": adv_eur,
        "advert_rate_toman": adv_rate,
        "skips_toman_rate": skips_rate,
        "requires_account_country": needs_country,
        "account_country_kind": "recipient" if recipient else ("bank" if bank else None),
        "account_country_label": country_label,
        "account_country_hint": country_hint,
        "is_exchange": skips_rate,
        "blocked_reason": blocked,
        "has_pending": proposer_has_pending_offer_on_advert(advert_id, uid) if not blocked else False,
    }


def validate_and_submit_offer(
    *,
    advert_id: int,
    user_id: int,
    mode: str,
    rate_toman: int,
    description: str,
    proposed_euro_amount: int | None = None,
    proposer_account_country: str | None = None,
) -> tuple[dict | None, str | None]:
    """
    Returns (result_dict, error_message).
    mode: 'agree' | 'custom'
    """
    adv = get_euro_advert_by_rowid(advert_id)
    if not adv:
        return None, "آگهی یافت نشد."

    uid = int(user_id)
    aid = int(advert_id)

    cfg = get_offer_flow_config(aid, uid)
    if not cfg:
        return None, "آگهی یافت نشد."
    if cfg.get("blocked_reason"):
        return None, str(cfg["blocked_reason"])

    if int(adv.get("user_id") or 0) == uid:
        return None, "نمی‌توانید به آگهی خود پیشنهاد دهید."

    if list_accepted_offers_for_advert(aid):
        return None, "برای این آگهی پیشنهادی پذیرفته شده؛ پیشنهاد جدید ممکن نیست."

    desc = (description or "").strip()
    if len(desc) < 2:
        return None, "توضیحات پیشنهاد باید حداقل ۲ کاراکتر باشد."

    skips_rate = _offer_skips_toman_rate_step(adv)
    rate = int(rate_toman or 0)
    if skips_rate:
        rate = 0
    elif rate <= 0:
        return None, "نرخ پیشنهادی باید بزرگ‌تر از صفر باشد."

    if _proposer_same_rate_blocked(aid, uid, rate):
        if rate == 0 and skips_rate:
            return None, "برای این آگهی هنوز پیشنهاد شما در انتظار تأیید است."
        return None, "با این نرخ قبلاً برای این آگهی پیشنهاد داده‌اید."

    counter = (mode or "").strip().lower() == "custom"
    proposed_euro: int | None = None

    if counter:
        adv_amt = _advert_euro_amount_int(adv)
        draft_amt = proposed_euro_amount
        adv_rate = _advert_rate_toman_int(adv)
        amount_changed = isinstance(draft_amt, int) and draft_amt > 0 and draft_amt != adv_amt
        rate_changed = False
        if not skips_rate:
            rate_changed = adv_rate is None or int(rate) != int(adv_rate)
        desc_ok = len(desc) >= 2
        if not (amount_changed or rate_changed or desc_ok):
            return None, (
                "حداقل مقدار یورو، نرخ تومان، یا توضیحات را نسبت به آگهی تغییر دهید."
            )
        if amount_changed and draft_amt:
            proposed_euro = int(draft_amt)
        elif isinstance(draft_amt, int) and draft_amt > 0:
            proposed_euro = int(draft_amt)
    elif proposed_euro_amount is not None and int(proposed_euro_amount) > 0:
        # ignore extra field in agree mode
        pass

    eff = effective_offer_euro_amount_for_advert(aid, proposed_euro)
    rej_err = _offer_rate_after_rejection_error(
        adv,
        rate,
        proposer_telegram_id=uid,
        effective_euro_amount=eff,
        proposed_euro_amount=proposed_euro,
    )
    if rej_err:
        # strip HTML for web
        import re

        plain = re.sub(r"<[^>]+>", "", rej_err).replace("\u200f", "").strip()
        return None, plain or "این نرخ/مقدار قابل قبول نیست."

    prop_ctry: str | None = None
    if _offer_requires_proposer_country_step(adv):
        raw_c = (proposer_account_country or "").strip()
        if len(raw_c) < 2:
            if _offer_requires_proposer_recipient_country(adv):
                return None, "کشور حساب دریافت را وارد کنید."
            return None, "کشور حساب بانکی را وارد کنید."
        prop_ctry = raw_c

    if not check_rate_limit(offer_bucket(uid, aid), max_events=12, window_sec=3600):
        return None, RATE_LIMIT_OFFER.replace("\u200f", "").strip()

    delete_pending_offers_for_proposer_on_advert(aid, uid)

    db_user = get_user(uid) or {}
    alias = (db_user.get("display_name") or "").strip() or None

    ins = insert_advert_offer(
        aid,
        uid,
        rate,
        desc,
        offer_alias_name=alias,
        proposer_account_country=prop_ctry,
        proposed_euro_amount=proposed_euro,
    )
    if ins is None:
        if rejected_offer_same_rate_and_euro(aid, rate, eff):
            rej = _offer_rate_after_rejection_error(
                adv,
                rate,
                proposer_telegram_id=uid,
                effective_euro_amount=eff,
                proposed_euro_amount=proposed_euro,
            )
            import re

            plain = re.sub(r"<[^>]+>", "", rej or "").replace("\u200f", "").strip()
            return None, plain or "پیشنهاد با این نرخ/مقدار پذیرفته نشد."
        return None, "ذخیره پیشنهاد انجام نشد."

    row_id, offer_seq = ins
    return {
        "offer_id": row_id,
        "seq": offer_seq,
        "advert_id": aid,
        "rate_toman": rate,
        "proposed_euro_amount": proposed_euro,
        "effective_euro_amount": eff,
        "description": desc,
        "proposer_account_country": prop_ctry,
        "public_display_name": _public_offer_name(db_user, uid),
    }, None


async def notify_offer_created(bot, *, result: dict, advert_id: int, user_id: int) -> None:
    adv = get_euro_advert_by_rowid(advert_id)
    if not adv or not bot:
        return
    await dispatch_offer_created_notifications(
        bot,
        advert_rowid=int(advert_id),
        proposer_telegram_id=int(user_id),
        offer_row_id=int(result["offer_id"]),
        offer_seq=int(result["seq"]),
        rate_toman=int(result["rate_toman"]),
        description=str(result.get("description") or ""),
        public_display_name=str(result.get("public_display_name") or ""),
        is_admin_proxy=False,
        proposer_account_country=result.get("proposer_account_country"),
        skip_main_menu_refresh_for_proposer=True,
    )
