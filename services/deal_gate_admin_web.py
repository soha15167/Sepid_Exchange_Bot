"""Admin deal-gate actions from web — mirrors handlers/deal_gate.py admin callbacks."""

from __future__ import annotations

import html as html_module
import logging
import time
from types import SimpleNamespace

from telegram import Bot, InputFile

from config.settings import BANK_CARDS
from database.db import (
    bot_outbound_log_list,
    deal_gate_append_buyer_receipt,
    deal_gate_append_seller_receipt,
    deal_gate_append_seller_toman_admin,
    deal_gate_enable_seller_toman_close,
    deal_gate_get,
    deal_gate_upsert,
    get_advert_offer_joined,
    get_euro_advert_by_rowid,
)
from handlers.deal_gate import (
    _account_photo_saved_text,
    _admin_proxy_party_final_yes,
    _admin_send_toman_deposit_card,
    _apply_euro_settled,
    _buyer_toman_card_delivered,
    _deal_gate_allows_admin_payment,
    _deal_gate_allows_party_receipts,
    refresh_admin_deal_markup,
    _first_unconfirmed_seller_euro_index,
    _log,
    _notify_buyer_euro_receipt_confirm,
    _photo_caption_html,
    _seller_buyer_eur_account_delivered,
    _send_buyer_eur_account_to_seller,
    admin_save_party_account,
    deal_admin_main_keyboard,
    seller_toman_settled_keyboard,
    sync_deal_admin_notification,
)
from handlers.offers import (
    _post_acceptance_admin_message_html,
    _seller_euro_fully_confirmed_gate,
)
from services.offer_owner_actions import WebBotContext
from utils.bank_cards import display_bank_title, parse_bank_cards

logger = logging.getLogger(__name__)
_RTL = "\u200f"


class _WebAdminAck:
    """Minimal callback_query stand-in for bot handlers invoked from web."""

    def __init__(self, admin_id: int):
        self.from_user = SimpleNamespace(id=int(admin_id))
        self.message = SimpleNamespace(chat_id=int(admin_id))
        self.last_answer: str | None = None

    async def answer(self, text: str = "", show_alert: bool = False, **kwargs) -> None:
        if text:
            self.last_answer = text


def _keyboard_to_actions(markup) -> list[dict]:
    actions: list[dict] = []
    if not markup or not getattr(markup, "inline_keyboard", None):
        return actions
    for row in markup.inline_keyboard:
        for btn in row:
            cb = btn.callback_data or ""
            parts = cb.split("|")
            item: dict = {"label": btn.text, "callback": cb}
            if len(parts) >= 3 and parts[0] == "adm":
                kind = parts[1]
                item["kind"] = kind
                if kind == "pxy" and len(parts) == 4:
                    item["sub"] = parts[3]
                elif kind == "pay":
                    if len(parts) == 3:
                        item["sub"] = "menu"
                    elif len(parts) == 4:
                        item["sub"] = parts[3]
                elif kind == "eurcfm" and len(parts) == 4:
                    item["receipt_index"] = int(parts[3])
                elif kind == "stom" and len(parts) >= 4:
                    item["sub"] = parts[3]
            actions.append(item)
    return actions


def _bank_cards_for_web() -> list[dict]:
    cards = parse_bank_cards(BANK_CARDS)
    return [
        {"id": c.id, "title": display_bank_title(c.title) or c.title}
        for c in cards
    ]


