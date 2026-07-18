#!/usr/bin/env python3
"""ارسال دستی حساب یوروی خریدار به فروشنده برای یک offer (پشتیبانی)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


async def main(offer_id: int) -> int:
    from telegram import Bot
    from telegram.ext import ContextTypes

    from config.settings import BOT_TOKEN
    from database.db import deal_gate_get, deal_gate_upsert
    from handlers.deal_gate import _send_buyer_eur_account_to_seller

    gate = deal_gate_get(offer_id)
    if not gate:
        print(f"offer {offer_id}: gate not found")
        return 1

    now = __import__("time").time()
    upsert = {
        "offer_id": offer_id,
        "advert_rowid": int(gate["advert_rowid"]),
        "buyer_telegram_id": int(gate["buyer_telegram_id"]),
        "seller_telegram_id": int(gate["seller_telegram_id"]),
    }
    if not int(gate.get("buyer_toman_settled_at") or 0):
        upsert["buyer_toman_settled_at"] = int(now)
    if not int(gate.get("buyer_toman_card_sent_at") or 0):
        upsert["buyer_toman_card_sent_at"] = int(now)
    deal_gate_upsert(**upsert)
    gate = deal_gate_get(offer_id) or gate

    from types import SimpleNamespace

    bot = Bot(BOT_TOKEN)
    ctx = SimpleNamespace(bot=bot)
    ok = await _send_buyer_eur_account_to_seller(ctx, offer_id, gate)
    print(f"offer {offer_id}: send={'ok' if ok else 'FAILED'} seller={gate.get('seller_telegram_id')}")
    return 0 if ok else 2


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("offer_id", type=int)
    raise SystemExit(asyncio.run(main(p.parse_args().offer_id)))
