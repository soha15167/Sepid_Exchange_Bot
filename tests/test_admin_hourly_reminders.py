"""Tests for persistent hourly admin reminders before Toman receipt delivery."""

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


class AdminHourlyReminderTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_sweep_sends_persian_deal_specific_message_to_each_admin(self):
        bot = SimpleNamespace(
            send_message=AsyncMock(
                side_effect=[
                    SimpleNamespace(message_id=9001),
                    SimpleNamespace(message_id=9002),
                ]
            ),
            delete_message=AsyncMock(),
        )
        log = Mock()
        with (
            patch.object(
                deal_gate,
                "deal_gate_list_awaiting_admin_toman_receipt",
                return_value=[_gate()],
            ),
            patch.object(
                deal_gate,
                "get_advert_offer_joined",
                return_value={"advert_rowid": 3448, "seq_in_advert": 2},
            ),
            patch.object(deal_gate, "bot_outbound_log_list", return_value=[]),
            patch("handlers.offers._deal_admin_recipient_ids", return_value=[7001, 7002]),
            patch("utils.deal_outbound.deal_bot_log_text", log),
        ):
            sent = await deal_gate.run_admin_toman_receipt_reminder_sweep(
                bot, now=4_600
            )

        self.assertEqual(sent, 2)
        self.assertEqual(bot.send_message.await_count, 2)
        for call in bot.send_message.await_args_list:
            self.assertIn("یادآوری ساعتی ادمین", call.kwargs["text"])
            self.assertIn("ارسال فیش واریز تومان به فروشنده", call.kwargs["text"])
            buttons = [
                button
                for row in call.kwargs["reply_markup"].inline_keyboard
                for button in row
            ]
            self.assertTrue(
                any(button.callback_data == "adm|stomset|252" for button in buttons)
            )
            self.assertTrue(
                any(button.callback_data == "adm|dgs|252" for button in buttons)
            )
        self.assertEqual(log.call_count, 2)
        self.assertTrue(all(call.args[2] == "admin" for call in log.call_args_list))
        self.assertEqual(
            [call.kwargs["telegram_message_id"] for call in log.call_args_list],
            [9001, 9002],
        )
        bot.delete_message.assert_not_awaited()

    async def test_new_reminder_deletes_previous_tracked_reminder(self):
        old_row = {
            "recipient_telegram_id": 7001,
            "party": "admin",
            "tag": deal_gate._ADMIN_TOMAN_REMINDER_TAG,
            "created_at": 4_500,
            "telegram_message_id": 8123,
        }
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=9123)),
            delete_message=AsyncMock(),
        )
        with (
            patch.object(
                deal_gate,
                "deal_gate_list_awaiting_admin_toman_receipt",
                return_value=[_gate()],
            ),
            patch.object(deal_gate, "get_advert_offer_joined", return_value={}),
            patch.object(
                deal_gate, "bot_outbound_log_list", return_value=[old_row]
            ),
            patch("handlers.offers._deal_admin_recipient_ids", return_value=[7001]),
            patch("utils.deal_outbound.deal_bot_log_text"),
        ):
            sent = await deal_gate.run_admin_toman_receipt_reminder_sweep(
                bot, now=8_100
            )

        self.assertEqual(sent, 1)
        bot.delete_message.assert_awaited_once_with(
            chat_id=7001,
            message_id=8123,
        )

    async def test_failed_send_is_not_logged_and_will_be_retried(self):
        bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=RuntimeError("offline")),
            delete_message=AsyncMock(),
        )
        log = Mock()
        with (
            patch.object(
                deal_gate,
                "deal_gate_list_awaiting_admin_toman_receipt",
                return_value=[_gate()],
            ),
            patch.object(deal_gate, "get_advert_offer_joined", return_value={}),
            patch.object(deal_gate, "bot_outbound_log_list", return_value=[]),
            patch("handlers.offers._deal_admin_recipient_ids", return_value=[7001]),
            patch("utils.deal_outbound.deal_bot_log_text", log),
        ):
            sent = await deal_gate.run_admin_toman_receipt_reminder_sweep(
                bot, now=4_600
            )
        self.assertEqual(sent, 0)
        log.assert_not_called()
        bot.delete_message.assert_not_awaited()

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


class AdminReminderSchedulerTests(unittest.TestCase):
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
