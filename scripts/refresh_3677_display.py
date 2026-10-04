"""One-off display refresh; does not change payment or accounting state."""
import asyncio
import json
from telegram import Bot
from config.settings import BOT_TOKEN
from database.db import deal_gate_get
from database import outgoing_receipts as store
from handlers.deal_gate import _viewer_toman_body, _viewer_toman_keyboard, _viewer_toman_ids
from handlers.deal_outgoing import preview


async def main():
    gate = deal_gate_get(525)
    assert gate and int(gate['advert_rowid']) == 3677, 'Deal mapping mismatch'
    mids = json.loads(gate.get('viewer_toman_notify_mids') or '{}')
    owner = int(gate.get('viewer_toman_claimed_by') or 0)
    receipts = json.loads(gate.get('viewer_toman_receipt_log') or '[]')
    viewers = _viewer_toman_ids()
    async with Bot(BOT_TOKEN) as bot:
        for uid, mid in mids.items():
            uid = int(uid)
            if uid not in viewers:
                continue
            await bot.edit_message_text(
                chat_id=uid, message_id=int(mid),
                text=_viewer_toman_body(gate, claimed=bool(owner), receipt_count=len(receipts)),
                parse_mode='HTML',
                reply_markup=_viewer_toman_keyboard(525, claimed=bool(owner), owner=uid == owner))
            print('Updated reviewer payment message for ad 3677')
        with store.connect() as conn:
            drafts = conn.execute("SELECT id,uploader FROM deal_outgoing_drafts WHERE offer_id=? AND status='draft'", (525,)).fetchall()
        for draft in drafts:
            if draft['uploader'] in viewers:
                await preview(bot, draft['uploader'], draft['id'])
                print('Resent pending outgoing preview for ad 3677')


if __name__ == '__main__':
    asyncio.run(main())
