#!/usr/bin/env python3
"""ارسال دستی کارت واریز تومان به خریدار (پشتیبانی)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


async def main(offer_id: int, *, card_id: str | None = None) -> int:
    from telegram import Bot
    from telegram.constants import ParseMode

    from config.settings import BANK_CARDS, BOT_TOKEN
    from database.db import deal_gate_get, deal_gate_upsert, get_advert_offer_joined, get_euro_advert_by_rowid
    from handlers.deal_gate import (
        _buyer_toman_deposit_message_html,
        _buyer_toman_pay_keyboard,
        _track_pay_card_msg,
        sync_deal_admin_notification,
    )
    from handlers.offers import (
        _offer_effective_euro_amount,
        buyer_deposit_toman_amount,
    )
    from state import user_data_store
    from utils.bank_cards import format_bank_card_html, parse_bank_cards
    from utils.deal_outbound import deal_bot_send_message

    _RTL = "\u200f"

    gate = deal_gate_get(offer_id)
    if not gate:
        print(f"offer {offer_id}: gate not found")
        return 1
    row = get_advert_offer_joined(offer_id)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
    if not row or not advert:
        print(f"offer {offer_id}: offer/advert not found")
        return 1

    cards = parse_bank_cards(BANK_CARDS)
    if not cards:
        print("BANK_CARDS_JSON empty on server")
        return 1
    picked = next((c for c in cards if c.id == card_id), None) if card_id else cards[0]
    if not picked:
        print(f"card id {card_id!r} not found; available:", [c.id for c in cards])
        return 1

    buyer_id = int(gate["buyer_telegram_id"])
    pe_raw = int(row.get("proposed_euro_amount") or 0)
    pe_kw = pe_raw if pe_raw > 0 else None
    eur_amt = _offer_effective_euro_amount(advert, pe_kw)
    amount = buyer_deposit_toman_amount(advert, row)
    seq = int(row.get("seq_in_advert") or offer_id)
    aid = int(row["advert_rowid"])
    card_html = format_bank_card_html(picked)

    msg = _buyer_toman_deposit_message_html(
        advert_id=aid,
        offer_sequence=seq,
        euro_amount=eur_amt,
        toman_amount=amount,
        card_html=card_html,
    )

    bot = Bot(BOT_TOKEN)
    try:
        sent = await deal_bot_send_message(
            bot,
            offer_id=offer_id,
            chat_id=buyer_id,
            party="buyer",
            tag="کارت واریز تومان به خریدار",
            text=msg,
            reply_markup=_buyer_toman_pay_keyboard(offer_id),
            disable_web_page_preview=True,
        )
        _track_pay_card_msg(user_data_store, buyer_id, offer_id, sent.message_id)
    except Exception as e:
        print(f"send FAILED buyer={buyer_id}: {e}")
        return 2

    deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=buyer_id,
        seller_telegram_id=int(gate["seller_telegram_id"]),
        buyer_toman_card_sent_at=int(time.time()),
    )
    await sync_deal_admin_notification(bot, offer_id, deal_complete=True)
    print(
        f"offer {offer_id} advert {aid}: card '{picked.title}' sent to buyer {buyer_id} "
        f"amount={amount:,} toman"
    )
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("offer_id", type=int)
    p.add_argument("--card-id", default=None)
    raise SystemExit(asyncio.run(main(p.parse_args().offer_id, card_id=p.parse_args().card_id)))
