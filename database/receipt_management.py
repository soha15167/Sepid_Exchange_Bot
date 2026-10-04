"""Recoverable attachment removal. Financial state is deliberately preserved."""
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from database import db

KINDS = {'buyer': 'buyer_receipt_log', 'euro': 'seller_receipt_log',
         'admin': 'seller_toman_admin_log', 'viewer': 'viewer_toman_receipt_log'}


def fingerprint(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def edit_buyer_amount(offer_id, index, expected, amount, actor):
    if not isinstance(amount, int) or not 0 < amount < 10**16:
        return None
    with closing(sqlite3.connect(db.DB_PATH, timeout=20)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT buyer_receipt_log,buyer_toman_settled_at,gate_status FROM offer_deal_gates WHERE offer_id=?', (offer_id,)).fetchone()
        if not row or int(row[1] or 0) > 0 or row[2] != 'completed':
            return None
        items = json.loads(row[0] or '[]')
        if index < 0 or index >= len(items):
            return None
        item = items[index]
        if fingerprint(item) != expected or item.get('attachment_removed_at') or item.get('accounting_status') in {'submitted','submitting','panel_failed','duplicate','rejected'}:
            return None
        stamp = int(time.time())
        item.setdefault('amount_edit_history', []).append({'actor': actor, 'at': stamp, 'old': item.get('amount_rial'), 'new': amount})
        item.update(amount_rial=amount, recognized_amount_rial=amount, reviewer_received_at=0, reviewer_received_by=0,
                    recognition_source='manual', fee_adjusted_to_remaining=False)
        item['accounting_status'] = 'ready_for_review' if all(item.get(k) for k in ('bank_name','transfer_type','jdate')) else 'review'
        conn.execute('UPDATE offer_deal_gates SET buyer_receipt_log=? WHERE offer_id=?', (json.dumps(items, ensure_ascii=False), offer_id))
        return dict(item)


def edit_buyer_accounting_fields(offer_id, index, expected, bank_name, transfer_type, jdate, actor):
    """Correct receipt metadata before settlement; never submits or settles money."""
    values = (str(bank_name or '').strip(), str(transfer_type or '').strip(), str(jdate or '').strip())
    if not all(values) or any(len(value) > 120 for value in values):
        return None
    with closing(sqlite3.connect(db.DB_PATH, timeout=20)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT buyer_receipt_log,buyer_toman_settled_at,gate_status FROM offer_deal_gates WHERE offer_id=?', (offer_id,)).fetchone()
        if not row or int(row[1] or 0) > 0 or row[2] != 'completed':
            return None
        items = json.loads(row[0] or '[]')
        if index < 0 or index >= len(items):
            return None
        item = items[index]
        if fingerprint(item) != expected or item.get('attachment_removed_at') or item.get('accounting_status') in {'submitted', 'submitting', 'duplicate', 'rejected'}:
            return None
        stamp = int(time.time())
        item.setdefault('accounting_field_edit_history', []).append({
            'actor': actor, 'at': stamp, 'bank_name': item.get('bank_name') or '',
            'transfer_type': item.get('transfer_type') or '', 'jdate': item.get('jdate') or '',
        })
        item.update(bank_name=values[0], transfer_type=values[1], jdate=values[2],
                    accounting_status='ready_for_review', panel_error='', recognition_source='manual')
        conn.execute('UPDATE offer_deal_gates SET buyer_receipt_log=? WHERE offer_id=?', (json.dumps(items, ensure_ascii=False), offer_id))
        return dict(item)


def archive(offer_id, kind, index, expected, actor):
    column = KINDS[kind]
    with closing(sqlite3.connect(db.DB_PATH, timeout=20)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS receipt_attachment_audit (id INTEGER PRIMARY KEY, offer_id INTEGER, kind TEXT, receipt_index INTEGER, actor INTEGER, at INTEGER, original_json TEXT)')
        row = conn.execute(f'SELECT {column} FROM offer_deal_gates WHERE offer_id=?', (offer_id,)).fetchone()
        items = json.loads(row[0] or '[]') if row else []
        if index < 0 or index >= len(items):
            return False
        item = items[index]
        if item.get('attachment_removed_at') or fingerprint(item) != expected:
            return False
        stamp = int(time.time())
        conn.execute('INSERT INTO receipt_attachment_audit (offer_id,kind,receipt_index,actor,at,original_json) VALUES (?,?,?,?,?,?)',
                     (offer_id, kind, index, actor, stamp, json.dumps(item, ensure_ascii=False)))
        # Keep list positions and financial fields so old buttons cannot target
        # a different receipt and existing accounting is never silently reversed.
        items[index] = {**item, 'file_id': '', 'text': 'فیش توسط ادمین حذف شد',
                        'type': 'removed', 'attachment_removed_at': stamp, 'attachment_removed_by': actor}
        conn.execute(f'UPDATE offer_deal_gates SET {column}=? WHERE offer_id=?', (json.dumps(items, ensure_ascii=False), offer_id))
        if kind in {'viewer', 'admin'} and item.get('file_id'):
            mirror = KINDS['admin' if kind == 'viewer' else 'viewer']
            other = json.loads(conn.execute(f'SELECT {mirror} FROM offer_deal_gates WHERE offer_id=?', (offer_id,)).fetchone()[0] or '[]')
            for pos, entry in enumerate(other):
                if entry.get('file_id') == item['file_id'] and not entry.get('attachment_removed_at'):
                    conn.execute('INSERT INTO receipt_attachment_audit (offer_id,kind,receipt_index,actor,at,original_json) VALUES (?,?,?,?,?,?)',
                        (offer_id, 'admin' if kind == 'viewer' else 'viewer', pos, actor, stamp, json.dumps(entry, ensure_ascii=False)))
                    other[pos] = {**entry, 'file_id': '', 'text': 'فیش توسط ادمین حذف شد', 'type': 'removed', 'attachment_removed_at': stamp, 'attachment_removed_by': actor}
            conn.execute(f'UPDATE offer_deal_gates SET {mirror}=? WHERE offer_id=?', (json.dumps(other, ensure_ascii=False), offer_id))
        # Legacy drafts have no reliable file link. Invalidate their approval
        # buttons conservatively, never touch submitted/in-flight entries.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deal_outgoing_drafts'").fetchone():
            conn.execute("UPDATE deal_outgoing_drafts SET status='skipped',error='Receipt removed; admin must review and recreate pending previews',confirmed_by=?,finished_at=? WHERE offer_id=? AND status='draft'", (actor, stamp, offer_id))
        return True
