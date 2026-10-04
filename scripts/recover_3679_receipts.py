"""Recover explicitly supplied receipts; never submit ledger transactions."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from telegram import Bot
from config.settings import BOT_TOKEN
from database import outgoing_receipts as drafts
from database.db import deal_gate_get, deal_gate_append_viewer_toman_receipt
from handlers.deal_gate import _enqueue_and_deliver_deal_message, _update_viewer_toman_prompts, sync_deal_admin_notification, _viewer_toman_ids
from handlers.deal_outgoing import preview

FILES = [
    ('codex-clipboard-29b300af-7754-40d9-b8ed-5fe327e35462.jpg', 218750000, 'ملت', 'پایا'),
    ('codex-clipboard-994881a1-a786-4a6e-b414-a96ed7328327.jpg', 70000000, 'سامان', 'حساب به حساب'),
    ('codex-clipboard-482fbff3-6143-4585-a6f6-f59e3a0e8e65.jpg', 80000000, 'ملی', 'کارت به کارت'),
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    gate = deal_gate_get(537)
    assert gate and int(gate['advert_rowid']) == 3679
    owner = int(gate['viewer_toman_claimed_by'])
    seller = int(gate['seller_telegram_id'])
    assert owner in _viewer_toman_ids() and seller > 0
    print('Seller account:', gate.get('seller_accounts_text'))
    print('Existing reviewer receipts:', len(json.loads(gate.get('viewer_toman_receipt_log') or '[]')))
    if not args.apply:
        return
    state_path = Path('/root/sepid-3679-recovery/state.json')
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    async with Bot(BOT_TOKEN) as bot:
        for index, (name, amount, bank, transfer) in enumerate(FILES, 1):
            path = state_path.parent / name
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            key = f'recovered:3679:{digest}'
            note = '⚠️ درخواست پایا؛ این رسید تأیید وصول قطعی وجه نیست.' if index == 1 else ''
            caption = f'آگهی 3679 — فیش {index} از 3\n{note}'
            if key not in state:
                with path.open('rb') as photo:
                    sent = await bot.send_photo(owner, photo, caption=caption)
                state[key] = {'file_id': sent.photo[-1].file_id, 'message_id': sent.message_id}
                state_path.write_text(json.dumps(state))
            item = state[key]
            current = deal_gate_get(537)
            receipts = json.loads(current.get('viewer_toman_receipt_log') or '[]')
            if not any(r.get('file_id') == item['file_id'] for r in receipts):
                assert deal_gate_append_viewer_toman_receipt(537, viewer_id=owner, entry_type='photo', text=caption, file_id=item['file_id'], source_message_id=item['message_id']) is not None
            delivered = await _enqueue_and_deliver_deal_message(bot, offer_id=537, chat_id=seller, party='seller', tag='فیش تومان بازیابی‌شده بررسی‌کننده', payload_type='photo', payload={'file_id': item['file_id'], 'body_html': caption}, dedupe_key=key)
            print(f'Receipt {index}: seller delivered={delivered}')
            payload = dict(iran_amount=amount, bank_name=bank, dest_bank='سامان', depositor_name='بهروز عرفانی نسب', transfer_type=transfer, jdate='1405/06/18', description='آگهی 3679', _receipt_kind='viewer_toman')
            did = drafts.create(537, owner, key, payload)
            if not item.get('preview_sent'):
                if note:
                    await bot.send_message(owner, f'آگهی 3679 — پیش از تأیید خروجی فیش {index}: {note}')
                await preview(bot, owner, did)
                item['preview_sent'] = True
                state_path.write_text(json.dumps(state))
        await _update_viewer_toman_prompts(bot, deal_gate_get(537))
        await sync_deal_admin_notification(bot, 537, deal_complete=True)
        print('Reviewer and admin refresh complete; no accounting submitted')


if __name__ == '__main__':
    asyncio.run(main())
