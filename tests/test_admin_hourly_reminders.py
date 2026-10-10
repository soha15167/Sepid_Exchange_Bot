"""Hourly admin reminders refresh the original deal card without extra messages."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import handlers.deal_gate as deal_gate
import main as bot_main
import utils.deal_outbound as deal_outbound


def _gate(**changes) -> dict:
    gate = {
        "offer_id": 252,
        "advert_rowid": 3448,
        "gate_status": "accounts",
        "started_at": 1_000,
        "seller_toman_settled_at": 0,
        "seller_toman_close_enabled_at": 0,
    }
    gate.update(changes)
    return gate


def _reminder_row(admin_id: int, message_id: int, *, created_at: int = 4_500):
    return {
        "recipient_telegram_id": admin_id,
        "party": "admin",
        "tag": deal_gate._ADMIN_TOMAN_REMINDER_TAG,
        "created_at": created_at,
        "telegram_message_id": message_id,
    }


class AdminHourlyReminderTests(unittest.IsolatedAsyncioTestCase):
    async def _run_sweep(
        self,
        *,
        gates=None,
        admin_ids=(7001,),
        rows=None,
        sync_result=None,
        sync_error=None,
        bot=None,
        now=4_600,
    ):
        bot = bot or SimpleNamespace(
            send_message=AsyncMock(), delete_message=AsyncMock()
        )
        sync = AsyncMock(return_value=sync_result or {}, side_effect=sync_error)
        log = Mock()
        with (
            patch.object(
                deal_gate,
                "deal_gate_list_awaiting_admin_toman_receipt",
                return_value=[_gate()] if gates is None else gates,
            ),
            patch.object(deal_gate, "bot_outbound_log_list", return_value=rows or []),
            patch.object(deal_gate, "sync_deal_admin_notification", new=sync),
            patch("handlers.offers._deal_admin_recipient_ids", return_value=list(admin_ids)),
            patch("utils.deal_outbound.deal_bot_log_text", log),
        ):
            sent = await deal_gate.run_admin_toman_receipt_reminder_sweep(bot, now=now)
        return sent, bot, sync, log

    def test_due_is_scoped_to_deal_and_admin_and_waits_one_hour(self):
        with patch.object(deal_gate, "bot_outbound_log_list", return_value=[]):
            self.assertFalse(
                deal_gate._admin_toman_reminder_due(_gate(), 7001, now=4_599)
            )
            self.assertTrue(
                deal_gate._admin_toman_reminder_due(_gate(), 7001, now=4_600)
            )

        rows = [
            {
                "recipient_telegram_id": 7001,
                "party": "admin",
                "tag": deal_gate._ADMIN_TOMAN_REMINDER_TAG,
                "created_at": 4_500,
            }
        ]
        with patch.object(deal_gate, "bot_outbound_log_list", return_value=rows):
            self.assertFalse(
                deal_gate._admin_toman_reminder_due(_gate(), 7001, now=8_099)
            )
            self.assertTrue(
                deal_gate._admin_toman_reminder_due(_gate(), 7001, now=8_100)
            )
            self.assertTrue(
                deal_gate._admin_toman_reminder_due(_gate(), 7002, now=4_600)
            )

    def test_closed_rejected_or_delivered_deals_are_not_eligible(self):
        self.assertFalse(
            deal_gate._gate_awaiting_admin_toman_receipt(_gate(gate_status="closed"))
        )
        self.assertFalse(
            deal_gate._gate_awaiting_admin_toman_receipt(_gate(gate_status="rejected"))
        )
        self.assertFalse(
            deal_gate._gate_awaiting_admin_toman_receipt(
                _gate(seller_toman_close_enabled_at=2_000)
            )
        )
        self.assertFalse(
            deal_gate._gate_awaiting_admin_toman_receipt(
                _gate(seller_toman_settled_at=2_000)
            )
        )

    async def test_sweep_refreshes_original_deal_card_without_separate_message(self):
        sent, bot, sync, log = await self._run_sweep(
            admin_ids=[7001, 7002], sync_result={7001: 9001, 7002: 9002}
        )
        self.assertEqual(sent, 2)
        sync.assert_awaited_once_with(
            bot,
            252,
            deal_complete=False,
            resend_fresh=True,
            recipient_ids=[7001, 7002],
            reminder_only=True,
        )
        bot.send_message.assert_not_awaited()
        self.assertEqual(log.call_count, 2)
        self.assertEqual(
            [call.args[:4] for call in log.call_args_list],
            [
                (252, 7001, "admin", deal_gate._ADMIN_TOMAN_REMINDER_TAG),
                (252, 7002, "admin", deal_gate._ADMIN_TOMAN_REMINDER_TAG),
            ],
        )
        self.assertEqual(
            [call.kwargs["telegram_message_id"] for call in log.call_args_list],
            [9001, 9002],
        )
        bot.delete_message.assert_not_awaited()

    async def test_completed_deal_refresh_preserves_payment_stage(self):
        sent, bot, sync, _log = await self._run_sweep(
            gates=[_gate(gate_status="completed")], sync_result={7001: 9123}
        )
        self.assertEqual(sent, 1)
        sync.assert_awaited_once_with(
            bot, 252, deal_complete=True, resend_fresh=True, recipient_ids=[7001],
            reminder_only=True,
        )

    async def test_pending_deal_refresh_keeps_acceptance_stage(self):
        _sent, bot, sync, _log = await self._run_sweep(
            gates=[_gate(gate_status="pending")], sync_result={7001: 9123}
        )
        sync.assert_awaited_once_with(
            bot, 252, deal_complete=False, resend_fresh=True, recipient_ids=[7001],
            reminder_only=True,
        )

    async def test_refresh_targets_only_due_admins(self):
        sent, bot, sync, log = await self._run_sweep(
            admin_ids=[7001, 7002],
            rows=[
                _reminder_row(7001, 8123),
                _reminder_row(7002, 8234, created_at=5_000),
            ],
            sync_result={7001: 9123},
            now=8_100,
        )
        self.assertEqual(sent, 1)
        sync.assert_awaited_once_with(
            bot, 252, deal_complete=False, resend_fresh=True, recipient_ids=[7001],
            reminder_only=True,
        )
        self.assertEqual(log.call_args.args[1], 7001)
        bot.delete_message.assert_awaited_once_with(chat_id=7001, message_id=8123)

    async def test_not_due_does_not_refresh_or_reset_hour(self):
        sent, bot, sync, log = await self._run_sweep(now=4_599)
        self.assertEqual(sent, 0)
        sync.assert_not_awaited()
        log.assert_not_called()
        bot.send_message.assert_not_awaited()
        bot.delete_message.assert_not_awaited()

    async def test_partial_success_logs_and_cleans_only_successful_admin(self):
        sent, bot, sync, log = await self._run_sweep(
            admin_ids=[7001, 7002],
            rows=[_reminder_row(7001, 8123), _reminder_row(7002, 8234)],
            sync_result={7002: 9234},
            now=8_100,
        )
        self.assertEqual(sent, 1)
        self.assertEqual(sync.call_args.kwargs["recipient_ids"], [7001, 7002])
        log.assert_called_once()
        self.assertEqual(log.call_args.args[1], 7002)
        self.assertEqual(log.call_args.kwargs["telegram_message_id"], 9234)
        bot.delete_message.assert_awaited_once_with(chat_id=7002, message_id=8234)
        bot.send_message.assert_not_awaited()

    async def test_refresh_deletes_legacy_reminders_but_preserves_current_card(self):
        rows = [
            _reminder_row(7001, 8012, created_at=4_000),
            _reminder_row(7001, 8123),
            _reminder_row(7001, 9123),
            _reminder_row(7002, 8234),
            {**_reminder_row(7001, 8345), "tag": "unrelated admin message"},
            {**_reminder_row(7001, 8456), "party": "buyer"},
        ]
        sent, bot, _sync, _log = await self._run_sweep(
            rows=rows, sync_result={7001: 9123}, now=8_100
        )
        self.assertEqual(sent, 1)
        self.assertEqual(
            {
                (call.kwargs["chat_id"], call.kwargs["message_id"])
                for call in bot.delete_message.await_args_list
            },
            {(7001, 8012), (7001, 8123)},
        )

    async def test_no_success_preserves_old_reminder_and_retry_eligibility(self):
        rows = [_reminder_row(7001, 8123)]
        sent, bot, sync, log = await self._run_sweep(
            rows=rows, sync_result={}, now=8_100
        )
        self.assertEqual(sent, 0)
        sync.assert_awaited_once()
        log.assert_not_called()
        bot.delete_message.assert_not_awaited()
        with patch.object(deal_gate, "bot_outbound_log_list", return_value=rows):
            self.assertTrue(deal_gate._admin_toman_reminder_due(_gate(), 7001, now=8_100))

    async def test_refresh_error_is_not_logged_and_will_be_retried(self):
        sent, bot, sync, log = await self._run_sweep(
            rows=[_reminder_row(7001, 8123)],
            sync_error=RuntimeError("offline"),
            now=8_100,
        )
        self.assertEqual(sent, 0)
        sync.assert_awaited_once()
        log.assert_not_called()
        bot.send_message.assert_not_awaited()
        bot.delete_message.assert_not_awaited()

    async def test_failed_legacy_delete_does_not_lose_successful_refresh(self):
        bot = SimpleNamespace(
            send_message=AsyncMock(),
            delete_message=AsyncMock(side_effect=RuntimeError("cannot delete")),
        )
        sent, _bot, _sync, log = await self._run_sweep(
            bot=bot,
            rows=[_reminder_row(7001, 8123)],
            sync_result={7001: 9123},
            now=8_100,
        )
        self.assertEqual(sent, 1)
        log.assert_called_once()
        self.assertEqual(log.call_args.kwargs["telegram_message_id"], 9123)
        bot.delete_message.assert_awaited_once_with(chat_id=7001, message_id=8123)

    async def test_admin_reminders_are_hidden_from_party_message_replay(self):
        bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        reminder = {
            "recipient_telegram_id": 7001,
            "party": "admin",
            "tag": deal_gate._ADMIN_TOMAN_REMINDER_TAG,
            "msg_type": "text",
            "body_html": "internal reminder",
        }
        with patch.object(
            deal_outbound, "bot_outbound_log_list", return_value=[reminder]
        ):
            replayed = await deal_outbound.deal_admin_replay_outbound(bot, 7001, 252)
        self.assertFalse(replayed)
        bot.send_message.assert_not_awaited()

    async def test_party_message_replay_keeps_only_latest_summary_per_user(self):
        bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        rows = [
            {
                "recipient_telegram_id": 111,
                "party": "buyer",
                "tag": "پیام تکمیل معامله + منوی اصلی",
                "msg_type": "text",
                "body_html": "old buyer summary",
            },
            {
                "recipient_telegram_id": 222,
                "party": "seller",
                "tag": "پیام تکمیل معامله + منوی اصلی",
                "msg_type": "text",
                "body_html": "seller summary",
            },
            {
                "recipient_telegram_id": 111,
                "party": "buyer",
                "tag": "ویرایش اطلاعات معامله توسط ادمین",
                "msg_type": "text",
                "body_html": "current buyer summary",
            },
        ]
        with patch.object(
            deal_outbound, "bot_outbound_log_list", return_value=rows
        ):
            replayed = await deal_outbound.deal_admin_replay_outbound(bot, 7001, 252)

        self.assertTrue(replayed)
        replay_texts = [call.args[1] for call in bot.send_message.await_args_list[1:]]
        self.assertEqual(len(replay_texts), 2)
        self.assertFalse(any("old buyer summary" in text for text in replay_texts))
        self.assertTrue(any("current buyer summary" in text for text in replay_texts))
        self.assertTrue(any("seller summary" in text for text in replay_texts))


class AdminReminderSchedulerTests(unittest.TestCase):
    def test_quiet_operations_schedule_all_maintenance_jobs(self):
        queue = Mock()
        bot_main._setup_deal_operations_jobs(SimpleNamespace(job_queue=queue))
        self.assertEqual(queue.run_repeating.call_count, 4)
        names = {call.kwargs["name"] for call in queue.run_repeating.call_args_list}
        self.assertEqual(
            names,
            {
                "deal_verified_backup",
                "deal_privacy_retention",
                "deal_backup_restore_drill",
                "deal_daily_reconciliation",
            },
        )

    def test_critical_delivery_retry_checks_every_minute(self):
        queue = Mock()
        app = SimpleNamespace(job_queue=queue)
        bot_main._setup_deal_delivery_retry_job(app)
        queue.run_repeating.assert_called_once()
        kwargs = queue.run_repeating.call_args.kwargs
        self.assertEqual(kwargs["interval"], 60)
        self.assertEqual(kwargs["first"], 20)
        self.assertEqual(kwargs["name"], "deal_delivery_retry_sweep")

    def test_scheduler_checks_every_five_minutes(self):
        queue = Mock()
        app = SimpleNamespace(job_queue=queue)
        bot_main._setup_admin_toman_receipt_reminder_job(app)
        queue.run_repeating.assert_called_once()
        kwargs = queue.run_repeating.call_args.kwargs
        self.assertEqual(kwargs["interval"], 300)
        self.assertEqual(kwargs["first"], 60)
        self.assertEqual(kwargs["name"], "admin_toman_receipt_reminder_sweep")


if __name__ == "__main__":
    unittest.main()
