import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from database import db, receipt_management as store
from handlers import receipt_management as flow, deal_gate


class ReceiptManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'db.sqlite')
        self.patcher = patch.object(db, 'DB_PATH', self.path)
        self.patcher.start()
        self.items = [{'type': 'photo', 'file_id': 'wrong', 'reviewer_received_at': 123}, {'type': 'photo', 'file_id': 'correct'}]
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('CREATE TABLE offer_deal_gates (offer_id INTEGER PRIMARY KEY, buyer_receipt_log TEXT)')
            conn.execute('INSERT INTO offer_deal_gates VALUES (?,?)', (1, json.dumps(self.items)))

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_archive_preserves_indices_financial_state_and_audit(self):
        fp = store.fingerprint(self.items[0])
        self.assertTrue(store.archive(1, 'buyer', 0, fp, 42))
        self.assertFalse(store.archive(1, 'buyer', 0, fp, 42))
        with closing(sqlite3.connect(self.path)) as conn, conn:
            items = json.loads(conn.execute('SELECT buyer_receipt_log FROM offer_deal_gates').fetchone()[0])
            original = json.loads(conn.execute('SELECT original_json FROM receipt_attachment_audit').fetchone()[0])
        self.assertEqual(original, self.items[0])
        self.assertEqual(items[0]['reviewer_received_at'], 123)
        self.assertEqual(items[0]['file_id'], '')
        self.assertEqual(items[1], self.items[1])

    def test_stale_preview_cannot_remove(self):
        self.assertFalse(store.archive(1, 'buyer', 0, 'stale', 42))

    def test_amount_edit_is_atomic_and_resets_confirmation(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('ALTER TABLE offer_deal_gates ADD COLUMN buyer_toman_settled_at INTEGER')
            conn.execute("ALTER TABLE offer_deal_gates ADD COLUMN gate_status TEXT DEFAULT 'completed'")
        before = store.fingerprint(self.items[0])
        item = store.edit_buyer_amount(1, 0, before, 12345000, 42)
        self.assertEqual(item['amount_rial'], 12345000)
        self.assertEqual(item['recognized_amount_rial'], 12345000)
        self.assertEqual(item['reviewer_received_at'], 0)
        self.assertEqual(item['amount_edit_history'][0]['actor'], 42)
        self.assertIsNone(store.edit_buyer_amount(1, 0, before, 999, 43))

    def test_submitted_amount_cannot_be_silently_changed(self):
        item = dict(self.items[0], accounting_status='submitted', amount_rial=1000)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('ALTER TABLE offer_deal_gates ADD COLUMN buyer_toman_settled_at INTEGER')
            conn.execute("ALTER TABLE offer_deal_gates ADD COLUMN gate_status TEXT DEFAULT 'completed'")
            conn.execute('UPDATE offer_deal_gates SET buyer_receipt_log=?', (json.dumps([item]),))
        self.assertIsNone(store.edit_buyer_amount(1, 0, store.fingerprint(item), 999, 42))

    async def test_non_admin_cannot_access(self):
        q = SimpleNamespace(from_user=SimpleNamespace(id=99), answer=AsyncMock())
        with patch.object(flow, 'allowed', return_value=False), patch.object(flow, 'deal_gate_get') as lookup:
            await flow.callback(SimpleNamespace(callback_query=q), SimpleNamespace())
        lookup.assert_not_called()

    async def test_cancel_cleans_preview_and_does_not_duplicate_menu(self):
        bot = SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock(return_value=SimpleNamespace(message_id=30)))
        context = SimpleNamespace(bot=bot, user_data={flow._UI_KEY: {'mids': [20, 21], 'retired': []}})
        q = SimpleNamespace(data='receipts|menu|1', from_user=SimpleNamespace(id=42), message=SimpleNamespace(message_id=21, text='حذف فیش 1؟'), answer=AsyncMock())
        with patch.object(flow, 'allowed', return_value=True), patch.object(flow, 'deal_gate_get', return_value={'advert_rowid': 3679}):
            await flow.callback(SimpleNamespace(callback_query=q), context)
            await flow.callback(SimpleNamespace(callback_query=q), context)
        self.assertEqual(bot.send_message.await_count, 1)
        self.assertEqual({c.args[1] for c in bot.delete_message.await_args_list}, {20, 21})
        buttons = bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertTrue(any(b.callback_data == 'receipts|close|1' for row in buttons for b in row))

    async def test_close_removes_only_temporary_messages(self):
        bot = SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock())
        context = SimpleNamespace(bot=bot, user_data={flow._UI_KEY: {'mids': [30], 'retired': []}})
        q = SimpleNamespace(data='receipts|close|1', from_user=SimpleNamespace(id=42), message=SimpleNamespace(message_id=30, text='آگهی 3679 — مدیریت فیش‌ها'), answer=AsyncMock())
        with patch.object(flow, 'allowed', return_value=True), patch.object(flow, 'deal_gate_get', return_value={'advert_rowid': 3679}), patch.object(flow, 'archive') as remove:
            await flow.callback(SimpleNamespace(callback_query=q), context)
        bot.delete_message.assert_awaited_once_with(42, 30)
        bot.send_message.assert_not_awaited()
        remove.assert_not_called()

    def test_seller_amount_excludes_fee(self):
        with patch.object(deal_gate, 'get_advert_offer_joined', return_value={'rate_toman': 125000, 'proposed_euro_amount': 300}), patch.object(deal_gate, 'get_euro_advert_by_rowid', return_value={'operation': 'فروش'}), patch('handlers.offers._offer_effective_euro_amount', return_value=300), patch('handlers.offers.advert_fee_override_eur', return_value=None), patch('handlers.offers.fee_total_eur', return_value=5):
            self.assertEqual(deal_gate._seller_expected_rial({'offer_id': 537, 'advert_rowid': 3679}), 368750000)

    async def test_deleted_receipt_does_not_block_totals_or_accounting(self):
        receipts = [dict(amount_rial=n, accounting_status='submitted', reviewer_received_at=123) for n in (86000000, 86000000, 86000000, 8500000)]
        receipts.append(dict(type='removed', attachment_removed_at=123, accounting_status='review', amount_rial=0))
        with patch.object(deal_gate, '_buyer_expected_rial', return_value=266500000):
            self.assertEqual(deal_gate._buyer_toman_reviewer_totals({}, receipts), (266500000, 266500000, 0, 0))
        with patch.object(deal_gate, 'deal_gate_buyer_receipt_list', return_value=receipts), patch.object(deal_gate, 'deal_gate_claim_buyer_receipt_submission') as claim:
            ok, _ = await deal_gate._submit_confirmed_buyer_receipts_to_iran(SimpleNamespace(), gate={'offer_id': 553})
        self.assertTrue(ok)
        claim.assert_not_called()

    async def test_removal_reconciliation_advances_only_already_submitted_receipts(self):
        gate = dict(offer_id=553, advert_rowid=3685, buyer_telegram_id=1, seller_telegram_id=2, gate_status='completed')
        items = [dict(amount_rial=1000, accounting_status='submitted', reviewer_received_at=123), dict(attachment_removed_at=123)]
        context = SimpleNamespace(bot=AsyncMock())
        with patch.object(deal_gate, 'deal_gate_get', return_value=gate), patch.object(deal_gate, 'deal_gate_buyer_receipt_list', return_value=items), patch.object(deal_gate, '_buyer_expected_rial', return_value=1000), patch.object(deal_gate, '_send_buyer_eur_account_to_seller', new_callable=AsyncMock, return_value=True) as send, patch.object(deal_gate, 'deal_gate_upsert') as save, patch.object(deal_gate, '_log'), patch('utils.deal_milestones.notify_toman_settled_buyer', new_callable=AsyncMock):
            self.assertTrue(await deal_gate.reconcile_received_buyer_receipts(context, 553))
            self.assertTrue(save.call_args.kwargs['buyer_toman_settled_at'])
            send.reset_mock()
            items[0]['accounting_status'] = 'ready_for_review'
            self.assertFalse(await deal_gate.reconcile_received_buyer_receipts(context, 553))
            send.assert_not_awaited()