def build_deal_admin_panel(offer_id: int) -> dict | None:
    gate = deal_gate_get(offer_id)
    row = get_advert_offer_joined(offer_id)
    if not gate or not row:
        return None
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"]))
    if not advert:
        return None

    oid = int(offer_id)
    seq = int(row.get("seq_in_advert") or oid)
    aid = int(row["advert_rowid"])
    st = (gate.get("gate_status") or "").strip().lower()
    buyer_acct = (gate.get("buyer_accounts_text") or "").strip()
    seller_acct = (gate.get("seller_accounts_text") or "").strip()

    if st == "completed":
        deal_complete = True
        accounts_mode = False
        keyboard = deal_admin_main_keyboard(oid, gate, include_payment=True)
    elif st in ("pending", "accounts"):
        deal_complete = False
        accounts_mode = st == "accounts"
        keyboard = deal_admin_main_keyboard(oid, gate, include_payment=False)
    else:
        deal_complete = False
        accounts_mode = False
        keyboard = None

    admin_html = _post_acceptance_admin_message_html(
        advert,
        row,
        seq,
        aid,
        buyer_accounts_text=buyer_acct or None,
        seller_accounts_text=seller_acct or None,
        accounts_status_mode=accounts_mode,
        deal_complete=deal_complete,
        embed_account_photos=False,
        embed_receipt_photos=False,
        receipt_slides_mode=deal_complete,
        gate=gate,
    )

    unconfirmed_eur_idx = _first_unconfirmed_seller_euro_index(oid)
    return {
        "offer_id": oid,
        "advert_id": aid,
        "seq": seq,
        "gate_status": st,
        "admin_html": admin_html,
        "actions": _keyboard_to_actions(keyboard),
        "bank_cards": _bank_cards_for_web(),
        "unconfirmed_eur_receipt_index": unconfirmed_eur_idx,
        "gate": gate,
    }


async def _upload_telegram_photo(
    bot: Bot, *, chat_id: int, file_bytes: bytes, filename: str
) -> str | None:
    try:
        sent = await bot.send_photo(
            chat_id,
            photo=InputFile(file_bytes, filename=filename or "upload.jpg"),
            caption=f"{_RTL}✅ آپلود وب",
        )
        if sent.photo:
            return sent.photo[-1].file_id
    except Exception:
        logger.exception("deal_gate_admin_web photo upload failed chat=%s", chat_id)
    return None


async def run_proxy_yes(
    bot: Bot, *, admin_id: int, offer_id: int, party: str
) -> tuple[bool, str | None]:
    if party not in ("buyer", "seller"):
        return False, "نقش نامعتبر."
    ctx = WebBotContext(bot)
    ack = _WebAdminAck(admin_id)
    await _admin_proxy_party_final_yes(ctx, int(offer_id), party, ack)
    return True, ack.last_answer


async def run_send_toman_card(
    bot: Bot, *, admin_id: int, offer_id: int, card_id: str
) -> tuple[bool, str | None]:
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_admin_payment(gate):
        return False, "معامله در مرحلهٔ واریز نیست."
    cards = parse_bank_cards(BANK_CARDS)
    picked = next((c for c in cards if c.id == card_id), None)
    if not picked:
        return False, "کارت پیدا نشد."
    ctx = WebBotContext(bot)
    ack = _WebAdminAck(admin_id)
    await _admin_send_toman_deposit_card(ctx, ack, oid, gate, picked)
    if ack.last_answer and "ناموفق" in ack.last_answer:
        return False, ack.last_answer
    await refresh_admin_deal_markup(bot, oid)
    return True, ack.last_answer or "کارت واریز برای خریدار ارسال شد."


async def run_toman_settled(
    bot: Bot, *, admin_id: int, offer_id: int
) -> tuple[bool, str | None]:
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_admin_payment(gate):
        return False, "معامله در این مرحله نیست."
    buyer_id = int(gate.get("buyer_telegram_id") or 0)
    card_ok = int(gate.get("buyer_toman_card_sent_at") or 0) > 0 or (
        _buyer_toman_card_delivered(oid, buyer_id) if buyer_id else False
    )
    if not card_ok:
        return False, "ابتدا کارت واریز به خریدار ارسال شود."
    now = int(time.time())
    upsert_kw: dict = {
        "offer_id": oid,
        "advert_rowid": int(gate["advert_rowid"]),
        "buyer_telegram_id": int(gate["buyer_telegram_id"]),
        "seller_telegram_id": int(gate["seller_telegram_id"]),
        "buyer_toman_settled_at": now,
    }
    if not int(gate.get("buyer_toman_card_sent_at") or 0):
        upsert_kw["buyer_toman_card_sent_at"] = now
    deal_gate_upsert(**upsert_kw)
    gate = deal_gate_get(oid) or gate
    _log(oid, "ادمین تأیید کرد: تومان نشست (وب)", from_role="admin")
    ctx = WebBotContext(bot)
    ack = _WebAdminAck(admin_id)
    ok = await _send_buyer_eur_account_to_seller(ctx, oid, gate, q=ack)
    if not ok:
        deal_gate_upsert(
            offer_id=oid,
            advert_rowid=int(gate["advert_rowid"]),
            buyer_telegram_id=int(gate["buyer_telegram_id"]),
            seller_telegram_id=int(gate["seller_telegram_id"]),
            buyer_toman_settled_at=None,
        )
        return False, ack.last_answer or "ارسال حساب یورو به فروشنده ناموفق بود."
    gate = deal_gate_get(oid) or gate
    from utils.deal_milestones import notify_toman_settled_buyer

    await notify_toman_settled_buyer(bot, offer_id=oid, gate=gate)
    await sync_deal_admin_notification(bot, oid, deal_complete=True)
    await refresh_admin_deal_markup(bot, oid)
    return True, "تومان نشست — حساب یورو برای فروشنده ارسال شد."


