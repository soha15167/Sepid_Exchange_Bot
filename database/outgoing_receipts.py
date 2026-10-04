"""Durable outgoing drafts; an uncertain HTTP result must never auto-retry."""
import json
import sqlite3
import time
from contextlib import contextmanager
from database import db


@contextmanager
def connect():
    conn = sqlite3.connect(db.DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS deal_outgoing_drafts (
        id INTEGER PRIMARY KEY, offer_id INTEGER NOT NULL, uploader INTEGER NOT NULL,
        source_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft', created_at INTEGER NOT NULL,
        confirmed_by INTEGER, finished_at INTEGER, error TEXT NOT NULL DEFAULT ''
    )""")
    conn.commit()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def create(offer_id, uploader, source_key, payload):
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO deal_outgoing_drafts (offer_id,uploader,source_key,payload,created_at) VALUES (?,?,?,?,?)",
                     (offer_id, uploader, source_key, json.dumps(payload, ensure_ascii=False), int(time.time())))
        return conn.execute("SELECT id FROM deal_outgoing_drafts WHERE source_key=?", (source_key,)).fetchone()[0]


def get(draft_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM deal_outgoing_drafts WHERE id=?", (draft_id,)).fetchone()
        return dict(row) if row else None


def transition(draft_id, status, *, actor, payload=None, expected=None):
    if status not in {'submitting', 'skipped', 'draft'}:
        raise ValueError(status)
    with connect() as conn:
        return conn.execute("UPDATE deal_outgoing_drafts SET status=?,confirmed_by=?,payload=COALESCE(?,payload) WHERE id=? AND status='draft' AND (? IS NULL OR payload=?)",
                            (status, actor, json.dumps(payload, ensure_ascii=False) if payload is not None else None, draft_id, expected, expected)).rowcount == 1


def finish(draft_id, ok, error=''):
    with connect() as conn:
        conn.execute("UPDATE deal_outgoing_drafts SET status=?,error=?,finished_at=? WHERE id=? AND status='submitting'",
                     ('submitted' if ok else 'unknown', str(error)[:300], int(time.time()), draft_id))


def summary(offer_id):
    with connect() as conn:
        return [(r['status'], r['n']) for r in conn.execute("SELECT status,COUNT(*) AS n FROM deal_outgoing_drafts WHERE offer_id=? GROUP BY status", (offer_id,))]


def receipt_display(draft_id, uid, **updates):
    """UI-only metadata; never changes the accounting payload/revision."""
    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS outgoing_receipt_display (draft_id INTEGER, uid INTEGER, data TEXT NOT NULL, PRIMARY KEY(draft_id,uid))')
        row = conn.execute('SELECT data FROM outgoing_receipt_display WHERE draft_id=? AND uid=?', (draft_id, uid)).fetchone()
        data = json.loads(row[0]) if row else {}
        if updates:
            data.update(updates)
            conn.execute('INSERT OR REPLACE INTO outgoing_receipt_display VALUES (?,?,?)', (draft_id, uid, json.dumps(data)))
        return data
