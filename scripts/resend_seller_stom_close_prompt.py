#!/usr/bin/env python3
"""ارسال مجدد دکمهٔ پایان — فقط معاملاتی که فلو جدید فعال شده (seller_toman_close_enabled_at)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


async def main(offer_ids: list[int] | None) -> int:
    from telegram import Bot

    from config.settings import BOT_TOKEN
    from database.db import deal_gate_list_awaiting_seller_toman_confirm
    from handlers.deal_gate import resend_seller_stom_close_prompt

    if offer_ids:
        oids = [int(x) for x in offer_ids]
    else:
        oids = [
            int(g["offer_id"])
            for g in deal_gate_list_awaiting_seller_toman_confirm()
        ]

    if not oids:
        print("no offers awaiting seller close", file=sys.stderr)
        return 1

    bot = Bot(BOT_TOKEN)
    ok = 0
    for oid in oids:
        if await resend_seller_stom_close_prompt(bot, oid):
            print(f"sent close prompt to seller for offer {oid}")
            ok += 1
        else:
            print(f"SKIP offer {oid}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "ids",
        type=int,
        nargs="*",
        help="offer ids (default: all awaiting seller toman confirm)",
    )
    args = p.parse_args()
    raise SystemExit(asyncio.run(main(args.ids or None)))