async def run_send_buyer_eur_account(
    bot: Bot, *, admin_id: int, offer_id: int
) -> tuple[bool, str | None]:
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not gate:
        return False, "معامله پیدا نشد."
    if int(gate.get("buyer_toman_settled_at") or 0) <= 0:
        return False, "ابتدا «تومان نشست» را بزنید."
    ctx = WebBotContext(bot)
    ack = _WebAdminAck(admin_id)
    ok = await _send_buyer_eur_account_to_seller(ctx, oid, gate, q=ack, force_resend=True)
    if not ok:
        return False, ack.last_answer or "ارسال ناموفق بود."
    await sync_deal_admin_notification(bot, oid, deal_complete=True, text_only=True)
    await refresh_admin_deal_markup(bot, oid)
    return True, ack.last_answer or "حساب خریدار برای فروشنده ارسال شد."


async def run_euro_settled(
    bot: Bot, *, admin_id: int, offer_id: int, receipt_index: int
) -> tuple[bool, str | None]:
    ctx = WebBotContext(bot)
    ack = _WebAdminAck(admin_id)
    await _apply_euro_settled(
        ctx,
        offer_id=int(offer_id),
        receipt_index=int(receipt_index),
        confirmed_by="admin",
        answer_query=ack,
    )
    return True, ack.last_answer or "یورو نشست ثبت شد."


async def run_save_account(
    bot: Bot, *, admin_id: int, offer_id: int, party: str, text: str
) -> tuple[bool, str | None]:
    if party not in ("buyer", "seller"):
        return False, "نقش نامعتبر."
    ctx = WebBotContext(bot)
    err = await admin_save_party_account(ctx, int(offer_id), party, text)
    if err:
        return False, err
    return True, None


async def run_save_account_photo(
    bot: Bot,
    *,
    admin_id: int,
    offer_id: int,
    party: str,
    file_bytes: bytes,
    filename: str,
    caption: str = "",
) -> tuple[bool, str | None]:
    if party not in ("buyer", "seller"):
        return False, "نقش نامعتبر."
    fid = await _upload_telegram_photo(
        bot, chat_id=int(admin_id), file_bytes=file_bytes, filename=filename
    )
    if not fid:
        return False, "آپلود عکس به تلگرام ناموفق بود."
    saved_text = _account_photo_saved_text(extra_caption=(caption or "").strip())
    ctx = WebBotContext(bot)
    err = await admin_save_party_account(
        ctx, int(offer_id), party, saved_text, photo_file_id=fid
    )
    if err:
        return False, err
    return True, None


