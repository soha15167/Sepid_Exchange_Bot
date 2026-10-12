import asyncio
import gc
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telegram.error import BadRequest, TimedOut
from database import db, outgoing_receipts as store
from handlers import deal_outgoing as flow


class OutgoingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(db, 'DB_PATH', str(Path(self.temp.name) / 'test.db')),
            patch.object(flow, 'ADMIN_IDS', [900, 901]),
            patch.object(flow, 'DEAL_SUPPORT_ADMIN_IDS', [901]),
            patch.object(flow, 'DEAL_RECEIPT_REVIEWER_IDS', [111, 222]),
            patch('handlers.deal_gate.sync_deal_admin_notification', new_callable=AsyncMock),
        ]
        for item in self.patches:
            item.start()
        self.payload = dict(bank_name='ملت', dest_bank='ملی', depositor_name='گیرنده',
                            iran_amount=1230000, transfer_type='پایا', jdate='1405/06/17',
                            description='آگهی 3662', _receipt_kind='viewer_toman')
        self.did = store.create(12, 111, 'receipt-unique', self.payload)
        self.context = SimpleNamespace(bot=AsyncMock(), user_data={})
        self.context.bot.send_message.return_value = SimpleNamespace(message_id=20)
        self.context.bot.send_photo.return_value = SimpleNamespace(message_id=21)
        self.context.bot.send_document.return_value = SimpleNamespace(message_id=22)
        self.context.bot.get_file.return_value = SimpleNamespace(download_to_drive=AsyncMock())
        flow._preview_locks.clear()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        gc.collect()
        self.temp.cleanup()

    def update(self, action, uid=111, rev=None):
        data = f'outreceipt|{action}|{self.did}'
        if action == 'yes':
            data += '|' + (rev or flow.revision(store.get(self.did)))
        return SimpleNamespace(callback_query=SimpleNamespace(
            data=data, from_user=SimpleNamespace(id=uid), answer=AsyncMock(),
            message=SimpleNamespace(message_id=11, edit_text=AsyncMock())))

    def upload(self, uid=111, mid=900, *, photo=False, text='رسید'):
        return SimpleNamespace(effective_user=SimpleNamespace(id=uid), message=SimpleNamespace(
            text=None if photo else text, caption=text if photo else None,
            photo=[SimpleNamespace(file_id='receipt', file_unique_id='same-file')] if photo else [],
            document=None, message_id=mid, reply_text=AsyncMock()))

    async def test_staff_uploads_auto_submit_without_approval(self):
        with patch.object(flow, 'deal_gate_get', return_value={'advert_rowid': 3662}), \
             patch('handlers.iran_panel_sync._parse_payload_out', side_effect=lambda _: (dict(self.payload), '')), \
             patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post:
            await flow.prepare(self.upload(uid=111), self.context, 12, 'viewer_toman')
            await flow.prepare(self.upload(uid=900, mid=901), self.context, 12, 'seller_toman')
        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            body = call.kwargs['payload']
            self.assertEqual(body['iran_type'], 'خروجی')
            self.assertEqual(body['description'], 'آگهی 3662')
            self.assertEqual(body['destination_bank'], 'ملی')
            self.assertNotIn('tax', body)
        for call in self.context.bot.send_message.await_args_list:
            self.assertIsNone(call.kwargs['reply_markup'])
            self.assertNotIn('ثبت این فیش', call.args[1])

    async def test_legacy_yes_submits_once_under_concurrent_callbacks(self):
        with patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post:
            await asyncio.gather(flow.callback(self.update('yes'), self.context),
                                 flow.callback(self.update('yes'), self.context))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(store.get(self.did)['status'], 'submitted')
        self.context.bot.send_message.assert_not_awaited()
        self.assertEqual(self.context.bot.edit_message_text.await_count, 2)

    async def test_callback_acknowledgement_timeout_does_not_block_legacy_submission(self):
        update = self.update('yes')
        update.callback_query.answer.side_effect = TimedOut()
        with patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post:
            await flow.callback(update, self.context)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(store.get(self.did)['status'], 'submitted')

    async def test_legacy_no_never_posts(self):
        with patch('utils.iran_panel_client.post_transaction') as post:
            await flow.callback(self.update('no'), self.context)
            await flow.auto_submit(111, self.did)
        post.assert_not_called()
        self.assertEqual(store.get(self.did)['status'], 'skipped')

    async def test_status_edits_existing_receipt_without_resending_photo(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', receipt_mid=10, prompt_mid=11)
        with patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post:
            await flow.callback(self.update('yes'), self.context)
            await flow.callback(self.update('yes'), self.context)
        self.assertEqual(post.call_count, 1)
        self.context.bot.send_photo.assert_not_awaited()
        self.context.bot.send_message.assert_not_awaited()
        self.assertEqual(self.context.bot.edit_message_caption.await_count, 2)
        self.context.bot.delete_message.assert_awaited_once_with(chat_id=111, message_id=11)
        self.assertEqual(store.receipt_display(self.did, 111)['receipt_mid'], 10)

    async def test_initial_receipt_card_then_repeated_refresh_edits_same_card(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', source_mid=10)
        await flow.preview(self.context.bot, 111, self.did)
        await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.send_photo.assert_awaited_once()
        self.context.bot.send_message.assert_not_awaited()
        self.context.bot.delete_message.assert_awaited_once_with(chat_id=111, message_id=10)
        self.assertEqual(store.receipt_display(self.did, 111)['receipt_mid'], 21)
        markup = self.context.bot.send_photo.call_args.kwargs['reply_markup']
        self.assertEqual(len(markup.inline_keyboard), 1)
        self.assertIn('|edit|', markup.inline_keyboard[0][0].callback_data)

    async def test_failed_initial_receipt_copy_preserves_original(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', source_mid=10)
        self.context.bot.send_photo.side_effect = TimedOut()
        with self.assertRaises(TimedOut):
            await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.delete_message.assert_not_awaited()
        self.context.bot.send_message.assert_not_awaited()
        self.assertEqual(store.receipt_display(self.did, 111)['source_mid'], 10)

    async def test_uncertain_initial_delivery_never_sends_another_card_on_retry(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', source_mid=10)
        self.context.bot.send_photo.side_effect = TimedOut()
        with self.assertRaises(TimedOut):
            await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.send_photo.side_effect = None
        await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.send_photo.assert_awaited_once()
        self.context.bot.delete_message.assert_not_awaited()
        self.assertEqual(store.receipt_display(self.did, 111)['delivery_status'], 'unknown')

    async def test_uncertain_replacement_delivery_never_resends_on_retry(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', receipt_mid=10)
        self.context.bot.edit_message_caption.side_effect = BadRequest('Message to edit not found')
        self.context.bot.send_photo.side_effect = TimedOut()
        with self.assertRaises(TimedOut):
            await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.send_photo.side_effect = None
        await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.send_photo.assert_awaited_once()
        self.context.bot.edit_message_caption.assert_awaited_once()
        self.context.bot.delete_message.assert_not_awaited()

    async def test_transient_edit_error_keeps_existing_card_without_copy(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', receipt_mid=10, prompt_mid=11)
        self.context.bot.edit_message_caption.side_effect = TimedOut()
        await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.send_photo.assert_not_awaited()
        self.context.bot.send_message.assert_not_awaited()
        self.context.bot.delete_message.assert_not_awaited()
        self.assertEqual(store.receipt_display(self.did, 111)['receipt_mid'], 10)

    async def test_nonmissing_edit_error_does_not_replace_card(self):
        store.receipt_display(self.did, 111, prompt_mid=11)
        self.context.bot.edit_message_text.side_effect = BadRequest('Message can\'t be edited')
        await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.send_message.assert_not_awaited()

    async def test_definitively_deleted_media_card_is_replaced_then_old_status_removed(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', receipt_mid=10, prompt_mid=11)
        self.context.bot.edit_message_caption.side_effect = BadRequest('Message to edit not found')
        await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.send_photo.assert_awaited_once()
        self.assertEqual({c.kwargs['message_id'] for c in self.context.bot.delete_message.await_args_list}, {10, 11})
        self.assertEqual(store.receipt_display(self.did, 111)['receipt_mid'], 21)

    async def test_unchanged_caption_is_success_and_old_status_is_cleaned_up(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', receipt_mid=10, prompt_mid=11)
        self.context.bot.edit_message_caption.side_effect = BadRequest('Message is not modified')
        await flow.preview(self.context.bot, 111, self.did)
        self.context.bot.send_photo.assert_not_awaited()
        self.context.bot.delete_message.assert_awaited_once_with(chat_id=111, message_id=11)

    async def test_failed_old_card_deletion_is_retained_for_retry(self):
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', receipt_mid=10, prompt_mid=11)
        self.context.bot.delete_message.side_effect = TimedOut()
        await flow.preview(self.context.bot, 111, self.did)
        self.assertEqual(store.receipt_display(self.did, 111)['cleanup_mids'], [11])
        self.context.bot.delete_message.side_effect = None
        await flow.preview(self.context.bot, 111, self.did)
        self.assertEqual(store.receipt_display(self.did, 111)['cleanup_mids'], [])
        self.context.bot.send_photo.assert_not_awaited()

    async def test_preview_formats_rial_and_toman_without_changing_payload(self):
        store.transition(self.did, 'draft', actor=111, payload=dict(self.payload, iran_amount=499675000))
        await flow.preview(self.context.bot, 111, self.did)
        body = self.context.bot.send_message.call_args.args[1]
        self.assertIn('💰 مبلغ: <b>۴۹۹٬۶۷۵٬۰۰۰</b> ریال (<b>۴۹٬۹۶۷٬۵۰۰</b> تومان)', body)
        self.assertEqual(json.loads(store.get(self.did)['payload'])['iran_amount'], 499675000)

    async def test_unknown_result_never_retries(self):
        with patch('utils.iran_panel_client.post_transaction', return_value=(False, 'timeout')) as post:
            await flow.auto_submit(111, self.did)
            await flow.auto_submit(111, self.did)
            await flow.callback(self.update('yes'), self.context)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(store.get(self.did)['status'], 'unknown')

    async def test_exception_result_never_retries(self):
        with patch('utils.iran_panel_client.post_transaction', side_effect=RuntimeError('timeout')) as post:
            await flow.auto_submit(111, self.did)
            await flow.auto_submit(111, self.did)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(store.get(self.did)['status'], 'unknown')

    async def test_submitted_result_survives_new_context(self):
        with patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post:
            await flow.auto_submit(111, self.did)
            await flow.callback(self.update('yes'), SimpleNamespace(bot=self.context.bot, user_data={}))
        self.assertEqual(post.call_count, 1)

    async def test_duplicate_file_upload_posts_once_and_keeps_single_card(self):
        with patch.object(flow, 'deal_gate_get', return_value={'advert_rowid': 3662}), \
             patch('handlers.iran_panel_sync._read_receipt_image', new=AsyncMock(side_effect=lambda *a: (dict(self.payload), 'انتقال وجه', 'text'))), \
             patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post:
            await flow.prepare(self.upload(photo=True, mid=900), self.context, 12, 'viewer_toman')
            await flow.prepare(self.upload(photo=True, mid=901), self.context, 12, 'viewer_toman')
        self.assertEqual(post.call_count, 1)
        self.context.bot.send_photo.assert_awaited_once()
        self.context.bot.edit_message_caption.assert_awaited_once()
        self.assertEqual({c.kwargs['message_id'] for c in self.context.bot.delete_message.await_args_list}, {900, 901})

    async def test_duplicate_unknown_file_upload_does_not_post_again(self):
        with patch.object(flow, 'deal_gate_get', return_value={'advert_rowid': 3662}), \
             patch('handlers.iran_panel_sync._read_receipt_image', new=AsyncMock(side_effect=lambda *a: (dict(self.payload), 'انتقال وجه', 'text'))), \
             patch('utils.iran_panel_client.post_transaction', return_value=(False, 'timeout')) as post:
            await flow.prepare(self.upload(photo=True), self.context, 12, 'viewer_toman')
            await flow.prepare(self.upload(photo=True, mid=901), self.context, 12, 'viewer_toman')
        self.assertEqual(post.call_count, 1)
        self.context.bot.send_photo.assert_awaited_once()

    async def test_concurrent_duplicate_file_upload_keeps_one_card_and_one_transaction(self):
        with patch.object(flow, 'deal_gate_get', return_value={'advert_rowid': 3662}), \
             patch('handlers.iran_panel_sync._read_receipt_image', new=AsyncMock(side_effect=lambda *a: (dict(self.payload), 'انتقال وجه', 'text'))), \
             patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post:
            await asyncio.gather(
                flow.prepare(self.upload(photo=True, mid=900), self.context, 12, 'viewer_toman'),
                flow.prepare(self.upload(photo=True, mid=901), self.context, 12, 'viewer_toman'),
            )
        self.assertEqual(post.call_count, 1)
        self.context.bot.send_photo.assert_awaited_once()
        self.assertEqual({c.kwargs['message_id'] for c in self.context.bot.delete_message.await_args_list}, {900, 901})

    async def test_another_viewer_cannot_confirm(self):
        with patch('utils.iran_panel_client.post_transaction') as post:
            await flow.callback(self.update('yes', uid=222), self.context)
            self.assertEqual(await flow.auto_submit(222, self.did), 'unauthorized')
        post.assert_not_called()
        self.assertEqual(store.get(self.did)['status'], 'draft')

    async def test_full_admin_can_process_reviewers_draft(self):
        with patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post:
            await flow.auto_submit(900, self.did)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(store.get(self.did)['confirmed_by'], 900)

    async def test_support_admin_and_ordinary_user_cannot_upload_or_confirm(self):
        with patch('utils.iran_panel_client.post_transaction') as post, patch.object(store, 'create') as create:
            for uid in (901, 444):
                await flow.prepare(self.upload(uid=uid), self.context, 12, 'seller_toman')
                await flow.callback(self.update('yes', uid=uid), self.context)
        post.assert_not_called()
        create.assert_not_called()

    async def test_stale_legacy_button_does_not_submit_changed_payload(self):
        old_revision = flow.revision(store.get(self.did))
        store.transition(self.did, 'draft', actor=111, payload=dict(self.payload, iran_amount=99999))
        with patch('utils.iran_panel_client.post_transaction') as post:
            await flow.callback(self.update('yes', rev=old_revision), self.context)
            self.assertEqual(await flow.auto_submit(111, self.did, expected_revision=old_revision), 'stale')
        post.assert_not_called()

    async def test_incomplete_upload_only_offers_edit(self):
        incomplete = dict(self.payload, dest_bank='', jdate='')
        with patch.object(flow, 'deal_gate_get', return_value={'advert_rowid': 3662}), \
             patch('handlers.iran_panel_sync._parse_payload_out', return_value=(incomplete, '')), \
             patch('utils.iran_panel_client.post_transaction') as post:
            await flow.prepare(self.upload(), self.context, 12, 'viewer_toman')
        post.assert_not_called()
        markup = self.context.bot.send_message.call_args.kwargs['reply_markup']
        self.assertEqual(len(markup.inline_keyboard), 1)
        self.assertIn('|edit|', markup.inline_keyboard[0][0].callback_data)
        self.assertNotIn('تأیید', self.context.bot.send_message.call_args.args[1])

    async def test_complete_correction_automatically_submits_and_edits_same_card(self):
        store.transition(self.did, 'draft', actor=111, payload=dict(self.payload, jdate=''))
        store.receipt_display(self.did, 111, file_id='receipt', type='photo', receipt_mid=10)
        self.context.user_data[flow.EDIT_KEY] = self.did
        self.context.user_data['deal_outgoing_edit_prompt_mid'] = 30
        with patch('handlers.iran_panel_sync._parse_payload_out', return_value=(dict(self.payload), '')), \
             patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post:
            self.assertTrue(await flow.edit_message(self.upload(text='مبلغ: 1230000'), self.context))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(store.get(self.did)['status'], 'submitted')
        self.context.bot.send_photo.assert_not_awaited()
        self.context.bot.send_message.assert_not_awaited()
        self.context.bot.edit_message_caption.assert_awaited_once()
        self.context.bot.delete_message.assert_awaited_once_with(chat_id=111, message_id=30)

    async def test_incomplete_correction_never_submits(self):
        self.context.user_data[flow.EDIT_KEY] = self.did
        with patch('handlers.iran_panel_sync._parse_payload_out', return_value=(dict(self.payload, bank_name=''), '')), \
             patch('utils.iran_panel_client.post_transaction') as post:
            await flow.edit_message(self.upload(text='مبلغ: 1230000'), self.context)
        post.assert_not_called()
        self.assertEqual(store.get(self.did)['status'], 'draft')

    async def test_repeat_edit_button_reuses_instruction_message(self):
        await flow.callback(self.update('edit'), self.context)
        await flow.callback(self.update('edit'), self.context)
        self.context.bot.send_message.assert_awaited_once()
        self.context.bot.edit_message_text.assert_awaited_once()

    async def test_foreign_currency_and_failed_or_incoming_transfer_are_not_booked(self):
        cases = [
            ('مبلغ: 1230000 EUR', {}),
            ('انتقال وجه ناموفق', {}),
            ('دریافت وجه', {}),
            ('انتقال وجه', {'_receipt_status': 'failed'}),
            ('انتقال وجه', {'_detected_direction': 'in'}),
            ('انتقال وجه', {'_amount_disagreement': {'gemini': 1230000, 'google_vision': 999999}}),
        ]
        with patch.object(flow, 'deal_gate_get', return_value={'advert_rowid': 3662}), \
             patch('utils.iran_panel_client.post_transaction') as post:
            for mid, (raw, extra) in enumerate(cases, start=900):
                with patch('handlers.iran_panel_sync._parse_payload_out', return_value=(dict(self.payload, **extra), '')):
                    await flow.prepare(self.upload(mid=mid, text=raw), self.context, 12, 'seller_toman')
        post.assert_not_called()

    async def test_legacy_recognized_invalid_receipts_are_not_booked(self):
        for extra in ({'_receipt_status': 'failed'}, {'_detected_direction': 'in'}, {'_receipt_currency': 'eur'},
                      {'_amount_disagreement': {'gemini': 1230000, 'google_vision': 999999}},
                      {'_invalid_receipt': True}):
            store.transition(self.did, 'draft', actor=111, payload=dict(self.payload, **extra))
            with patch('utils.iran_panel_client.post_transaction') as post:
                await flow.auto_submit(111, self.did)
            post.assert_not_called()
            self.assertEqual(store.get(self.did)['status'], 'draft')

    async def test_complete_manual_correction_clears_invalid_recognition(self):
        store.transition(self.did, 'draft', actor=111, payload=dict(self.payload, _invalid_receipt=True,
                                                                  _amount_inconsistent=True))
        self.context.user_data[flow.EDIT_KEY] = self.did
        with patch('handlers.iran_panel_sync._parse_payload_out', return_value=(dict(self.payload), '')), \
             patch('utils.iran_panel_client.post_transaction', return_value=(True, 'ok')) as post:
            await flow.edit_message(self.upload(text='مبلغ: 1230000'), self.context)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(store.get(self.did)['status'], 'submitted')

    async def test_euro_receipt_never_prepares_accounting(self):
        with patch.object(flow, 'preview', new_callable=AsyncMock) as preview, patch.object(store, 'create') as create:
            await flow.prepare(SimpleNamespace(), self.context, 12, 'seller_euro')
        preview.assert_not_awaited()
        create.assert_not_called()

    async def test_old_euro_confirmation_cannot_submit(self):
        store.transition(self.did, 'draft', actor=111, payload=dict(self.payload, _receipt_kind='seller_euro'))
        with patch('utils.iran_panel_client.post_transaction') as post:
            await flow.callback(self.update('yes'), self.context)
        post.assert_not_called()
        self.assertEqual(store.get(self.did)['status'], 'skipped')

    async def test_preparation_error_never_requests_resending(self):
        with patch.object(flow, '_prepare', new=AsyncMock(side_effect=RuntimeError('display unavailable'))):
            await flow.prepare(self.upload(), self.context, 12, 'viewer_toman')
        body = self.context.bot.send_message.call_args.args[1]
        self.assertNotIn('دوباره', body)
        self.assertNotIn('ارسال کنید', body)

    async def test_atomic_claim_across_connections(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            winners = list(executor.map(lambda uid: store.transition(self.did, 'submitting', actor=uid), range(8)))
        self.assertEqual(sum(winners), 1)


if __name__ == '__main__':
    unittest.main()
