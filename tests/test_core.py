"""Core behavior tests (no Telegram network)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest


class TestEuroFees(unittest.TestCase):
    def test_fee_tiers(self):
        from utils.euro_fees import fee_total_eur

        self.assertEqual(fee_total_eur(400), 2.5)
        self.assertEqual(fee_total_eur(501), round(501 * 0.005, 4))
        self.assertEqual(fee_total_eur(2000), 10.0)


class TestBotEnabled(unittest.TestCase):
    def test_is_bot_enabled(self):
        from database import db

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            old = db.DB_PATH
            db.DB_PATH = path
            with sqlite3.connect(path) as conn:
                conn.execute(
                    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)"
                )
                conn.execute(
                    "INSERT INTO settings VALUES ('bot_enabled', '0')"
                )
                conn.commit()
            self.assertFalse(db.is_bot_enabled())
            db.set_setting("bot_enabled", "1")
            self.assertTrue(db.is_bot_enabled())
        finally:
            db.DB_PATH = old
            try:
                os.remove(path)
            except OSError:
                pass


class TestRateLimit(unittest.TestCase):
    def test_rate_limit_blocks(self):
        from utils import rate_limit

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            old = rate_limit.DB_PATH
            rate_limit.DB_PATH = path
            rate_limit.ensure_rate_limit_schema()
            b = "test:1"
            self.assertTrue(rate_limit.check_rate_limit(b, max_events=2, window_sec=60))
            self.assertTrue(rate_limit.check_rate_limit(b, max_events=2, window_sec=60))
            self.assertFalse(rate_limit.check_rate_limit(b, max_events=2, window_sec=60))
        finally:
            rate_limit.DB_PATH = old
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
