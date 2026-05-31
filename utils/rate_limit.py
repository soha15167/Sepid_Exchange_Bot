"""
utils/rate_limit.py — Rate limiting / محدودیت نرخ درخواست

EN: SQLite-backed sliding window counters (survives restarts).
FA: شمارنده در دیتابیس برای OTP، پیشنهاد و مذاکره.
"""

from __future__ import annotations

import sqlite3
import time

from config.settings import DB_PATH

_WINDOW_SEC = 3600


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def ensure_rate_limit_schema() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limit_events (
                bucket TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_bucket "
            "ON rate_limit_events (bucket, created_at)"
        )
        conn.commit()


def _prune(conn: sqlite3.Connection, bucket: str, window_sec: float) -> None:
    cutoff = time.time() - window_sec
    conn.execute(
        "DELETE FROM rate_limit_events WHERE bucket = ? AND created_at < ?",
        (bucket, cutoff),
    )


def check_rate_limit(
    bucket: str,
    *,
    max_events: int,
    window_sec: float = _WINDOW_SEC,
) -> bool:
    """
    Returns True if allowed (under limit), False if rate limited.
    Records this attempt when allowed.
    """
    if not bucket or max_events < 1:
        return True
    ensure_rate_limit_schema()
    now = time.time()
    with _conn() as conn:
        _prune(conn, bucket, window_sec)
        row = conn.execute(
            "SELECT COUNT(*) FROM rate_limit_events WHERE bucket = ?",
            (bucket,),
        ).fetchone()
        count = int(row[0] or 0) if row else 0
        if count >= max_events:
            return False
        conn.execute(
            "INSERT INTO rate_limit_events (bucket, created_at) VALUES (?, ?)",
            (bucket, now),
        )
        conn.commit()
    return True


def otp_bucket(telegram_id: int) -> str:
    return f"otp:{int(telegram_id)}"


def offer_bucket(telegram_id: int, advert_rowid: int) -> str:
    return f"offer:{int(telegram_id)}:{int(advert_rowid)}"


def negotiation_bucket(telegram_id: int, offer_id: int) -> str:
    return f"neg:{int(telegram_id)}:{int(offer_id)}"
