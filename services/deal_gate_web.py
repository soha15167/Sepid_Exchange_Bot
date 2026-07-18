"""Deal Gate actions from web API — party confirm + account text."""

from __future__ import annotations

import logging
import time

from telegram import Bot
from telegram.constants import ParseMode

from database.db import (
    deal_gate_append_buyer_receipt,
    deal_gate_append_seller_receipt,
    deal_gate_get,
    deal_gate_upsert,
    get_advert_offer_joined,
    get_euro_advert_by_rowid,
)
from handlers.deal_gate import (
    _commit_party_account,
    _deal_gate_allows_party_receipts,
    _log,
    _notify_buyer_euro_receipt_confirm,
    _on_both_yes,
    _on_gate_rejected,
    sync_deal_admin_notification,
)
from services.offer_owner_actions import WebBotContext
from state import user_data_store

logger = logging.getLogger(__name__)
_RTL = "\u200f"


def _party_role_for_user(gate: dict, user_id: int) -> str | None:
    uid = int(user_id)
    if uid == int(gate.get("buyer_telegram_id") or 0):
        return "buyer"
    if uid == int(gate.get("seller_telegram_id") or 0):
        return "seller"
    return None


def enrich_deal_status(*, gate: dict | None, row: dict, user_id: int) -> dict:
    uid = int(user_id)
    owner = int(row.get("owner_id") or 0)
    proposer = int(row.get("proposer_telegram_id") or 0)
    role = "owner" if uid == owner else "proposer"
    party_role = _party_role_for_user(gate, uid) if gate else None

    st = (gate.get("gate_status") or "").strip().lower() if gate else None
    my_response = None
    my_account_sent = False
    can_respond = False
    can_submit_account = False
    can_submit_receipt = False
    receipt_kind = None

    if gate and party_role:
        my_key = "buyer_response" if party_role == "buyer" else "seller_response"
        acct_key = "buyer_accounts_text" if party_role == "buyer" else "seller_accounts_text"
        my_response = (gate.get(my_key) or "").strip().lower() or None
        my_account_sent = bool((gate.get(acct_key) or "").strip())
        if st == "pending" and my_response not in ("yes", "no"):
            can_respond = True
        if st == "accounts" and my_response == "yes" and not my_account_sent:
            can_submit_account = True
        if _deal_gate_allows_party_receipts(gate):
            can_submit_receipt = True
            receipt_kind = "toman" if party_role == "buyer" else "euro"

    from config.settings import BOT_USERNAME

    bot_link = f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else None

    _STATUS_FA = {
        "pending": "در انتظار تأیید نهایی",
        "accounts": "ثبت حساب بانکی",
        "completed": "تأیید حساب — مرحله پرداخت",
        "closed": "بسته شده",
        "rejected": "رد شده",
    }
    status_label = _STATUS_FA.get(st or "", st or "—")
    needs_telegram_handoff = bool(
        gate
        and not can_submit_receipt
        and (
            st == "completed"
            or (st == "accounts" and not can_respond and not can_submit_account)
        )
    )
    if gate and st == "completed" and not can_submit_receipt:
        telegram_hint = "رسید واریز، تأیید ادمین و تسویه — فقط از ربات تلگرام."
    elif gate and st == "completed" and can_submit_receipt:
        telegram_hint = "فیش را اینجا بفرستید؛ تأیید ادمین و تسویه از ربات تلگرام انجام می‌شود."
    elif gate and st not in ("closed", "rejected"):
        telegram_hint = "مراحل پیشرفته (رسید، تأیید ادمین) از ربات تلگرام انجام می‌شود."
    else:
        telegram_hint = None

    return {
        "offer_id": int(row["id"]),
        "advert_id": row.get("advert_rowid"),
        "offer_status": row.get("status") or "pending",
        "role": role,
        "party_role": party_role,
        "my_response": my_response,
        "can_respond": can_respond,
        "can_submit_account": can_submit_account,
        "can_submit_receipt": can_submit_receipt,
        "receipt_kind": receipt_kind,
        "needs_telegram_handoff": needs_telegram_handoff,
        "bot_link": bot_link,
        "gate": {
            "active": gate is not None,
            "status": st,
            "status_label": status_label,
            "buyer_confirmed": (gate or {}).get("buyer_response") == "yes" if gate else False,
            "seller_confirmed": (gate or {}).get("seller_response") == "yes" if gate else False,
            "buyer_account_sent": bool((gate or {}).get("buyer_accounts_text")) if gate else False,
            "seller_account_sent": bool((gate or {}).get("seller_accounts_text")) if gate else False,
        },
        "telegram_required": gate is not None and st not in ("closed", "rejected"),
        "telegram_hint": telegram_hint,
    }


