import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from handlers import deal_gate as flow


class ViewerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_removes_clicked_and_tracked_controls_when_session_is_stopped(self):
        context = SimpleNamespace(bot=AsyncMock(), user_data={'viewer_receipt_controls': {'537': 20}, 'viewer_receipt_acknowledgments': {'537': [22]}})
        q = SimpleNamespace(from_user=SimpleNamespace(id=111), message=SimpleNamespace(message_id=21), answer=AsyncMock())
        with patch.object(flow, '_viewer_toman_ids', return_value={111}):
            await flow._handle_viewer_toman_callback(SimpleNamespace(callback_query=q), context, 'cancel', 537)
        self.assertEqual({c.kwargs['message_id'] for c in context.bot.delete_message.await_args_list}, {20, 21, 22})
        self.assertEqual(context.user_data['viewer_receipt_controls'], {})

    async def test_cancel_does_not_stop_another_deal(self):
        q = SimpleNamespace(from_user=SimpleNamespace(id=111), answer=AsyncMock())
        context = SimpleNamespace(user_data={flow._VIEWER_TOMAN_RECEIPT_KEY: {'offer_id': 537}})
        with patch.object(flow, '_viewer_toman_ids', return_value={111}):
            await flow._handle_viewer_toman_callback(SimpleNamespace(callback_query=q), context, 'cancel', 525)
            self.assertEqual(context.user_data[flow._VIEWER_TOMAN_RECEIPT_KEY]['offer_id'], 537)
            await flow._handle_viewer_toman_callback(SimpleNamespace(callback_query=q), context, 'cancel', 537)
            self.assertNotIn(flow._VIEWER_TOMAN_RECEIPT_KEY, context.user_data)

    async def test_each_album_receipt_is_queued_for_correct_seller(self):
        context = SimpleNamespace(user_data={flow._VIEWER_TOMAN_RECEIPT_KEY: {'offer_id': 537}}, bot=AsyncMock())
        gate = {'offer_id': 537, 'advert_rowid': 3679, 'seller_telegram_id': 222}
        with patch.object(flow, 'deal_gate_append_viewer_toman_receipt', return_value=[{}]), patch.object(flow, 'deal_gate_get', return_value=gate), patch.object(flow, '_update_viewer_toman_prompts', new_callable=AsyncMock), patch.object(flow, 'sync_deal_admin_notification', new_callable=AsyncMock), patch.object(flow, 'prepare_outgoing_receipt', new_callable=AsyncMock), patch.object(flow, '_enqueue_and_deliver_deal_message', new_callable=AsyncMock) as deliver:
            for mid in (10, 11, 12):
                message = SimpleNamespace(text='', caption='', photo=[SimpleNamespace(file_id=f'photo{mid}')], document=None, message_id=mid, reply_text=AsyncMock())
                await flow._viewer_toman_receipt_try(SimpleNamespace(message=message, effective_user=SimpleNamespace(id=111)), context)
        self.assertEqual(deliver.await_count, 3)
        self.assertEqual(len({c.kwargs['dedupe_key'] for c in deliver.await_args_list}), 3)
        for call in deliver.await_args_list:
            self.assertEqual(call.kwargs['chat_id'], 222)
            self.assertIn('3679', call.kwargs['payload']['body_html'])
            self.assertEqual(call.kwargs['payload']['after_hook'], 'record_seller_toman_receipt')
            self.assertEqual(call.kwargs['payload']['keyboard'], 'seller_toman_settled')

    async def test_euro_confirmation_schedules_without_account_delivery_flag(self):
        gate = {'offer_id': 525, 'advert_rowid': 3677, 'buyer_telegram_id': 1, 'seller_telegram_id': 2}
        context = SimpleNamespace(bot=AsyncMock())
        with patch.object(flow, 'deal_gate_get', return_value=gate), patch.object(flow, 'deal_gate_seller_receipt_list', return_value=[{'buyer_confirmed_at': 123}]), patch.object(flow, 'deal_gate_confirm_seller_receipt_buyer', return_value=True), patch('database.db.deal_gate_seller_receipt_list', return_value=[{'buyer_confirmed_at': 123}]), patch.object(flow, 'deal_gate_upsert') as save, patch.object(flow.time, 'time', return_value=1000), patch.object(flow, '_log', side_effect=RuntimeError('stop after schedule')):
            with self.assertRaisesRegex(RuntimeError, 'stop after schedule'):
                await flow._apply_euro_settled(context, offer_id=525, receipt_index=0, confirmed_by='buyer')
        self.assertEqual(save.call_args.kwargs['viewer_toman_due_at'], 4600)

    def test_viewer_album_photos_and_documents_are_in_admin_plan(self):
        gate = {'viewer_toman_receipt_log': json.dumps([
            {'type': 'photo', 'file_id': 'photo1'},
            {'type': 'photo', 'file_id': 'photo2'},
            {'type': 'document', 'file_id': 'pdf1'}])}
        with patch.object(flow, 'deal_gate_buyer_receipt_list', return_value=[]), patch.object(flow, 'deal_gate_seller_receipt_list', return_value=[]), patch.object(flow, 'deal_gate_seller_toman_admin_list', return_value=[]):
            slides = flow._admin_receipt_slides_plan(gate, 525, seq=1, aid=3677)
        self.assertEqual([s[0] for s in slides], ['photo1', 'photo2', 'pdf1'])
        self.assertEqual(slides[-1][2], 'document')
        self.assertTrue(all('3677' in s[1] for s in slides))

    async def test_admin_sync_failure_does_not_block_outgoing_preview(self):
        message = SimpleNamespace(text='', caption='', photo=[SimpleNamespace(file_id='photo1')], document=None, message_id=10, reply_text=AsyncMock())
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=111))
        context = SimpleNamespace(user_data={flow._VIEWER_TOMAN_RECEIPT_KEY: {'offer_id': 525}}, bot=AsyncMock())
        with patch.object(flow, 'deal_gate_append_viewer_toman_receipt', return_value=[{}]), patch.object(flow, 'deal_gate_get', return_value={'offer_id': 525}), patch.object(flow, '_update_viewer_toman_prompts', new_callable=AsyncMock), patch.object(flow, 'sync_deal_admin_notification', side_effect=RuntimeError('unavailable')), patch.object(flow, 'prepare_outgoing_receipt', new_callable=AsyncMock) as preview:
            self.assertTrue(await flow._viewer_toman_receipt_try(update, context))
        preview.assert_awaited_once_with(update, context, 525, 'viewer_toman')

    async def test_claim_activates_receipt_upload(self):
        q = SimpleNamespace(from_user=SimpleNamespace(id=111), answer=AsyncMock())
        context = SimpleNamespace(user_data={}, bot=AsyncMock())
        with patch.object(flow, '_viewer_toman_ids', return_value={111}), patch.object(flow, 'deal_gate_get', return_value={'offer_id': 525}), patch.object(flow, 'deal_gate_claim_viewer_toman_payment', return_value=True), patch.object(flow, '_log'), patch.object(flow, '_update_viewer_toman_prompts', new_callable=AsyncMock), patch.object(flow, 'sync_deal_admin_notification', new_callable=AsyncMock):
            await flow._handle_viewer_toman_callback(SimpleNamespace(callback_query=q), context, 'claim', 525)
        self.assertEqual(context.user_data[flow._VIEWER_TOMAN_RECEIPT_KEY], {'offer_id': 525})
