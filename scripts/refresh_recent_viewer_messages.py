"""Inspect recent notified deals; explicitly selected deals can be refreshed."""
import argparse
import asyncio
import json
import sqlite3

from telegram import Bot
from telegram.error import BadRequest
from config.settings import BOT_TOKEN
from database import db
from handlers.deal_gate import (
    _viewer_toman_body, _viewer_toman_keyboard, _viewer_toman_ids,
    sync_deal_admin_notification,
)


def recent():
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM offer_deal_gates WHERE viewer_toman_notify_mids IS NOT NULL ORDER BY CAST(viewer_toman_due_at AS INTEGER) DESC, offer_id DESC").fetchall()
    result = []
    for row in rows:
        gate = dict(row)
        mids = json.loads(gate.get('viewer_toman_notify_mids') or '{}')
        if isinstance(mids, dict) and mids:
            result.append(gate)
        if len(result) == 3:
            break
    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', nargs=3, type=int)
    args = parser.parse_args()
    gates = recent()
    for gate in gates:
        print(json.dumps({'offer_id': gate['offer_id'], 'deal': gate['advert_rowid'],
                          'due_at': gate['viewer_toman_due_at'],
                          'reviewer_messages': len(json.loads(gate['viewer_toman_notify_mids'])),
                          'receipts': len(json.loads(gate.get('viewer_toman_receipt_log') or '[]'))}))
    if not args.apply:
        return
    assert args.apply == [int(g['offer_id']) for g in gates], 'Recent deals changed; inspect again'
    async with Bot(BOT_TOKEN) as bot:
        for gate in gates:
            oid = int(gate['offer_id'])
            owner = int(gate.get('viewer_toman_claimed_by') or 0)
            count = len(json.loads(gate.get('viewer_toman_receipt_log') or '[]'))
            for uid, mid in json.loads(gate['viewer_toman_notify_mids']).items():
                uid = int(uid)
                if uid not in _viewer_toman_ids():
                    continue
                try:
                    await bot.edit_message_text(chat_id=uid, message_id=int(mid),
                        text=_viewer_toman_body(gate, claimed=bool(owner), receipt_count=count),
                        parse_mode='HTML', reply_markup=_viewer_toman_keyboard(oid, claimed=bool(owner), owner=uid == owner))
                    print(f"Deal {gate['advert_rowid']}: reviewer message updated")
                except BadRequest as exc:
                    if 'message is not modified' not in str(exc).lower():
                        raise
                    print(f"Deal {gate['advert_rowid']}: reviewer message already current")
            await sync_deal_admin_notification(bot, oid, deal_complete=True)
            print(f"Deal {gate['advert_rowid']}: admin refresh completed")


if __name__ == '__main__':
    asyncio.run(main())
