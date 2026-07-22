"""Failure injection and idempotency tests for the deal delivery workflow."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


def _delivery(**changes) -> dict:
    row = {
        "id": 7,
        "offer_id": 264,
        "recipient_telegram_id": 89369067,
        "party": "seller",
        "tag": "فیش تومان از ادمین",
        "payload_type": "photo",
        "payload_json": json.dumps(
            {
                "entry_type": "photo",
                "receipt_text": "",
                "file_id": "receipt-file",
                "body_html": "receipt",
                "keyboard": "seller_toman_settled",
                "after_hook": "record_seller_toman_receipt",
            }
        ),
        "dedupe_key": "seller_toman:264:7001:9001",
        "status": "pending",
    }
    row.update(changes)
    return row


class DurableDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_receipt_send_is_queued_without_recording_payment(self):
        from handlers import deal_gate

        bot = SimpleNamespace(send_photo=AsyncMock(side_effect=RuntimeError("offline")))
        with (
            patch.object(deal_gate, "deal_delivery_claim", return_value=True),
            patch.object(deal_gate, "deal_delivery_mark_failed") as failed,
            patch.object(deal_gate, "deal_gate_record_seller_toman_delivery") as record,
        ):
            delivered = await deal_gate._deliver_deal_queue_item(bot, _delivery())

        self.assertFalse(delivered)
        failed.assert_called_once()
        record.assert_not_called()

    async def test_successful_receipt_delivery_and_queue_completion_are_atomic(self):
        from handlers import deal_gate

        bot = SimpleNamespace(
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=9010))
        )
        with (
            patch.object(deal_gate, "deal_delivery_claim", return_value=True),
            patch.object(
                deal_gate,
                "deal_gate_record_seller_toman_delivery",
                return_value=True,
            ) as record,
            patch.object(deal_gate, "bot_outbound_log_insert"),
        ):
            delivered = await deal_gate._deliver_deal_queue_item(bot, _delivery())

        self.assertTrue(delivered)
        record.assert_called_once_with(
            264,
            entry_type="photo",
            text="",
            file_id="receipt-file",
            delivery_key="seller_toman:264:7001:9001",
            queue_delivery_id=7,
            telegram_message_id=9010,
        )

    async def test_sent_or_concurrently_claimed_item_is_not_sent_twice(self):
        from handlers import deal_gate

        bot = SimpleNamespace(send_photo=AsyncMock())
        self.assertTrue(
            await deal_gate._deliver_deal_queue_item(
                bot,
                _delivery(status="sent"),
            )
        )
        bot.send_photo.assert_not_awaited()

        with patch.object(deal_gate, "deal_delivery_claim", return_value=False):
            self.assertFalse(
                await deal_gate._deliver_deal_queue_item(bot, _delivery())
            )
        bot.send_photo.assert_not_awaited()

    async def test_telegram_retry_after_is_deferred_without_failure_count(self):
        from telegram.error import RetryAfter
        from handlers import deal_gate

        bot = SimpleNamespace(send_photo=AsyncMock(side_effect=RetryAfter(17)))
        with (
            patch.object(deal_gate, "deal_delivery_claim", return_value=True),
            patch.object(deal_gate, "deal_delivery_defer_rate_limit") as defer,
            patch.object(deal_gate, "deal_delivery_mark_failed") as failed,
        ):
            delivered = await deal_gate._deliver_deal_queue_item(bot, _delivery())

        self.assertFalse(delivered)
        self.assertEqual(defer.call_args.args[:2], (7, 17))
        failed.assert_not_called()


class StaleCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_party_button_is_expired_without_state_change(self):
        from handlers import deal_gate

        message = SimpleNamespace(edit_reply_markup=AsyncMock())
        query = SimpleNamespace(
            data="deal|yes|264",
            from_user=SimpleNamespace(id=111),
            message=message,
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace()
        with (
            patch.object(
                deal_gate,
                "deal_gate_get",
                return_value={
                    "offer_id": 264,
                    "gate_status": "closed",
                    "buyer_telegram_id": 111,
                    "seller_telegram_id": 222,
                },
            ),
            patch.object(deal_gate, "deal_gate_upsert") as upsert,
        ):
            await deal_gate.deal_gate_callback(update, context)

        query.answer.assert_awaited_once()
        message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        upsert.assert_not_called()

    async def test_malformed_callback_is_rejected_without_exception(self):
        from handlers import deal_gate

        message = SimpleNamespace(edit_reply_markup=AsyncMock())
        query = SimpleNamespace(
            data="deal|yes|not-an-offer",
            from_user=SimpleNamespace(id=111),
            message=message,
            answer=AsyncMock(),
        )
        await deal_gate.deal_gate_callback(
            SimpleNamespace(callback_query=query), SimpleNamespace()
        )

        query.answer.assert_awaited_once()
        message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)

    async def test_wrong_party_cannot_expire_the_real_users_button(self):
        from handlers import deal_gate

        message = SimpleNamespace(edit_reply_markup=AsyncMock())
        query = SimpleNamespace(
            data="deal|rcpt|264|go",
            from_user=SimpleNamespace(id=999),
            message=message,
            answer=AsyncMock(),
        )
        gate = {
            "offer_id": 264,
            "gate_status": "completed",
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
        }
        with patch.object(deal_gate, "deal_gate_get", return_value=gate):
            await deal_gate.deal_gate_callback(
                SimpleNamespace(callback_query=query), SimpleNamespace()
            )

        query.answer.assert_awaited_once()
        message.edit_reply_markup.assert_not_awaited()


class SensitiveAdminConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sensitive_action_requires_a_fresh_second_click(self):
        from handlers import deal_gate

        message = SimpleNamespace(edit_reply_markup=AsyncMock())
        query = SimpleNamespace(
            answer=AsyncMock(),
            message=message,
            from_user=SimpleNamespace(id=7001),
        )
        context = SimpleNamespace(user_data={})
        with patch.object(deal_gate, "_log") as log:
            first = await deal_gate._admin_sensitive_confirmation(
                context,
                query,
                action="close",
                offer_id=264,
                confirm_data="adm|dg|closeok|264",
                prompt="confirm",
                is_confirmation=False,
            )
            second = await deal_gate._admin_sensitive_confirmation(
                context,
                query,
                action="close",
                offer_id=264,
                confirm_data="adm|dg|closeok|264",
                prompt="confirm",
                is_confirmation=True,
            )
        self.assertFalse(first)
        self.assertTrue(second)
        self.assertIn("admin_id", log.call_args.args[1])
        message.edit_reply_markup.assert_awaited_once()

    async def test_support_operator_is_read_only_for_money_actions(self):
        from handlers import deal_gate

        query = SimpleNamespace(from_user=SimpleNamespace(id=7002), answer=AsyncMock())
        with (
            patch.object(deal_gate, "ADMIN_IDS", [7001, 7002]),
            patch.object(deal_gate, "DEAL_SUPPORT_ADMIN_IDS", [7002]),
        ):
            self.assertFalse(await deal_gate._require_full_deal_admin(query))
        query.answer.assert_awaited_once()


class QuietNotificationTests(unittest.TestCase):
    def test_seller_receives_at_most_one_delayed_reminder(self):
        from handlers import deal_gate

        gate = {
            "offer_id": 264,
            "gate_status": "completed",
            "seller_telegram_id": 222,
            "seller_toman_settled_at": 0,
            "seller_toman_close_enabled_at": 1_000,
        }
        with (
            patch.object(
                deal_gate,
                "deal_gate_seller_toman_admin_list",
                return_value=[{"delivered_at": 1_000}],
            ),
            patch.object(deal_gate, "_last_seller_stom_reminder_at", return_value=0),
        ):
            self.assertTrue(deal_gate._seller_stom_reminder_due(gate, now=29_800))

        with (
            patch.object(
                deal_gate,
                "deal_gate_seller_toman_admin_list",
                return_value=[{"delivered_at": 1_000}],
            ),
            patch.object(
                deal_gate,
                "_last_seller_stom_reminder_at",
                return_value=29_800,
            ),
        ):
            self.assertFalse(deal_gate._seller_stom_reminder_due(gate, now=100_000))


class InvariantRepairTests(unittest.TestCase):
    def test_safe_repair_never_invents_a_payment_confirmation(self):
        import database.db as db

        gate = {
            "offer_id": 264,
            "advert_rowid": 3470,
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
            "gate_status": "completed",
            "buyer_response": "yes",
            "seller_response": "yes",
            "buyer_accounts_text": "buyer account",
            "seller_accounts_text": "seller account",
            "seller_toman_close_enabled_at": 123,
            "seller_toman_admin_log": "[]",
            "seller_toman_settled_at": 0,
            "seller_receipt_log": "[]",
            "seller_eur_account_sent_at": 1,
        }
        repaired = {**gate, "seller_toman_close_enabled_at": None}
        fake_conn = Mock()
        fake_conn.execute.return_value.fetchone.return_value = None
        fake_cm = Mock()
        fake_cm.__enter__ = Mock(return_value=fake_conn)
        fake_cm.__exit__ = Mock(return_value=False)
        with (
            patch.object(db, "deal_gate_get", side_effect=[gate, gate, repaired, repaired]),
            patch.object(db, "deal_gate_upsert") as upsert,
            patch.object(db, "deal_gate_close_atomic") as close,
            patch.object(db.sqlite3, "connect", return_value=fake_cm),
        ):
            remaining = db.deal_gate_repair_safe(264)

        self.assertEqual(remaining, [])
        self.assertIsNone(upsert.call_args.kwargs["seller_toman_close_enabled_at"])
        close.assert_not_called()


class ProblemDashboardTests(unittest.TestCase):
    def test_receipt_warning_never_auto_approves_a_mismatched_account(self):
        from handlers import deal_gate

        warnings = deal_gate._receipt_consistency_warnings(
            {"seller_accounts_text": "IR120000000000001234567890"},
            "amount 204750000 reference 998877",
            receipt_kind="seller_toman",
        )
        self.assertTrue(any("7890" in warning for warning in warnings))

    def test_problem_dashboard_is_passive_and_explains_issues(self):
        from handlers import deal_gate

        text = deal_gate.build_admin_problem_deals_html(
            [
                {
                    "offer_id": 264,
                    "advert_rowid": 3470,
                    "problem_age_seconds": 13 * 3600,
                    "problem_issues": [
                        "stuck_completed",
                        "critical_delivery_pending",
                    ],
                }
            ]
        )
        self.assertIn("264", text)
        self.assertIn("3470", text)
        self.assertIn("هیچ اعلان خودکاری", text)
        self.assertIn("ارسال تلگرام", text)

    def test_timeline_combines_events_outbound_and_delivery(self):
        from handlers import deal_gate

        with (
            patch.object(
                deal_gate,
                "negotiation_transcript_list",
                return_value=[{"from": "admin", "text": "closed", "created_at": 10}],
            ),
            patch.object(
                deal_gate,
                "bot_outbound_log_list",
                return_value=[
                    {
                        "party": "seller",
                        "tag": "status",
                        "recipient_telegram_id": 22,
                        "telegram_message_id": 7,
                        "created_at": 11,
                    }
                ],
            ),
            patch.object(
                deal_gate,
                "deal_delivery_list_for_offer",
                return_value=[
                    {
                        "status": "sent",
                        "tag": "status",
                        "attempts": 0,
                        "updated_at": 12,
                    }
                ],
            ),
        ):
            text = deal_gate.build_deal_timeline_text(264)
        self.assertIn("EVENT [admin] closed", text)
        self.assertIn("OUTBOUND [seller]", text)
        self.assertIn("DELIVERY [sent]", text)


if __name__ == "__main__":
    unittest.main()
