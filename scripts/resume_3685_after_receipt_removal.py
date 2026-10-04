"""Replay an already-confirmed receipt after fixing the deleted-slot guard."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telegram.ext import Application
from config.settings import BOT_TOKEN
from database.db import deal_gate_get, deal_gate_buyer_receipt_list
from handlers import deal_gate as flow


async def main():
    gate = deal_gate_get(553)
    assert int(gate['advert_rowid']) == 3685
    if int(gate.get('buyer_toman_settled_at') or 0):
        print('Already settled; no replay needed')
        return
    receipts = deal_gate_buyer_receipt_list(553)
    active = [(i, r) for i, r in enumerate(receipts) if not r.get('attachment_removed_at')]
    assert len(active) == 4 and all(r.get('accounting_status') == 'submitted' and r.get('reviewer_received_at') for _, r in active)
    assert flow._buyer_toman_reviewer_totals(gate, receipts) == (266500000, 266500000, 0, 0)
    index, receipt = active[-1]
    actor = int(receipt['reviewer_received_by'])
    assert not flow._is_full_deal_admin(actor), 'Expected original reviewer confirmation'
    app = Application.builder().token(BOT_TOKEN).build()
    async with app:
        q = SimpleNamespace(data=f'adm|tomset|553|{index}', from_user=SimpleNamespace(id=actor), message=AsyncMock(), answer=AsyncMock())
        context = SimpleNamespace(bot=app.bot, application=app, user_data={}, bot_data=app.bot_data)
        # This recovery must never issue a new ledger POST.
        with patch('utils.iran_panel_client.post_transaction', side_effect=AssertionError('No new accounting allowed during recovery')):
            await flow._deal_admin_toman_settled_callback_locked(SimpleNamespace(callback_query=q), context)
        refreshed = deal_gate_get(553)
        print('buyer_toman_settled:', bool(refreshed.get('buyer_toman_settled_at')))
        print('seller_eur_account_sent:', bool(refreshed.get('seller_eur_account_sent_at')))
        print('result:', q.answer.call_args)


if __name__ == '__main__':
    asyncio.run(main())
