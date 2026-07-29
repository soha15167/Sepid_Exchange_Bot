"""Admin may confirm the seller's final Toman settlement safely."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class TestAdminSellerTomanSettlement(unittest.IsolatedAsyncioTestCase):
    def test_admin_button_is_scoped_to_the_awaiting_offer(self):
        from handlers import deal_gate

        gate = {
            "offer_id": 41,
            "gate_status": "completed",
            "seller_toman_close_enabled_at": 123,
            "seller_toman_settled_at": 0,
        }
        with (
            patch.object(
                deal_gate,
                "_gate_awaiting_seller_toman_close",
                return_value=True,
            ),
            patch(
                "handlers.offers._seller_euro_fully_confirmed_gate",
                return_value=False,
            ),
        ):
            rows = deal_gate.deal_admin_payment_only_rows(41, gate)

        buttons = [button for row in rows for button in row]
        matching = [b for b in buttons if b.callback_data == "adm|stomset|41"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(
            matching[0].text,
            "✅ فروشنده تومان را دریافت کرد",
        )

    def test_admin_button_is_visible_at_final_payment_stage_before_receipt(self):
        from handlers import deal_gate

        gate = {
            "offer_id": 262,
            "gate_status": "completed",
            "seller_toman_close_enabled_at": 0,
            "seller_toman_settled_at": 0,
        }
        with (
            patch.object(
                deal_gate,
                "_gate_awaiting_seller_toman_close",
                return_value=False,
            ),
            patch(
                "handlers.offers._seller_euro_fully_confirmed_gate",
                return_value=True,
            ),
        ):
            rows = deal_gate.deal_admin_payment_only_rows(262, gate)

        buttons = [button for row in rows for button in row]
        matching = [b for b in buttons if b.callback_data == "adm|stomset|262"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].text, "✅ فروشنده تومان را دریافت کرد")

    async def test_admin_confirmation_records_milestone_and_closes_deal(self):
        from handlers import deal_gate

        gate = {
            "offer_id": 41,
            "advert_rowid": 77,
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
            "gate_status": "completed",
            "seller_toman_close_enabled_at": 123,
            "seller_toman_settled_at": 0,
        }
        settled_gate = {
            **gate,
            "gate_status": "closed",
            "seller_toman_settled_at": 456,
        }
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            data="adm|stomset|41",
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(bot=object())

        with (
            patch.object(deal_gate, "ADMIN_IDS", [999]),
            patch.object(
                deal_gate,
                "_admin_sensitive_confirmation",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                deal_gate,
                "deal_gate_get",
                side_effect=[gate, settled_gate],
            ),
            patch.object(
                deal_gate,
                "_gate_awaiting_seller_toman_close",
                return_value=True,
            ),
            patch.object(
                deal_gate,
                "get_advert_offer_joined",
                return_value={"advert_rowid": 77},
            ),
            patch.object(
                deal_gate,
                "deal_gate_settle_and_close_atomic",
                return_value=True,
            ) as mark_settled,
            patch.object(deal_gate, "_log") as log,
            patch.object(
                deal_gate,
                "_finalize_deal_close",
                new=AsyncMock(),
            ) as finalize,
        ):
            await deal_gate.deal_admin_seller_toman_settled_callback(
                update, context
            )

        self.assertEqual(mark_settled.call_args.args, (41, 77))
        self.assertGreater(mark_settled.call_args.kwargs["settled_at"], 0)
        self.assertFalse(mark_settled.call_args.kwargs["require_receipt"])
        log.assert_called_once_with(
            41,
            "ادمین از طرف فروشنده تأیید کرد: تومان نشست",
            from_role="admin",
        )
        finalize.assert_awaited_once_with(
            context,
            41,
            settled_gate,
            {"advert_rowid": 77},
            closed_by="admin",
            answer_query=query,
            persist_close=False,
        )

    async def test_admin_can_confirm_without_recorded_receipt_at_final_stage(self):
        from handlers import deal_gate

        gate = {
            "offer_id": 41,
            "advert_rowid": 77,
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
            "gate_status": "completed",
            "seller_toman_close_enabled_at": 0,
            "seller_toman_settled_at": 0,
        }
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            data="adm|stomset|41",
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )
        context = SimpleNamespace(bot=object())
        settled_gate = {
            **gate,
            "gate_status": "closed",
            "seller_toman_settled_at": 456,
        }

        with (
            patch.object(deal_gate, "ADMIN_IDS", [999]),
            patch.object(
                deal_gate,
                "_admin_sensitive_confirmation",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                deal_gate,
                "deal_gate_get",
                side_effect=[gate, settled_gate],
            ),
            patch.object(
                deal_gate,
                "_gate_awaiting_seller_toman_close",
                return_value=False,
            ),
            patch(
                "handlers.offers._seller_euro_fully_confirmed_gate",
                return_value=True,
            ),
            patch.object(
                deal_gate,
                "deal_gate_settle_and_close_atomic",
                return_value=True,
            ) as mark_settled,
            patch.object(
                deal_gate,
                "get_advert_offer_joined",
                return_value={"advert_rowid": 77},
            ),
            patch.object(deal_gate, "_log"),
            patch.object(
                deal_gate,
                "refresh_admin_deal_markup",
                new=AsyncMock(),
            ),
            patch.object(
                deal_gate,
                "_finalize_deal_close",
                new=AsyncMock(),
            ) as finalize,
        ):
            await deal_gate.deal_admin_seller_toman_settled_callback(
                SimpleNamespace(callback_query=query), context
            )

        self.assertEqual(mark_settled.call_args.args, (41, 77))
        self.assertFalse(mark_settled.call_args.kwargs["require_receipt"])
        finalize.assert_awaited_once_with(
            context,
            41,
            settled_gate,
            {"advert_rowid": 77},
            closed_by="admin",
            answer_query=query,
            persist_close=False,
        )


if __name__ == "__main__":
    unittest.main()
