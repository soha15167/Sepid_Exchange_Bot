"""Admin reminder detail and terminal deal-status synchronization."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


class AdminFullDealMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_problem_dashboard_is_opened_only_on_admin_request(self):
        from handlers import admin

        query = SimpleNamespace(
            data="adm|dgs|problems",
            from_user=SimpleNamespace(id=7001),
            message=SimpleNamespace(chat_id=7001, message_id=8999),
            answer=AsyncMock(),
        )
        context = SimpleNamespace(bot=object(), user_data={})
        with (
            patch.object(admin, "_is_admin", return_value=True),
            patch(
                "handlers.deal_gate.admin_show_problem_deals",
                new=AsyncMock(),
            ) as show,
        ):
            await admin.admin_dashboard_callback(
                SimpleNamespace(callback_query=query), context
            )

        show.assert_awaited_once()

    async def test_repair_callback_is_admin_only_and_rebuilds_without_user_notice(self):
        from handlers import admin

        query = SimpleNamespace(
            data="adm|dgs|repair|258",
            from_user=SimpleNamespace(id=7001),
            message=SimpleNamespace(chat_id=7001, message_id=9000),
            answer=AsyncMock(),
        )
        context = SimpleNamespace(bot=object(), user_data={})
        gate = {"offer_id": 258, "gate_status": "completed"}
        with (
            patch.object(admin, "_is_admin", return_value=True),
            patch("database.db.deal_gate_get", return_value=gate),
            patch("database.db.deal_gate_audit", return_value=[]),
            patch("database.db.deal_gate_repair_safe", return_value=[]),
            patch(
                "handlers.deal_gate._admin_sensitive_confirmation",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "handlers.deal_gate._require_full_deal_admin",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "handlers.deal_gate.sync_deal_admin_notification",
                new=AsyncMock(),
            ) as sync,
        ):
            await admin.admin_dashboard_callback(
                SimpleNamespace(callback_query=query),
                context,
            )

        sync.assert_awaited_once_with(context.bot, 258, deal_complete=True)
        self.assertIn("وضعیت سالم است", query.answer.await_args_list[-1].args[0])

    async def test_resync_callback_shows_full_message_for_an_accounts_deal(self):
        from handlers import admin

        query = SimpleNamespace(
            data="adm|dgs|resync|258",
            from_user=SimpleNamespace(id=7001),
            message=SimpleNamespace(chat_id=7001, message_id=9001),
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(bot=object(), user_data={})
        gate = {"offer_id": 258, "gate_status": "accounts"}

        with (
            patch.object(admin, "_is_admin", return_value=True),
            patch("database.db.deal_gate_get", return_value=gate),
            patch(
                "handlers.deal_gate.sync_deal_admin_notification",
                new=AsyncMock(),
            ) as sync,
        ):
            await admin.admin_dashboard_callback(update, context)

        sync.assert_awaited_once_with(
            context.bot,
            258,
            deal_complete=False,
        )
        self.assertEqual(
            query.answer.await_args_list[-1].args[0],
            "پیام کامل معامله برای ادمین نمایش داده شد",
        )

    async def test_resync_callback_includes_receipts_for_completed_deal(self):
        from handlers import admin

        query = SimpleNamespace(
            data="adm|dgs|resync|264",
            from_user=SimpleNamespace(id=7001),
            message=SimpleNamespace(chat_id=7001, message_id=9002),
            answer=AsyncMock(),
        )
        context = SimpleNamespace(bot=object(), user_data={})

        with (
            patch.object(admin, "_is_admin", return_value=True),
            patch(
                "database.db.deal_gate_get",
                return_value={"offer_id": 264, "gate_status": "completed"},
            ),
            patch(
                "handlers.deal_gate.sync_deal_admin_notification",
                new=AsyncMock(),
            ) as sync,
        ):
            await admin.admin_dashboard_callback(
                SimpleNamespace(callback_query=query),
                context,
            )

        sync.assert_awaited_once_with(
            context.bot,
            264,
            deal_complete=True,
        )


class AdminTerminalStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_updates_parties_and_the_complete_admin_message(self):
        from handlers import deal_gate

        context = SimpleNamespace(bot=object(), application=object())
        gate = {
            "offer_id": 258,
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
        }
        row = {"advert_rowid": 3453}

        with (
            patch.object(deal_gate, "deal_gate_close_atomic", return_value=True) as close,
            patch.object(
                deal_gate, "_refresh_deal_channel_status", new=AsyncMock()
            ),
            patch.object(deal_gate, "_log"),
            patch.object(deal_gate, "cancel_seller_stom_close_reminder"),
            patch.object(deal_gate, "_purge_gate_ui", new=AsyncMock()),
            patch.object(deal_gate, "_cancel_gate_jobs"),
            patch.object(
                deal_gate,
                "_enqueue_and_deliver_deal_message",
                new=AsyncMock(return_value=True),
            ) as notify_parties,
            patch.object(
                deal_gate,
                "sync_deal_admin_notification",
                new=AsyncMock(),
            ) as sync_admin,
        ):
            await deal_gate._finalize_deal_close(
                context,
                258,
                gate,
                row,
                closed_by="admin",
            )

        close.assert_called_once_with(258, 3453)
        self.assertEqual(notify_parties.await_count, 2)
        self.assertEqual(
            {call.kwargs["chat_id"] for call in notify_parties.await_args_list},
            {111, 222},
        )
        sync_admin.assert_awaited_once_with(
            context.bot,
            258,
            deal_complete=True,
        )

    async def test_reactivated_status_replaces_every_admin_card_and_album(self):
        from handlers import deal_gate

        gate = {
            "offer_id": 258,
            "admin_notify_mids": json.dumps({"7001": 101, "7002": 102}),
            "admin_notify_photo_mids": json.dumps(
                {
                    "7001": {"album": [201, 202]},
                    "7002": {"album": [203]},
                }
            ),
        }
        bot = SimpleNamespace(
            edit_message_text=AsyncMock(),
            delete_message=AsyncMock(),
            send_message=AsyncMock(),
        )

        await deal_gate._replace_admin_deal_messages_with_status(
            bot,
            gate=gate,
            text="reactivated",
        )

        edited = {
            (call.kwargs["chat_id"], call.kwargs["message_id"])
            for call in bot.edit_message_text.await_args_list
        }
        self.assertEqual(edited, {(7001, 101), (7002, 102)})
        deleted = {
            tuple(call.args) for call in bot.delete_message.await_args_list
        }
        self.assertEqual(deleted, {(7001, 201), (7001, 202), (7002, 203)})
        bot.send_message.assert_not_awaited()

    async def test_reactivate_archives_then_updates_admins_and_both_parties(self):
        from handlers import deal_gate

        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(
            bot=bot,
            application=SimpleNamespace(job_queue=None),
        )
        q = SimpleNamespace(
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        gate = {
            "offer_id": 258,
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
        }
        row = {"advert_rowid": 3453, "seq_in_advert": 1}
        events: list[str] = []

        def archive_gate(_offer_id, _advert_id):
            events.append("archive")
            return True

        async def replace_admin(*_args, **_kwargs):
            events.append("admin_status")

        with (
            patch.object(
                deal_gate,
                "deal_gate_archive_and_reactivate",
                side_effect=archive_gate,
            ),
            patch.object(deal_gate, "_log"),
            patch.object(deal_gate, "_purge_gate_ui", new=AsyncMock()),
            patch.object(deal_gate, "_cancel_gate_jobs"),
            patch(
                "handlers.offers.refresh_advert_channel_post",
                new=AsyncMock(),
            ),
            patch.object(
                deal_gate,
                "_replace_admin_deal_messages_with_status",
                side_effect=replace_admin,
            ),
            patch.object(
                deal_gate,
                "_enqueue_and_deliver_deal_message",
                new=AsyncMock(return_value=True),
            ) as notify_parties,
        ):
            await deal_gate._reactivate_advert(context, 258, gate, row, q)

        self.assertEqual(events, ["archive", "admin_status"])
        recipients = {
            call.kwargs["chat_id"] for call in notify_parties.await_args_list
        }
        self.assertEqual(recipients, {111, 222})
        for call in notify_parties.await_args_list:
            self.assertIn(
                "معامله لغو و آگهی دوباره فعال شد",
                call.kwargs["payload"]["body_html"],
            )
        q.message.edit_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