async def run_proxy_receipt_text(
    bot: Bot, *, admin_id: int, offer_id: int, party: str, text: str
) -> tuple[bool, str | None]:
    body = (text or "").strip()
    if len(body) < 2:
        return False, "متن فیش را کامل‌تر بنویسید."
    if party not in ("buyer", "seller"):
        return False, "نقش نامعتبر."
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_party_receipts(gate):
        return False, "ارسال فیش در این مرحله مجاز نیست."
    if party == "buyer":
        buyer_id = int(gate.get("buyer_telegram_id") or 0)
        card_sent = int(gate.get("buyer_toman_card_sent_at") or 0) > 0 or (
            _buyer_toman_card_delivered(oid, buyer_id) if buyer_id else False
        )
        if not card_sent:
            return False, "ابتدا کارت واریز به خریدار ارسال شود."
        deal_gate_append_buyer_receipt(oid, entry_type="text", text=body)
        _log(oid, "ادمین — فیش تومان متنی خریدار (وب)", from_role="admin")
    else:
        seller_id = int(gate.get("seller_telegram_id") or 0)
        if not int(gate.get("seller_eur_account_sent_at") or 0) and not (
            _seller_buyer_eur_account_delivered(oid, seller_id) if seller_id else False
        ):
            return False, "ابتدا حساب یورو به فروشنده ارسال شود."
        items = deal_gate_append_seller_receipt(oid, entry_type="text", text=body)
        gate = deal_gate_get(oid) or gate
        idx = len(items) - 1
        _log(oid, "ادمین — فیش یورو متنی فروشنده (وب)", from_role="admin")
        await _notify_buyer_euro_receipt_confirm(
            bot,
            offer_id=oid,
            gate=gate,
            receipt_index=idx,
            entry_type="text",
            text=body,
        )
    await sync_deal_admin_notification(bot, oid, deal_complete=True)
    return True, None


async def run_proxy_receipt_photo(
    bot: Bot,
    *,
    admin_id: int,
    offer_id: int,
    party: str,
    file_bytes: bytes,
    filename: str,
    caption: str = "",
) -> tuple[bool, str | None]:
    if party not in ("buyer", "seller"):
        return False, "نقش نامعتبر."
    fid = await _upload_telegram_photo(
        bot, chat_id=int(admin_id), file_bytes=file_bytes, filename=filename
    )
    if not fid:
        return False, "آپلود عکس به تلگرام ناموفق بود."
    cap = (caption or "").strip()
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_party_receipts(gate):
        return False, "ارسال فیش در این مرحله مجاز نیست."
    if party == "buyer":
        deal_gate_append_buyer_receipt(oid, entry_type="photo", text=cap, file_id=fid)
        _log(oid, "ادمین — فیش تومان عکس خریدار (وب)", from_role="admin")
    else:
        items = deal_gate_append_seller_receipt(
            oid, entry_type="photo", text=cap, file_id=fid
        )
        gate = deal_gate_get(oid) or gate
        idx = len(items) - 1
        _log(oid, "ادمین — فیش یورو عکس فروشنده (وب)", from_role="admin")
        await _notify_buyer_euro_receipt_confirm(
            bot,
            offer_id=oid,
            gate=gate,
            receipt_index=idx,
            entry_type="photo",
            text=cap,
            file_id=fid,
        )
    await sync_deal_admin_notification(bot, oid, deal_complete=True)
    return True, None


async def run_seller_toman_receipt_text(
    bot: Bot, *, admin_id: int, offer_id: int, text: str
) -> tuple[bool, str | None]:
    body = (text or "").strip()
    if len(body) < 2:
        return False, "متن فیش را کامل‌تر بنویسید."
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_admin_payment(gate):
        return False, "معامله در این مرحله نیست."
    if not _seller_euro_fully_confirmed_gate(gate):
        return False, "ابتدا یورو باید نشست تأیید شود."
    seller_id = int(gate.get("seller_telegram_id") or 0)
    deal_gate_append_seller_toman_admin(oid, entry_type="text", text=body)
    row = get_advert_offer_joined(oid)
    seq = int((row or {}).get("seq_in_advert") or oid)
    from utils.deal_outbound import deal_bot_send_message

    msg_body = (
        f"{_RTL}💳 <b>فیش واریز تومان</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b>\n\n"
        f"{_RTL}ادمین فیش واریز تومان به شما را ارسال کرد:\n\n"
        f"<pre>{html_module.escape(body[:3500])}</pre>"
    )
    try:
        await deal_bot_send_message(
            bot,
            offer_id=oid,
            chat_id=seller_id,
            party="seller",
            tag="فیش تومان از ادمین",
            text=msg_body,
            disable_web_page_preview=True,
            reply_markup=seller_toman_settled_keyboard(oid),
        )
    except Exception as e:
        logger.warning("deal_gate_admin_web stom text seller=%s: %s", seller_id, e)
        return False, "ارسال به فروشنده ناموفق بود."
    deal_gate_enable_seller_toman_close(oid)
    from utils.deal_milestones import notify_toman_to_seller_buyer

    gate = deal_gate_get(oid) or gate
    await notify_toman_to_seller_buyer(bot, offer_id=oid, gate=gate)
    _log(oid, "ادمین فیش تومان برای فروشنده فرستاد (متن، وب)", from_role="admin")
    await sync_deal_admin_notification(bot, oid, deal_complete=True)
    return True, None


