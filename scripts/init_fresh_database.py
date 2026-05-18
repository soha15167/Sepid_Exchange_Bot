#!/usr/bin/env python3
"""
دیتابیس خالی برای راه‌اندازی تازه (ثبت‌نام از اول).
قبل از اجرا: ربات را متوقف کنید. فایل قبلی به‌صورت خودکار rename می‌شود.

  python scripts/init_fresh_database.py
  python scripts/init_fresh_database.py --db /root/telegram_bot_project2/eurobot.db
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import DB_PATH  # noqa: E402
from database.db import ensure_schema  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize empty bot database")
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help="Path to SQLite file (default: DATABASE_NAME from .env)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing file without backup prompt (use only if sure)",
    )
    args = parser.parse_args()
    db_path = Path(args.db).resolve()

    if db_path.exists():
        if not args.force:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = db_path.with_name(f"{db_path.stem}.backup-{stamp}{db_path.suffix}")
            shutil.move(str(db_path), str(backup))
            print(f"Backed up existing database to:\n  {backup}")
        else:
            db_path.unlink()
            print(f"Removed: {db_path}")

    ensure_schema()
    print(f"Fresh database ready:\n  {db_path}")
    print("Tables: users, euro_adverts, advert_offers, settings, offer_negotiation_lines")
    print("Next: start the bot with Sepid .env (BOT_TOKEN, ADVERT_CHANNEL_ID, …).")


if __name__ == "__main__":
    main()
