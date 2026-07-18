#!/usr/bin/env python3
"""Create a verified online SQLite backup — safe while the bot is running."""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import mkstemp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def resolve_database_path(configured_path: str | os.PathLike[str]) -> Path:
    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def configured_backup_retention() -> int:
    raw_value = (os.getenv("BACKUP_KEEP") or "14").strip()
    try:
        keep = int(raw_value)
    except ValueError as exc:
        raise ValueError("BACKUP_KEEP must be a positive integer") from exc
    if keep < 1:
        raise ValueError("BACKUP_KEEP must be a positive integer")
    return keep


def create_verified_backup(
    source_path: str | os.PathLike[str],
    backup_dir: str | os.PathLike[str],
    *,
    keep: int = 14,
    now: datetime | None = None,
) -> Path:
    """Back up a live SQLite database, verify it, then publish it atomically."""
    if keep < 1:
        raise ValueError("keep must be at least 1")

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"DB not found: {source}")

    destination_dir = Path(backup_dir).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(destination_dir, 0o700)

    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S_%f")
    destination = destination_dir / f"{source.name}.{stamp}.bak"
    fd, temporary_name = mkstemp(
        prefix=f".{source.name}.", suffix=".tmp", dir=destination_dir
    )
    os.close(fd)
    temporary = Path(temporary_name)

    try:
        source_uri = f"{source.as_uri()}?mode=ro"
        with closing(
            sqlite3.connect(source_uri, uri=True, timeout=30.0)
        ) as source_db:
            with closing(sqlite3.connect(temporary, timeout=30.0)) as backup_db:
                source_db.backup(backup_db)
                backup_db.commit()
                result = backup_db.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise RuntimeError(f"backup integrity check failed: {result}")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise

    backups = sorted(
        destination_dir.glob(f"{source.name}.*.bak"),
        key=lambda path: path.stat().st_mtime,
    )
    for old_backup in backups[:-keep]:
        old_backup.unlink()

    return destination


def main() -> int:
    source = resolve_database_path(os.getenv("DATABASE_NAME", "eurobot.db"))
    try:
        destination = create_verified_backup(
            source,
            ROOT / "backups",
            keep=configured_backup_retention(),
        )
    except Exception as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    print(f"OK {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
