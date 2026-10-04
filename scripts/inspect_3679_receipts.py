"""Read-only metadata inspection of missing reviewer receipts."""
import json
import sqlite3
from database import db

with sqlite3.connect(db.DB_PATH) as conn:
    conn.row_factory = sqlite3.Row
    gate = dict(conn.execute('SELECT * FROM offer_deal_gates WHERE offer_id=537').fetchone())
    assert int(gate['advert_rowid']) == 3679
    owner = int(gate.get('viewer_toman_claimed_by') or 0)
    print('target', json.dumps({k: gate.get(k) for k in ('offer_id','advert_rowid','viewer_toman_claimed_by','viewer_toman_due_at','seller_toman_settled_at')}))
    for key in ('buyer_receipt_log','seller_receipt_log','seller_toman_admin_log','viewer_toman_receipt_log'):
        entries = json.loads(gate.get(key) or '[]')
        print(key, json.dumps([{k: r.get(k) for k in ('type','at','source_message_id','viewer_id')} for r in entries]))
    for row in conn.execute('SELECT offer_id,advert_rowid,viewer_toman_receipt_log FROM offer_deal_gates WHERE viewer_toman_claimed_by=?', (owner,)):
        print('owner_task', row['offer_id'], row['advert_rowid'], len(json.loads(row['viewer_toman_receipt_log'] or '[]')))
    print('outgoing', json.dumps([dict(r) for r in conn.execute('SELECT id,offer_id,uploader,status,created_at FROM deal_outgoing_drafts WHERE offer_id=537 OR uploader=? ORDER BY id DESC LIMIT 12', (owner,))]))
