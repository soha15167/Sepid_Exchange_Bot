#!/usr/bin/env python3
"""Diagnose why deal admin sync may not send."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def resolve_offer_id(raw: int) -> list[int]:
    import sqlite3

    from config.settings import DB_PATH
    from database.db import get_advert_offer_joined

    row = get_advert_offer_joined(raw)
    if row:
        return [int(raw)]
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT id FROM advert_offers WHERE advert_rowid = ? ORDER BY id DESC LIMIT 5",
            (int(raw),),
        )
        return [int(r[0]) for r in cur.fetchall()]


async def diagnose(offer_ids: list[int]) -> int:
    from telegram import Bot

    from config.settings import ADMIN_IDS, ADMIN_NOTIFY_CHAT_IDS, BOT_TOKEN
    from database.db import deal_gate_get, get_advert_offer_joined
    from handlers.deal_gate import (
        _admin_receipt_slides_plan,
        sync_deal_admin_notification,
    )
    from handlers.offers import _deal_admin_recipient_ids

    print("ADMIN_IDS:", ADMIN_IDS)
    print("ADMIN_NOTIFY_CHAT_IDS:", ADMIN_NOTIFY_CHAT_IDS)
    print("recipients:", _deal_admin_recipient_ids())
    print()

    bot = Bot(BOT_TOKEN)
    for oid in offer_ids:
        gate = deal_gate_get(oid)
        row = get_advert_offer_joined(oid)
        print(f"=== offer_id {oid} ===")
        print("  gate exists:", bool(gate))
        print("  offer row exists:", bool(row))
        if row:
            print("  advert_rowid:", row.get("advert_rowid"))
            print("  seq_in_advert:", row.get("seq_in_advert"))
        if gate:
            print("  admin_notify_mids:", gate.get("admin_notify_mids"))
            print("  admin_notify_photo_mids:", gate.get("admin_notify_photo_mids"))
            slides = _admin_receipt_slides_plan(gate, oid)
            print("  receipt slides:", len(slides))
            for i, (fid, cap) in enumerate(slides):
                print(f"    [{i}] fid={str(fid)[:24]}... cap={cap[:50]!r}")
        if not gate or not row:
            print("  SKIP: no gate or offer row — wrong id?")
            continue
        print("  running sync...")
        await sync_deal_admin_notification(bot, oid, deal_complete=True)
        gate2 = deal_gate_get(oid)
        print("  after sync admin_notify_mids:", (gate2 or {}).get("admin_notify_mids"))
        print("  after sync admin_notify_photo_mids:", (gate2 or {}).get("admin_notify_photo_mids"))
        print()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "ids",
        type=int,
        nargs="+",
        help="offer_id or advert_rowid",
    )
    p.add_argument(
        "--resolve-ad",
        action="store_true",
        help="if id is advert_rowid, find offer ids",
    )
    args = p.parse_args()
    oids: list[int] = []
    for raw in args.ids:
        if args.resolve_ad:
            oids.extend(resolve_offer_id(raw))
        else:
            oids.append(raw)
    if not oids:
        print("no offer ids found")
        raise SystemExit(1)
    raise SystemExit(asyncio.run(diagnose(oids)))
