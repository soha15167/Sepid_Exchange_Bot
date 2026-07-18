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


class TestAdminNotifyPhotoMidsParse(unittest.TestCase):
    def test_parse_with_media_group_mode(self):
        import json

        from handlers.deal_gate import (
            _all_stored_admin_photo_mids,
            _parse_admin_notify_photo_mids,
        )

        gate = {
            "admin_notify_photo_mids": json.dumps(
                {
                    "5809748588": {
                        "album": [111, 112],
                        "fids": ["AgACabc"],
                        "by_fid": {"AgACabc": 111},
                        "mode": "media_group",
                    }
                }
            )
        }
        parsed = _parse_admin_notify_photo_mids(gate)
        self.assertIn(5809748588, parsed)
        self.assertEqual(parsed[5809748588]["mode"], "media_group")
        self.assertEqual(parsed[5809748588]["album"], [111, 112])
        mids = _all_stored_admin_photo_mids(gate, 5809748588)
        self.assertEqual(mids, {111, 112})

    def test_parse_empty_album_not_fatal(self):
        import json

        from handlers.deal_gate import _parse_admin_notify_photo_mids

        gate = {
            "admin_notify_photo_mids": json.dumps(
                {"1": {"album": [], "fids": [], "by_fid": {}, "mode": "media_group"}}
            )
        }
        parsed = _parse_admin_notify_photo_mids(gate)
        self.assertEqual(parsed.get(1, {}).get("mode"), "media_group")


class TestDealAdminStepsChecklist(unittest.TestCase):
    def test_step4_done_when_toman_settled_without_buyer_receipt(self):
        from handlers.offers import _deal_admin_steps_checklist_html

        html = _deal_admin_steps_checklist_html(
            {
                "offer_id": 170,
                "buyer_telegram_id": 1,
                "seller_telegram_id": 2,
                "buyer_response": "yes",
                "seller_response": "yes",
                "buyer_accounts_text": "iban",
                "seller_accounts_text": "iban",
                "buyer_toman_card_sent_at": 1,
                "buyer_toman_settled_at": 99,
                "seller_eur_account_sent_at": 1,
                "gate_status": "completed",
            }
        )
        self.assertIn("✅ <b>4.</b>", html)
        self.assertNotIn("⏳ <b>4.</b>", html)


if __name__ == "__main__":
    unittest.main()
