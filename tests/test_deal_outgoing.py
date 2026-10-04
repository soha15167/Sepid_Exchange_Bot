import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from database import db, outgoing_receipts as store
from handlers import deal_outgoing as flow


class OutgoingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(db, 'DB_PATH', str(Path(self.temp.name) / 'test.db'))
        self.dbpatch.start()
        self.payload = dict(bank_name='ملت', dest_bank='ملی', depositor_name='گیرنده',
                            iran_amount=1230000, transfer_type='پایا', jdate='1405/06/17', description='آگهی 3662')
        self.did = store.create(12, 111, 'receipt-unique', self.payload)
        self.context = SimpleNamespace(bot=AsyncMock(), user_data={})

    def tearDown(self):
        self.dbpatch.stop()
        self.temp.cleanup()

    def update(self, action, uid=111, rev=None):
        data = f'outreceipt|{action}|{self.did}'
        if action == 'yes':
            data += '|' + (rev or flow.revision(store.get(self.did)))
        return SimpleNamespace(callback_query=SimpleNamespace(
            data=data, from_user=SimpleNamespace(id=uid), answer=AsyncMock(), message=AsyncMock()))

    async def test_yes_submits_outgoing_exact_description_once(self):
        with patch.object(flow, 'allowed', return_value=True), patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post, patch('handlers.deal_gate.sync_deal_admin_notification', new_callable=AsyncMock):
            await asyncio.gather(flow.callback(self.update('yes'), self.context), flow.callback(self.update('yes'), self.context))
        self.assertEqual(post.call_count, 1)
        body = post.call_args.kwargs['payload']
        self.assertEqual(body['iran_type'], 'خروجی')
        self.assertEqual(body['description'], 'آگهی 3662')
        self.assertEqual(body['destination_bank'], 'ملی')
        self.assertNotIn('tax', body)
        self.assertEqual(store.get(self.did)['status'], 'submitted')

    async def test_no_never_posts(self):
        with patch.object(flow, 'allowed', return_value=True), patch('utils.iran_panel_client.post_transaction') as post, patch('handlers.deal_gate.sync_deal_admin_notification', new_callable=AsyncMock):
            await flow.callback(self.update('no'), self.context)
        post.assert_not_called()
        self.assertEqual(store.get(self.did)['status'], 'skipped')

    async def test_result_is_below_reposted_receipt_and_old_pair_removed(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', receipt_mid=10, prompt_mid=11)
        self.context.bot.send_photo.return_value = SimpleNamespace(message_id=20)
        self.context.bot.send_message.return_value = SimpleNamespace(message_id=21)
        update = self.update('no')
        update.callback_query.message.message_id = 11
        with patch.object(flow, 'allowed', return_value=True), patch('handlers.deal_gate.sync_deal_admin_notification', new_callable=AsyncMock):
            await flow.callback(update, self.context)
        self.assertEqual(self.context.bot.send_message.call_args.kwargs['reply_to_message_id'], 20)
        self.assertEqual({c.args[1] for c in self.context.bot.delete_message.await_args_list}, {10, 11})

    async def test_receipt_copy_below_preview_then_old_copy_deleted(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', source_mid=10)
        self.context.bot.send_message.return_value = SimpleNamespace(message_id=20)
        self.context.bot.send_photo.return_value = SimpleNamespace(message_id=21)
        await flow.preview(self.context.bot, 111, self.did)
        self.assertEqual(self.context.bot.send_photo.call_args.kwargs['reply_to_message_id'], 20)
        self.context.bot.delete_message.assert_awaited_once_with(111, 10)
        self.assertEqual(store.receipt_display(self.did, 111)['receipt_mid'], 21)

    async def test_failed_receipt_copy_preserves_original(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', source_mid=10)
        self.context.bot.send_message.return_value = SimpleNamespace(message_id=20)
        self.context.bot.send_photo.side_effect = RuntimeError('network unavailable')
        with self.assertRaises(RuntimeError):
            await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.delete_message.assert_not_awaited()
        self.assertEqual(store.receipt_display(self.did, 111)['source_mid'], 10)

    async def test_preview_formats_rial_and_toman_without_changing_payload(self):
        store.transition(self.did, 'draft', actor=111, payload=dict(self.payload, iran_amount=499675000))
        await flow.preview(self.context.bot, 111, self.did)
        body = self.context.bot.send_message.call_args.args[1]
        self.assertIn('💰 مبلغ: <b>۴۹۹٬۶۷۵٬۰۰۰</b> ریال (<b>۴۹٬۹۶۷٬۵۰۰</b> تومان)', body)
        self.assertEqual(json.loads(store.get(self.did)['payload'])['iran_amount'], 499675000)

    async def test_unknown_outcome_cannot_retry_and_duplicate_upload_reuses_record(self):
        with patch.object(flow, 'allowed', return_value=True), patch('utils.iran_panel_client.post_transaction', return_value=(False, 'timeout')) as post, patch('handlers.deal_gate.sync_deal_admin_notification', new_callable=AsyncMock):
            await flow.callback(self.update('yes'), self.context)
            await flow.callback(self.update('yes'), self.context)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(store.get(self.did)['status'], 'unknown')
        self.assertEqual(store.create(12, 222, 'receipt-unique', self.payload), self.did)

    async def test_another_viewer_cannot_confirm(self):
        with patch.object(flow, 'allowed', return_value=True), patch.object(flow, 'ADMIN_IDS', []), patch('utils.iran_panel_client.post_transaction') as post:
            await flow.callback(self.update('yes', uid=222), self.context)
        post.assert_not_called()
        self.assertEqual(store.get(self.did)['status'], 'draft')

    async def test_stale_preview_and_foreign_currency_block_submission(self):
        old_revision = flow.revision(store.get(self.did))
        store.transition(self.did, 'draft', actor=111, payload=dict(self.payload, iran_amount=99999))
        with patch.object(flow, 'allowed', return_value=True), patch('utils.iran_panel_client.post_transaction') as post:
            await flow.callback(self.update('yes', rev=old_revision), self.context)
            store.transition(self.did, 'draft', actor=111, payload=dict(self.payload, _foreign_currency=True))
            await flow.callback(self.update('yes'), self.context)
        post.assert_not_called()

    async def test_atomic_claim_across_connections(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            winners = list(executor.map(lambda uid: store.transition(self.did, 'submitting', actor=uid), range(8)))
        self.assertEqual(sum(winners), 1)

    async def test_receipt_upload_builds_preview_without_posting(self):
        update = SimpleNamespace(effective_user=SimpleNamespace(id=111), message=SimpleNamespace(
            text='رسید', caption=None, photo=[], document=None, message_id=900))
        with patch.object(flow, 'allowed', return_value=True), patch.object(flow, 'deal_gate_get', return_value={'advert_rowid': 3662}), patch('handlers.iran_panel_sync._parse_payload_out', return_value=(dict(self.payload, description='wrong'), '')), patch('handlers.deal_gate.sync_deal_admin_notification', new_callable=AsyncMock), patch('utils.iran_panel_client.post_transaction') as post:
            await flow.prepare(update, self.context, 12, 'seller_toman')
        post.assert_not_called()
        sent = self.context.bot.send_message.call_args
        self.assertIn('آگهی 3662', sent.args[1])

    async def test_euro_receipt_never_prepares_accounting(self):
        with patch.object(flow, 'preview', new_callable=AsyncMock) as preview, patch.object(store, 'create') as create:
            await flow.prepare(SimpleNamespace(), self.context, 12, 'seller_euro')
        preview.assert_not_awaited()
        create.assert_not_called()

    async def test_old_euro_confirmation_cannot_submit(self):
        store.transition(self.did, 'draft', actor=111, payload=dict(self.payload, _receipt_kind='seller_euro'))
        with patch.object(flow, 'allowed', return_value=True), patch('utils.iran_panel_client.post_transaction') as post:
            await flow.callback(self.update('yes'), self.context)
        post.assert_not_called()
        self.assertEqual(store.get(self.did)['status'], 'skipped')


if __name__ == '__main__':
    unittest.main()