async def run_seller_toman_receipt_photo(
    bot: Bot,
    *,
    admin_id: int,
    offer_id: int,
    file_bytes: bytes,
    filename: str,
    caption: str = "",
) -> tuple[bool, str | None]:
    oid = int(offer_id)
    gate = deal_gate_get(oid)
    if not gate or not _deal_gate_allows_admin_payment(gate):
        return False, "معامله در این مرحله نیست."
    if not _seller_euro_fully_confirmed_gate(gate):
        return False, "ابتدا یورو باید نشست تأیید شود."
    fid = await _upload_telegram_photo(
        bot, chat_id=int(admin_id), file_bytes=file_bytes, filename=filename
    )
    if not fid:
        return False, "آپلود عکس به تلگرام ناموفق بود."
    cap = (caption or "").strip()
    seller_id = int(gate.get("seller_telegram_id") or 0)
    deal_gate_append_seller_toman_admin(oid, entry_type="photo", text=cap, file_id=fid)
    row = get_advert_offer_joined(oid)
    seq = int((row or {}).get("seq_in_advert") or oid)
    from utils.deal_outbound import deal_bot_send_photo

    body = (
        f"{_RTL}💳 <b>فیش واریز تومان</b>\n\n"
        f"{_RTL}پیشنهاد <b>{seq}</b>\n\n"
        f"{_RTL}ادمین فیش واریز تومان به شما را ارسال کرد."
    )
    if cap:
        body += f"\n\n<i>{html_module.escape(cap[:400])}</i>"
    try:
        await deal_bot_send_photo(
            bot,
            offer_id=oid,
            chat_id=seller_id,
            party="seller",
            tag="فیش تومان از ادمین",
            photo_file_id=fid,
            caption=_photo_caption_html(body),
            reply_markup=seller_toman_settled_keyboard(oid),
        )
    except Exception as e:
        logger.warning("deal_gate_admin_web stom photo seller=%s: %s", seller_id, e)
        return False, "ارسال به فروشنده ناموفق بود."
    deal_gate_enable_seller_toman_close(oid)
    from utils.deal_milestones import notify_toman_to_seller_buyer

    gate = deal_gate_get(oid) or gate
    await notify_toman_to_seller_buyer(bot, offer_id=oid, gate=gate)
    _log(oid, "ادمین فیش تومان برای فروشنده فرستاد (عکس، وب)", from_role="admin")
    await sync_deal_admin_notification(bot, oid, deal_complete=True)
    return True, None


async def run_resync(bot: Bot, *, offer_id: int) -> tuple[bool, str | None]:
    gate = deal_gate_get(offer_id)
    if not gate:
        return False, "معامله پیدا نشد."
    st = (gate.get("gate_status") or "").strip().lower()
    deal_complete = st == "completed"
    await sync_deal_admin_notification(
        bot, int(offer_id), deal_complete=deal_complete, text_only=deal_complete
    )
    return True, None


async def run_replay_outbound(
    bot: Bot, *, admin_id: int, offer_id: int
) -> tuple[bool, str | None]:
    from utils.deal_outbound import deal_admin_replay_outbound

    ok = await deal_admin_replay_outbound(bot, int(admin_id), int(offer_id))
    if not ok:
        return False, "پیامی در لاگ نیست یا بازپخش ناموفق بود."
    return True, None


def list_outbound_log(offer_id: int) -> list[dict]:
    return bot_outbound_log_list(offer_id)
