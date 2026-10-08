"""EUR settlement must advance past archived receipts without changing indices."""

from __future__ import annotations

import gc
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database import db, receipt_management
from handlers import deal_gate


class AdminEuroSettlementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(db, "DB_PATH", str(Path(self.tmp.name) / "test.db"))
        self.path_patch.start()
        db.ensure_schema()
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=4001,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            gate_status="completed",
            buyer_response="yes",
            seller_response="yes",
            buyer_toman_settled_at=100,
            seller_eur_account_sent_at=100,
        )

    def tearDown(self):
        self.path_patch.stop()
        gc.collect()
        self.tmp.cleanup()

    def _add_receipt(self, file_id, *, removed=False):
        items = db.deal_gate_append_seller_receipt(
            101, entry_type="photo", file_id=file_id
        )
        index = len(items) - 1
        if removed:
            self.assertTrue(
                receipt_management.archive(
                    101, "euro", index,
                    receipt_management.fingerprint(items[index]), 7001,
                )
            )
        return index

    def _euro_buttons(self):
        with (
            patch.object(deal_gate, "_buyer_toman_card_delivered", return_value=False),
            patch.object(deal_gate, "_seller_buyer_eur_account_delivered", return_value=True),
        ):
            rows = deal_gate.deal_admin_payment_only_rows(101)
        return [
            button.callback_data for row in rows for button in row
            if button.callback_data.startswith("adm|eurcfm|")
        ]

    def test_button_skips_archived_receipts_and_preserves_original_index(self):
        self._add_receipt("removed", removed=True)
        self._add_receipt("already-confirmed")
        self.assertTrue(db.deal_gate_confirm_seller_receipt_buyer(101, 1))
        self._add_receipt("pending")

        self.assertEqual(self._euro_buttons(), ["adm|eurcfm|101|2"])

    def test_only_archived_receipts_do_not_offer_settlement(self):
        self._add_receipt("removed", removed=True)
        self.assertEqual(self._euro_buttons(), [])

    async def test_two_click_confirmation_advances_to_next_live_receipt(self):
        self._add_receipt("removed", removed=True)
        self._add_receipt("first-payment")
        self._add_receipt("second-payment")
        context = SimpleNamespace(bot=object(), user_data={})
        query = SimpleNamespace(
            data=self._euro_buttons()[0],
            from_user=SimpleNamespace(id=7001),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_reply_markup=AsyncMock()),
        )
        update = SimpleNamespace(callback_query=query)

        with (
            patch.object(deal_gate, "ADMIN_IDS", [7001]),
            patch.object(deal_gate, "DEAL_SUPPORT_ADMIN_IDS", []),
            patch.object(deal_gate, "_log"),
            patch.object(deal_gate, "sync_deal_admin_notification", new=AsyncMock()),
            patch.object(deal_gate, "_show_user_main_menu", new=AsyncMock()),
            patch("utils.deal_milestones.notify_euro_settled_seller", new=AsyncMock()),
        ):
            await deal_gate.deal_admin_euro_settled_callback(update, context)
            self.assertFalse(db.deal_gate_seller_receipt_list(101)[1]["buyer_confirmed_at"])
            markup = query.message.edit_reply_markup.await_args.kwargs["reply_markup"]
            query.data = markup.inline_keyboard[0][0].callback_data
            self.assertEqual(query.data, "adm|eurcfm|101|1|yes")
            await deal_gate.deal_admin_euro_settled_callback(update, context)

        receipts = db.deal_gate_seller_receipt_list(101)
        self.assertFalse(receipts[0]["buyer_confirmed_at"])
        self.assertGreater(receipts[1]["buyer_confirmed_at"], 0)
        self.assertEqual(receipts[1]["confirmed_by"], "admin")
        self.assertFalse(receipts[2]["buyer_confirmed_at"])
        self.assertEqual(self._euro_buttons(), ["adm|eurcfm|101|2"])

    async def test_old_removed_receipt_button_refreshes_without_confirming_replacement(self):
        self._add_receipt("removed", removed=True)
        self._add_receipt("replacement")
        context = SimpleNamespace(bot=object(), user_data={})
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=7001),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_reply_markup=AsyncMock()),
        )
        with (
            patch.object(deal_gate, "ADMIN_IDS", [7001]),
            patch.object(deal_gate, "DEAL_SUPPORT_ADMIN_IDS", []),
            patch.object(deal_gate, "sync_deal_admin_notification", new=AsyncMock()) as sync,
            patch.object(deal_gate, "_admin_sensitive_confirmation", new=AsyncMock()) as confirm,
        ):
            for callback_data in ("adm|eurcfm|101|0", "adm|eurcfm|101|0|yes"):
                with self.subTest(callback_data=callback_data):
                    query.data = callback_data
                    await deal_gate.deal_admin_euro_settled_callback(
                        SimpleNamespace(callback_query=query), context
                    )
                    self.assertFalse(db.deal_gate_seller_receipt_list(101)[1]["buyer_confirmed_at"])

        self.assertEqual(sync.await_count, 2)
        sync.assert_awaited_with(context.bot, 101, deal_complete=True, text_only=True)
        confirm.assert_not_awaited()
        self.assertEqual(self._euro_buttons(), ["adm|eurcfm|101|1"])


if __name__ == "__main__":
    unittest.main()
