#!/usr/bin/env python3
"""Inspect offer/gate state on server or local."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path.cwd()
if not (ROOT / "eurobot.db").exists():
    ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "eurobot.db"
oid = int(sys.argv[1]) if len(sys.argv) > 1 else 93

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

gate = cur.execute(
    "SELECT * FROM offer_deal_gates WHERE offer_id = ?", (oid,)
).fetchone()
print("=== offer_deal_gates ===")
print(dict(gate) if gate else "none")

row = cur.execute(
    """
    SELECT o.*, a.rowid AS advert_rowid
    FROM advert_offers o
    JOIN euro_adverts a ON a.rowid = o.advert_rowid
    WHERE o.id = ?
    """,
    (oid,),
).fetchone()
if not row:
    row = cur.execute("SELECT * FROM advert_offers WHERE id = ?", (oid,)).fetchone()
print("\n=== advert_offers ===")
print(dict(row) if row else "none")

lines = cur.execute(
    """
    SELECT from_role, body, created_at FROM offer_negotiation_lines
    WHERE offer_id = ? ORDER BY id DESC LIMIT 30
    """,
    (oid,),
).fetchall()
print("\n=== transcript (last 30) ===")
for ln in reversed(lines):
    body = (ln["body"] or "")[:120]
    print(f"  [{ln['from_role']}] {body}")
