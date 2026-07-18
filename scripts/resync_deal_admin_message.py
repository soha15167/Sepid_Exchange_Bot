#!/usr/bin/env python3
"""بازسازی پیام اصلی ادمین برای یک یا چند offer."""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def resolve_offer_ids(raw_ids: list[int], *, by_advert: bool) -> list[int]:
    from config.settings import DB_PATH
    from database.db import deal_gate_get, get_advert_offer_joined

    out: list[int] = []
    seen: set[int] = set()
    for raw in raw_ids:
        if not by_advert:
            oid = int(raw)
            if oid not in seen:
                seen.add(oid)
                out.append(oid)
            continue
        row = get_advert_offer_joined(raw)
        if row and deal_gate_get(int(raw)):
            oid = int(raw)
            if oid not in seen:
                seen.add(oid)
                out.append(oid)
            continue
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT o.id
                FROM advert_offers o
                INNER JOIN offer_deal_gates g ON g.offer_id = o.id
                WHERE o.advert_rowid = ?
                ORDER BY o.id DESC
                """,
                (int(raw),),
            ).fetchall()
        if not rows:
            print(
                f"WARN: advert_rowid {raw} — no active deal gate found",
                file=sys.stderr,
            )
            continue
        for (oid,) in rows:
            oid = int(oid)
            if oid not in seen:
                seen.add(oid)
                out.append(oid)
    return out


async def main(offer_ids: list[int]) -> int:
    from telegram import Bot

    from config.settings import BOT_TOKEN
    from database.db import deal_gate_get, get_advert_offer_joined
    from handlers.deal_gate import sync_deal_admin_notification

    bot = Bot(BOT_TOKEN)
    ok = 0
    for oid in offer_ids:
        gate = deal_gate_get(oid)
        row = get_advert_offer_joined(oid)
        if not gate or not row:
            print(
                f"SKIP offer {oid}: gate={bool(gate)} row={bool(row)} "
                f"(3272 is usually advert id — use --ad 3272 or offer id)",
                file=sys.stderr,
            )
            continue
        await sync_deal_admin_notification(
            bot, oid, deal_complete=True, force_album_rebuild=True
        )
        print(
            f"resynced offer {oid} "
            f"(advert {row.get('advert_rowid')}, seq {row.get('seq_in_advert')})"
        )
        ok += 1
    return 0 if ok else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("ids", type=int, nargs="+", help="offer_id or with --ad: advert_rowid")
    p.add_argument(
        "--ad",
        action="store_true",
        help="ids are advert_rowid (e.g. 3272 on channel post)",
    )
    args = p.parse_args()
    oids = resolve_offer_ids(args.ids, by_advert=args.ad)
    if not oids:
        print("no offer ids to sync", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(asyncio.run(main(oids)))
