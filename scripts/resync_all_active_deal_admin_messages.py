#!/usr/bin/env python3
"""بازسازی پیام اصلی ادمین برای همهٔ معاملات در فاز واریز (gate_status=completed)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def list_offer_ids(*, include_closed: bool = False) -> list[int]:
    from config.settings import DB_PATH

    statuses = ("completed", "closed") if include_closed else ("completed",)
    placeholders = ",".join("?" for _ in statuses)
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            f"""
            SELECT offer_id
            FROM offer_deal_gates
            WHERE gate_status IN ({placeholders})
            ORDER BY offer_id ASC
            """,
            statuses,
        ).fetchall()
    return [int(r[0]) for r in rows]


async def main(offer_ids: list[int]) -> int:
    from telegram import Bot

    from config.settings import BOT_TOKEN
    from database.db import deal_gate_get, get_advert_offer_joined
    from handlers.deal_gate import sync_deal_admin_notification

    if not offer_ids:
        print("no offers to resync", file=sys.stderr)
        return 1

    bot = Bot(BOT_TOKEN)
    ok = 0
    for oid in offer_ids:
        gate = deal_gate_get(oid)
        row = get_advert_offer_joined(oid)
        if not gate or not row:
            print(f"SKIP offer {oid}: gate={bool(gate)} row={bool(row)}", file=sys.stderr)
            continue
        st = (gate.get("gate_status") or "").strip().lower()
        deal_complete = st in ("completed", "closed")
        await sync_deal_admin_notification(
            bot,
            oid,
            deal_complete=deal_complete,
            force_album_rebuild=True,
        )
        print(
            f"resynced offer {oid} "
            f"(advert {row.get('advert_rowid')}, status {st})"
        )
        ok += 1
    return 0 if ok else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Resync admin deal messages for all active payment-phase gates."
    )
    p.add_argument(
        "--include-closed",
        action="store_true",
        help="also resync gate_status=closed",
    )
    p.add_argument(
        "ids",
        type=int,
        nargs="*",
        help="optional explicit offer_ids (default: all completed gates)",
    )
    args = p.parse_args()
    oids = args.ids if args.ids else list_offer_ids(include_closed=args.include_closed)
    raise SystemExit(asyncio.run(main(oids)))
