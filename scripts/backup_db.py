#!/usr/bin/env python3
"""Backup SQLite DB — run via cron on server."""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config.settings import DB_PATH  # noqa: E402


def main() -> int:
    src = DB_PATH
    if not os.path.isfile(src):
        print(f"DB not found: {src}")
        return 1
    backup_dir = os.path.join(ROOT, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"{os.path.basename(src)}.{stamp}.bak")
    shutil.copy2(src, dest)
    print(f"OK {dest}")
    # prune old backups (keep 14)
    files = sorted(
        [
            os.path.join(backup_dir, f)
            for f in os.listdir(backup_dir)
            if f.endswith(".bak")
        ],
        key=os.path.getmtime,
    )
    for old in files[:-14]:
        try:
            os.remove(old)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
