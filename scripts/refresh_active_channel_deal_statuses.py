#!/usr/bin/env python3
"""Refresh public channel posts for active deal gates after status-format changes."""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def active_deal_advert_ids() -> list[int]:
    from config.settings import DB_PATH

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT advert_rowid
            FROM offer_deal_gates
            WHERE lower(trim(COALESCE(gate_status, ''))) IN (
                'pending', 'accounts', 'completed', 'rejected'
            )
            ORDER BY advert_rowid
            """
        ).fetchall()
    return [int(row[0]) for row in rows]


async def refresh_active_posts(
    *,
    dry_run: bool = False,
    advert_ids: list[int] | None = None,
    attempts: int = 3,
    delay_seconds: float = 2.0,
) -> int:
    from telegram import Bot
    from telegram.request import HTTPXRequest

    from config.settings import BOT_TOKEN
    from handlers.offers import refresh_advert_channel_post

    advert_ids = advert_ids or active_deal_advert_ids()
    print("active_adverts:", " ".join(map(str, advert_ids)) or "none")
    if dry_run:
        return 0

    refreshed = 0
    failed: list[int] = []
    request = HTTPXRequest(
        connect_timeout=15.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=15.0,
    )
    async with Bot(BOT_TOKEN, request=request) as bot:
        for advert_id in advert_ids:
            ok = False
            for attempt in range(1, max(1, attempts) + 1):
                ok = await refresh_advert_channel_post(bot, advert_id)
                if ok:
                    break
                if attempt < attempts:
                    retry_delay = max(delay_seconds, attempt * 3.0)
                    print(
                        f"retry advert {advert_id}: "
                        f"attempt {attempt + 1}/{attempts} in {retry_delay:.1f}s"
                    )
                    await asyncio.sleep(retry_delay)
            if ok:
                refreshed += 1
                print(f"refreshed advert {advert_id}")
            else:
                failed.append(advert_id)
                print(f"FAILED advert {advert_id}")
            await asyncio.sleep(max(0.0, delay_seconds))
    print(f"refreshed_total: {refreshed}")
    print("failed_adverts:", " ".join(map(str, failed)) or "none")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list active advert ids without editing Telegram messages",
    )
    parser.add_argument(
        "--advert",
        action="append",
        type=int,
        help="refresh only this advert row id (may be supplied more than once)",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=3,
        help="maximum Telegram edit attempts per advert (default: 3)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="seconds to wait between adverts (default: 2.0)",
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            refresh_active_posts(
                dry_run=args.dry_run,
                advert_ids=args.advert,
                attempts=max(1, args.attempts),
                delay_seconds=max(0.0, args.delay),
            )
        )
    )
