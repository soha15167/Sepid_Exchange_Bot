#!/usr/bin/env python3
"""Quick server/local sanity check for deal gates and schema."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "eurobot.db"
if not DB.exists():
    DB = ROOT / "database" / "bot.db"

if not DB.exists():
    print("DB not found:", DB)
    sys.exit(1)

conn = sqlite3.connect(DB)
cur = conn.cursor()
cols = [r[1] for r in cur.execute("PRAGMA table_info(offer_deal_gates)").fetchall()]
print("admin_notify_mids column:", "admin_notify_mids" in cols)
rows = cur.execute(
    """
    SELECT offer_id, gate_status, buyer_response, seller_response,
           length(coalesce(buyer_accounts_text,'')),
           length(coalesce(seller_accounts_text,''))
    FROM offer_deal_gates
    WHERE gate_status IN ('pending', 'accounts')
    ORDER BY started_at DESC
    """
).fetchall()
print("active_gates:", rows if rows else "none")