async def submit_party_response(
    bot: Bot,
    *,
    offer_id: int,
    user_id: int,
    response: str,
) -> tuple[bool, str | None]:
    resp = (response or "").strip().lower()
    if resp not in ("yes", "no"):
        return False, "پاسخ باید yes یا no باشد."

    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "") != "pending":
        return False, "این مرحله دیگر فعال نیست."

    uid = int(user_id)
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    if uid not in (buyer_id, seller_id):
        return False, "شما طرف این معامله نیستید."

    is_buyer = uid == buyer_id
    party = "خریدار" if is_buyer else "فروشنده"
    role_key = "buyer_response" if is_buyer else "seller_response"
    ts_key = "buyer_confirmed_at" if is_buyer else "seller_confirmed_at"
    now = int(time.time())

    deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        **{role_key: resp, ts_key: now},
    )
    _log(
        offer_id,
        f"{party} یورو: {'تأیید نهایی (بله)' if resp == 'yes' else 'رد نهایی (خیر)'} (وب)",
        from_role="buyer" if is_buyer else "seller",
    )

    row = get_advert_offer_joined(offer_id)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    ctx = WebBotContext(bot)

    if resp == "no":
        await _on_gate_rejected(ctx, offer_id, rejector_id=uid, party=party)
        return True, None

    gate = deal_gate_get(offer_id) or gate
    br = (gate.get("buyer_response") or "").strip().lower()
    sr = (gate.get("seller_response") or "").strip().lower()
    if br == "yes" and sr == "yes":
        await _on_both_yes(ctx, offer_id, row, advert)
        return True, None

    other_id = seller_id if is_buyer else buyer_id
    other_party = "فروشنده" if is_buyer else "خریدار"
    other_r = sr if is_buyer else br
    try:
        sent = await bot.send_message(
            uid,
            f"{_RTL}✅ تأیید شما از وب ثبت شد.\n"
            f"{_RTL}منتظر تأیید <b>{other_party} یورو</b> هستیم.",
            parse_mode=ParseMode.HTML,
        )
        from handlers.deal_gate import _track_deal_msg

        _track_deal_msg(user_data_store, uid, offer_id, sent.message_id)
    except Exception:
        logger.exception("deal_gate_web notify self failed uid=%s", uid)

    if other_r == "yes":
        await _on_both_yes(ctx, offer_id, row, advert)
    elif other_r != "no":
        try:
            sent_o = await bot.send_message(
                other_id,
                f"{_RTL}ℹ️ {party} یورو تأیید نهایی را زد (وب).\n"
                f"{_RTL}لطفاً شما هم در وب یا ربات تأیید نهایی را بزنید.",
                parse_mode=ParseMode.HTML,
            )
            from handlers.deal_gate import _track_deal_msg

            _track_deal_msg(user_data_store, other_id, offer_id, sent_o.message_id)
        except Exception:
            logger.exception("deal_gate_web notify other failed uid=%s", other_id)

    return True, None


async def submit_account_text(
    bot: Bot,
    *,
    offer_id: int,
    user_id: int,
    text: str,
) -> tuple[bool, str | None]:
    acct = (text or "").strip()
    if len(acct) < 2:
        return False, "متن حساب خیلی کوتاه است."
    if len(acct) > 2000:
        return False, "متن حساب حداکثر ۲۰۰۰ نویسه."

    gate = deal_gate_get(offer_id)
    if not gate or (gate.get("gate_status") or "") != "accounts":
        return False, "ثبت حساب در این مرحله مجاز نیست."

    uid = int(user_id)
    buyer_id = int(gate["buyer_telegram_id"])
    seller_id = int(gate["seller_telegram_id"])
    if uid not in (buyer_id, seller_id):
        return False, "شما طرف این معامله نیستید."

    party_role = _party_role_for_user(gate, uid)
    if not party_role:
        return False, "نقش شما در معامله مشخص نیست."

    my_key = "buyer_response" if party_role == "buyer" else "seller_response"
    if (gate.get(my_key) or "").strip().lower() != "yes":
        return False, "ابتدا باید تأیید نهایی را بزنید."

    acct_key = "buyer_accounts_text" if party_role == "buyer" else "seller_accounts_text"
    if (gate.get(acct_key) or "").strip():
        return False, "حساب شما قبلاً ثبت شده است."

    ctx = WebBotContext(bot)
    await _commit_party_account(
        ctx,
        gate=gate,
        uid=uid,
        text=acct,
        user_message_id=None,
    )
    return True, None


