"""Isolated tests for creating and migrating the SQLite schema."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import db


CRITICAL_TABLES = {
    "users",
    "settings",
    "dm_trackable_messages",
    "dm_main_menu_anchors",
    "euro_adverts",
    "advert_offers",
    "offer_negotiation_lines",
    "offer_bot_outbound_log",
    "offer_deal_gates",
    "admin_audit_log",
}


def _schema_snapshot(path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(path) as conn:
        return conn.execute(
            """
            SELECT type, name, COALESCE(sql, '')
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()


class DatabaseSchemaTests(unittest.TestCase):
    def test_ensure_schema_is_complete_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fresh.db"
            original_path = db.DB_PATH
            try:
                db.DB_PATH = str(path)
                db.ensure_schema()
                first = _schema_snapshot(path)
                with sqlite3.connect(path) as conn:
                    first_cutoff = conn.execute(
                        """
                        SELECT value FROM settings
                        WHERE key = 'admin_toman_reminder_cutoff_at'
                        """
                    ).fetchone()[0]
                    conn.execute(
                        "UPDATE settings SET value = '0' WHERE key = 'bot_enabled'"
                    )
                    conn.commit()
                db.ensure_schema()
                second = _schema_snapshot(path)
            finally:
                db.DB_PATH = original_path

            self.assertEqual(first, second)
            tables = {name for kind, name, _ in second if kind == "table"}
            self.assertTrue(
                CRITICAL_TABLES.issubset(tables),
                f"missing critical tables: {sorted(CRITICAL_TABLES - tables)}",
            )
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                settings = dict(conn.execute("SELECT key, value FROM settings"))
                self.assertEqual(
                    set(settings),
                    {
                        "bot_enabled",
                        "channel_post_template_v",
                        "admin_toman_reminder_cutoff_at",
                    },
                )
                self.assertEqual(settings["bot_enabled"], "0")
                self.assertEqual(settings["channel_post_template_v"], "2")
                self.assertEqual(settings["admin_toman_reminder_cutoff_at"], first_cutoff)
                self.assertGreater(int(first_cutoff), 0)
                for table in CRITICAL_TABLES - {"settings"}:
                    count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    self.assertEqual(count, 0, table)


if __name__ == "__main__":
    unittest.main()
