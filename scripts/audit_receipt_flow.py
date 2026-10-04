"""Read-only receipt workflow summary; excludes account and receipt content."""
import json
import sqlite3
from database import db
from database.db import deal_gate_list_for_admin
from handlers.deal_gate import _buyer_toman_reviewer_totals

with sqlite3.connect(db.DB_PATH) as conn:
    conn.row_factory = sqlite3.Row
    gates = [dict(r) for r in conn.execute("SELECT * FROM offer_deal_gates WHERE gate_status='completed' AND advert_rowid>=3680")]
for g in gates:
    if int(g.get('advert_rowid') or 0) < 3680 or g.get('gate_status') != 'completed':
        continue
    receipts = json.loads(g.get('buyer_receipt_log') or '[]')
    print(json.dumps({'deal': g['advert_rowid'], 'offer': g['offer_id'],
        'settled': bool(g.get('buyer_toman_settled_at')), 'totals': _buyer_toman_reviewer_totals(g, receipts),
        'receipts': [{k:r.get(k) for k in ('attachment_removed_at','accounting_status','amount_rial','reviewer_received_at')} for r in receipts],
        'viewer_payout_receipts': len(json.loads(g.get('viewer_toman_receipt_log') or '[]')),
        'seller_close_enabled': bool(g.get('seller_toman_close_enabled_at'))}))