async def submit_receipt_text(
    bot: Bot,
    *,
    offer_id: int,
    user_id: int,
    text: str,
) -> tuple[bool, str | None]:
    body = (text or "").strip()
    if len(body) < 2:
        return False, "متن فیش را کامل‌تر بنویسید."
    if len(body) > 2000:
        return False, "متن فیش حداکثر ۲۰۰۰ نویسه."

    gate = deal_gate_get(offer_id)
    if not gate or not _deal_gate_allows_party_receipts(gate):
        return False, "ارسال فیش در این مرحله مجاز نیست."

    uid = int(user_id)
    party_role = _party_role_for_user(gate, uid)
    if not party_role:
        return False, "شما طرف این معامله نیستید."

    oid = int(offer_id)
    advert_rowid = int(gate.get("advert_rowid") or 0)

    if party_role == "seller":
        if int(gate.get("seller_telegram_id") or 0) != uid:
            return False, "شما طرف این معامله نیستید."
        items = deal_gate_append_seller_receipt(oid, entry_type="text", text=body)
        gate = deal_gate_get(oid) or gate
        idx = len(items) - 1
        _log(oid, f"فیش یورو متنی فروشنده ({len(body)} کاراکتر) (وب)", from_role="seller")
        await _notify_buyer_euro_receipt_confirm(
            bot,
            offer_id=oid,
            gate=gate,
            receipt_index=idx,
            entry_type="text",
            text=body,
        )
    else:
        if int(gate.get("buyer_telegram_id") or 0) != uid:
            return False, "شما طرف این معامله نیستید."
        deal_gate_append_buyer_receipt(oid, entry_type="text", text=body)
        _log(oid, f"فیش واریز متنی خریدار ({len(body)} کاراکتر) (وب)", from_role="buyer")

    await sync_deal_admin_notification(bot, oid, deal_complete=True)
    return True, None


async def submit_receipt_photo(
    bot: Bot,
    *,
    offer_id: int,
    user_id: int,
    file_bytes: bytes,
    filename: str,
    caption: str = "",
) -> tuple[bool, str | None]:
    if not file_bytes or len(file_bytes) < 32:
        return False, "فایل تصویر نامعتبر است."
    if len(file_bytes) > 10 * 1024 * 1024:
        return False, "حداکثر حجم تصویر ۱۰ مگابایت."

    gate = deal_gate_get(offer_id)
    if not gate or not _deal_gate_allows_party_receipts(gate):
        return False, "ارسال فیش در این مرحله مجاز نیست."

    uid = int(user_id)
    if uid <= 0:
        return False, "ارسال عکس فیش فقط برای حساب متصل به تلگرام ممکن است."

    party_role = _party_role_for_user(gate, uid)
    if not party_role:
        return False, "شما طرف این معامله نیستید."

    from telegram import InputFile

    oid = int(offer_id)
    cap = (caption or "").strip()[:400]
    try:
        uploaded = await bot.send_photo(
            uid,
            photo=InputFile(file_bytes, filename=filename or "receipt.jpg"),
            caption=f"{_RTL}✅ فیش از وب ثبت شد.",
        )
        file_id = uploaded.photo[-1].file_id if uploaded.photo else ""
    except Exception:
        logger.exception("deal_gate_web receipt photo upload failed uid=%s offer=%s", uid, oid)
        return False, "آپلود تصویر به تلگرام ناموفق بود."

    if not file_id:
        return False, "آپلود تصویر ناموفق بود."

    if party_role == "seller":
        if int(gate.get("seller_telegram_id") or 0) != uid:
            return False, "شما طرف این معامله نیستید."
        items = deal_gate_append_seller_receipt(
            oid, entry_type="photo", text=cap, file_id=file_id
        )
        gate = deal_gate_get(oid) or gate
        idx = len(items) - 1
        _log(oid, "فیش یورو عکس فروشنده (وب)", from_role="seller")
        await _notify_buyer_euro_receipt_confirm(
            bot,
            offer_id=oid,
            gate=gate,
            receipt_index=idx,
            entry_type="photo",
            text=cap,
            file_id=file_id,
        )
    else:
        if int(gate.get("buyer_telegram_id") or 0) != uid:
            return False, "شما طرف این معامله نیستید."
        deal_gate_append_buyer_receipt(oid, entry_type="photo", text=cap, file_id=file_id)
        _log(oid, "فیش واریز عکس خریدار (وب)", from_role="buyer")

    await sync_deal_admin_notification(bot, oid, deal_complete=True)
    return True, None
