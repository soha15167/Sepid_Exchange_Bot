"""Only one offer may win an advertisement, including concurrent callbacks."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database import db


class AtomicOfferAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "atomic-acceptance.db"
        self.original_path = db.DB_PATH
        db.DB_PATH = str(self.path)
        db.ensure_schema()
        with sqlite3.connect(self.path) as conn:
            conn.executemany(
                """
                INSERT INTO advert_offers (
                    id, advert_rowid, proposer_telegram_id, rate_toman,
                    created_at, status, seq_in_advert
                ) VALUES (?, 4001, ?, ?, '2026-07-18', 'pending', ?)
                """,
                [
                    (501, 1001, 205000, 1),
                    (502, 1002, 206000, 2),
                    (503, 1003, 207000, 3),
                ],
            )
            conn.commit()

    def tearDown(self):
        db.DB_PATH = self.original_path
        self._tmp.cleanup()

    def _statuses(self) -> dict[int, str]:
        with sqlite3.connect(self.path) as conn:
            return {
                int(row[0]): str(row[1])
                for row in conn.execute(
                    "SELECT id, status FROM advert_offers ORDER BY id"
                )
            }

    def test_one_accept_rejects_every_other_pending_offer(self):
        result = db.accept_advert_offer_atomically(502)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["winner_offer_id"], 502)
        self.assertEqual(result["rejected_offer_ids"], [501, 503])
        self.assertEqual(
            self._statuses(),
            {501: "rejected", 502: "accepted", 503: "rejected"},
        )

    def test_simultaneous_accepts_produce_exactly_one_winner(self):
        barrier = threading.Barrier(2)

        def accept(offer_id: int) -> dict:
            barrier.wait(timeout=5)
            return db.accept_advert_offer_atomically(offer_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, (501, 502)))

        self.assertEqual(sum(bool(item["accepted"]) for item in results), 1)
        statuses = self._statuses()
        self.assertEqual(list(statuses.values()).count("accepted"), 1)
        self.assertEqual(list(statuses.values()).count("rejected"), 2)
        loser = next(item for item in results if not item["accepted"])
        self.assertIn(loser["reason"], ("rejected", "winner_exists"))

    def test_existing_gate_keeps_advert_locked_until_reactivation(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE advert_offers SET status = 'gate_rejected' WHERE id = 501"
            )
            conn.execute(
                """
                INSERT INTO offer_deal_gates (
                    offer_id, advert_rowid, buyer_telegram_id, seller_telegram_id,
                    gate_status, started_at
                ) VALUES (501, 4001, 10, 20, 'rejected', 1)
                """
            )
            conn.commit()

        result = db.accept_advert_offer_atomically(502)

        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "winner_exists")
        self.assertEqual(result["winner_offer_id"], 501)
        self.assertEqual(self._statuses()[502], "rejected")


class AtomicOfferAcceptanceHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_losing_callback_does_not_start_a_second_deal(self):
        from handlers import offers

        query = SimpleNamespace(
            data="adv_o|ok|502",
            from_user=SimpleNamespace(id=9001),
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace()
        row = {
            "id": 502,
            "advert_rowid": 4001,
            "owner_id": 9001,
            "proposer_telegram_id": 1002,
            "status": "pending",
        }
        with (
            patch(
                "handlers.access_gate.ensure_registered_or_redirect",
                new=AsyncMock(return_value=False),
            ),
            patch.object(offers, "get_advert_offer_joined", return_value=row),
            patch.object(
                offers,
                "accept_advert_offer_atomically",
                return_value={
                    "accepted": False,
                    "reason": "winner_exists",
                    "winner_offer_id": 501,
                    "rejected_offer_ids": [],
                },
            ),
            patch.object(
                offers,
                "refresh_advert_channel_post",
                new=AsyncMock(),
            ) as refresh_channel,
        ):
            await offers.handle_advert_owner_offer_action(update, context)

        query.answer.assert_awaited_once_with(
            "پیشنهاد دیگری برای این آگهی قبلاً پذیرفته شده است.",
            show_alert=True,
        )
        refresh_channel.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
