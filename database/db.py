"""
database/db.py — SQLite layer / لایهٔ دیتابیس

EN:
  Schema creation & light migrations (`ensure_schema`), users, euro_adverts,
  advert_offers, negotiation lines. Channel ad number = table `rowid`.

FA:
  ساخت جدول‌ها، migration ستون‌های جدید، CRUD کاربر/آگهی/پیشنهاد.
  «شماره آگهی» در کانال = `rowid` جدول euro_adverts.
"""

import logging
import sqlite3
import time
from config.settings import DB_PATH, LIST_RECENT_LIMIT
from contextlib import contextmanager

_logger = logging.getLogger(__name__)

# --- Schema helpers / کمک‌تابع‌های ساختار ---
def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}  # column name is index 1


def ensure_schema() -> None:
    """
    EN: Create tables if missing; ALTER TABLE for new columns; set ad ID sequence.
    FA: ساخت جدول‌ها؛ افزودن ستون‌های جدید؛ تنظیم شمارندهٔ آگهی (ADVERT_ID_START).
    """
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()

        # Ensure users table exists (minimal columns). If project already created it,
        # this is a no-op because of IF NOT EXISTS.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT,
                last_name TEXT,
                email TEXT,
                address TEXT,
                phone_number TEXT
            )
            """
        )

        cols = _table_columns(conn, "users")
        if "display_name" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        if "username" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN username TEXT")
        if "is_restricted" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN is_restricted INTEGER DEFAULT 0")
        if "restricted_until" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN restricted_until INTEGER")
        cols = _table_columns(conn, "users")
        if "created_at" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
            cur.execute(
                "UPDATE users SET created_at = datetime('now') "
                "WHERE created_at IS NULL OR TRIM(COALESCE(created_at, '')) = ''"
            )

        # Unique index for display_name (case-insensitive), allows NULLs.
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_display_name_unique ON users (LOWER(display_name))"
        )

        # --- Remove UNIQUE constraint on phone_number if it exists (admin must allow duplicates) ---
        def _has_unique_on_phone() -> bool:
            try:
                idx_rows = cur.execute("PRAGMA index_list(users)").fetchall()
                for r in idx_rows:
                    # r: (seq, name, unique, origin, partial) in newer sqlite
                    name = r[1]
                    unique = r[2]
                    if not unique:
                        continue
                    cols_rows = cur.execute(f"PRAGMA index_info({name})").fetchall()
                    indexed_cols = [c[2] for c in cols_rows]  # column name
                    if "phone_number" in indexed_cols:
                        return True
                # Also check table SQL for inline UNIQUE constraint
                sql_row = cur.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
                ).fetchone()
                if sql_row and sql_row[0]:
                    sql = sql_row[0].lower()
                    if "phone_number" in sql and "unique" in sql:
                        return True
            except Exception:
                return False
            return False

        if _has_unique_on_phone():
            old_cols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
            # Create new table without unique on phone_number
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users_new (
                    telegram_id INTEGER PRIMARY KEY,
                    full_name TEXT,
                    last_name TEXT,
                    email TEXT,
                    address TEXT,
                    phone_number TEXT,
                    display_name TEXT,
                    username TEXT
                )
                """
            )
            common = [c for c in ["telegram_id","full_name","last_name","email","address","phone_number","display_name","username"] if c in old_cols]
            cols_csv = ", ".join(common)
            cur.execute(f"INSERT INTO users_new ({cols_csv}) SELECT {cols_csv} FROM users")
            cur.execute("DROP TABLE users")
            cur.execute("ALTER TABLE users_new RENAME TO users")
            # Recreate display_name unique index
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_display_name_unique ON users (LOWER(display_name))")

        # کاربران تازه‌ثبت‌نام باید یک‌بار قوانین کانال را باز کنند؛ کاربران قبلی همگی ۱.
        cols = _table_columns(conn, "users")
        if "channel_rules_ack" not in cols:
            cur.execute(
                "ALTER TABLE users ADD COLUMN channel_rules_ack INTEGER NOT NULL DEFAULT 0"
            )
            cur.execute("UPDATE users SET channel_rules_ack = 1")

        conn.commit()

        # --- settings table (bot enabled/disabled) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_enabled', '1')")
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('channel_post_template_v', '2')"
        )

        _ensure_admin_audit_log_table(conn)

        # پیام‌های DM قابل حذف (بین ری‌استارت‌ها برای پاک‌سازی ثبت‌نام)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dm_trackable_messages (
                telegram_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, message_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dm_main_menu_anchors (
                telegram_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
            """
        )

        # --- پیشنهاد نرخ به آگهی (کانال) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS advert_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                advert_rowid INTEGER NOT NULL,
                proposer_telegram_id INTEGER NOT NULL,
                rate_toman INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_advert_offers_advert ON advert_offers (advert_rowid)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_advert_offers_user_advert ON advert_offers (proposer_telegram_id, advert_rowid)"
        )
        try:
            ao_cols = _table_columns(conn, "advert_offers")
            if "description" not in ao_cols:
                cur.execute("ALTER TABLE advert_offers ADD COLUMN description TEXT")
            if "status" not in ao_cols:
                cur.execute("ALTER TABLE advert_offers ADD COLUMN status TEXT DEFAULT 'pending'")
                cur.execute(
                    "UPDATE advert_offers SET status = 'pending' WHERE status IS NULL OR TRIM(COALESCE(status,'')) = ''"
                )
            if "seq_in_advert" not in ao_cols:
                cur.execute("ALTER TABLE advert_offers ADD COLUMN seq_in_advert INTEGER")
                cur.execute("SELECT DISTINCT advert_rowid FROM advert_offers")
                aid_rows = cur.fetchall()
                for (adv_id,) in aid_rows:
                    cur.execute(
                        """
                        SELECT id FROM advert_offers
                        WHERE advert_rowid = ? ORDER BY id ASC
                        """,
                        (adv_id,),
                    )
                    for i, (rid,) in enumerate(cur.fetchall(), start=1):
                        cur.execute(
                            "UPDATE advert_offers SET seq_in_advert = ? WHERE id = ?",
                            (i, rid),
                        )
            ao_cols = _table_columns(conn, "advert_offers")
            if "offer_alias_name" not in ao_cols:
                cur.execute("ALTER TABLE advert_offers ADD COLUMN offer_alias_name TEXT")
            ao_cols = _table_columns(conn, "advert_offers")
            if "proposer_account_country" not in ao_cols:
                cur.execute(
                    "ALTER TABLE advert_offers ADD COLUMN proposer_account_country TEXT"
                )
            ao_cols = _table_columns(conn, "advert_offers")
            if "proposed_euro_amount" not in ao_cols:
                cur.execute(
                    "ALTER TABLE advert_offers ADD COLUMN proposed_euro_amount INTEGER"
                )
        except Exception:
            pass

        # --- euro_adverts (آگهی‌های کانال) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS euro_adverts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                full_name TEXT,
                euro_amount INTEGER,
                rate_toman INTEGER,
                description TEXT,
                methods TEXT,
                operation TEXT,
                status TEXT DEFAULT 'فعال',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                city_ir TEXT,
                city_int TEXT,
                channel_chat_id TEXT,
                channel_message_id INTEGER,
                account_country TEXT,
                instant_transfer TEXT,
                euro_exchange INTEGER DEFAULT 0,
                fee_override_eur REAL
            )
            """
        )

        # ستون‌های قدیمی‌تر روی دیتابیس‌های موجود
        try:
            adv_cols = _table_columns(conn, "euro_adverts")
            if "channel_chat_id" not in adv_cols:
                cur.execute("ALTER TABLE euro_adverts ADD COLUMN channel_chat_id TEXT")
            if "channel_message_id" not in adv_cols:
                cur.execute("ALTER TABLE euro_adverts ADD COLUMN channel_message_id INTEGER")
            if "account_country" not in adv_cols:
                cur.execute("ALTER TABLE euro_adverts ADD COLUMN account_country TEXT")
            if "instant_transfer" not in adv_cols:
                cur.execute("ALTER TABLE euro_adverts ADD COLUMN instant_transfer TEXT")
            if "euro_exchange" not in adv_cols:
                cur.execute("ALTER TABLE euro_adverts ADD COLUMN euro_exchange INTEGER DEFAULT 0")
            if "fee_override_eur" not in adv_cols:
                cur.execute("ALTER TABLE euro_adverts ADD COLUMN fee_override_eur REAL")
        except Exception:
            pass

        ensure_advert_rowid_sequence(conn)

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS offer_negotiation_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER NOT NULL,
                from_role TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_offer_neg_lines_offer ON offer_negotiation_lines(offer_id)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS offer_bot_outbound_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER NOT NULL,
                recipient_telegram_id INTEGER NOT NULL,
                party TEXT NOT NULL,
                tag TEXT NOT NULL,
                msg_type TEXT NOT NULL DEFAULT 'text',
                body_html TEXT,
                caption_html TEXT,
                photo_file_id TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_outbound_offer ON offer_bot_outbound_log(offer_id)"
        )

        # --- offer_deal_gates | Deal gate after offer acceptance ---
        # EN: gate_status, receipt JSON columns, admin_notify_mids — see docs/DEAL_GATE.md
        # FA: وضعیت gate، لاگ فیش‌ها، شناسه پیام ادمین — ر.ک. docs/DEAL_GATE.md
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS offer_deal_gates (
                offer_id INTEGER PRIMARY KEY,
                advert_rowid INTEGER NOT NULL,
                buyer_telegram_id INTEGER NOT NULL,
                seller_telegram_id INTEGER NOT NULL,
                gate_status TEXT NOT NULL DEFAULT 'pending',
                buyer_response TEXT,
                seller_response TEXT,
                buyer_confirmed_at INTEGER,
                seller_confirmed_at INTEGER,
                started_at INTEGER NOT NULL,
                reminder_count INTEGER NOT NULL DEFAULT 0,
                admin_escalated_at INTEGER,
                admin_decision TEXT,
                buyer_gate_mid INTEGER,
                seller_gate_mid INTEGER,
                buyer_accounts_text TEXT,
                seller_accounts_text TEXT,
                completed_at INTEGER,
                admin_notify_mids TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_offer_deal_gates_advert ON offer_deal_gates(advert_rowid)"
        )
        try:
            cur.execute(
                "ALTER TABLE offer_deal_gates ADD COLUMN admin_notify_mids TEXT"
            )
        except Exception:
            pass
        for col in (
            "buyer_accounts_photo_file_id",
            "seller_accounts_photo_file_id",
            "admin_notify_photo_mids",
            "buyer_receipt_log",
            "buyer_toman_card_sent_at",
            "seller_receipt_log",
            "seller_eur_account_sent_at",
            "buyer_toman_settled_at",
            "seller_toman_admin_log",
            "seller_toman_settled_at",
            "seller_toman_close_enabled_at",
            "admin_escalation_mids",
        ):
            try:
                cur.execute(
                    f"ALTER TABLE offer_deal_gates ADD COLUMN {col} TEXT"
                )
            except Exception:
                pass

        conn.commit()


def ensure_advert_rowid_sequence(conn: sqlite3.Connection | None = None) -> None:
    """
    اولین آگهیٔ جدید شمارهٔ ADVERT_ID_START (پیش‌فرض ۳۱۹۶) می‌گیرد.
    اگر آگهی با rowid بزرگ‌تر وجود داشته باشد، شمارنده عقب‌تر نمی‌رود.
    """
    from config.settings import ADVERT_ID_START

    start = int(ADVERT_ID_START or 0)
    if start < 1:
        return
    seq_val = start - 1
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        try:
            row = cur.execute("SELECT MAX(rowid) FROM euro_adverts").fetchone()
            current_max = int(row[0] or 0)
        except sqlite3.Error:
            current_max = 0
        if current_max >= start:
            return
        target = max(seq_val, current_max)
        if current_max == 0:
            cur.execute(
                """
                INSERT INTO euro_adverts (
                    user_id, full_name, euro_amount, rate_toman, description, methods, operation
                ) VALUES (0, '', 0, 0, '', '', '-')
                """
            )
            seed_rid = int(cur.lastrowid or 0)
            if seed_rid:
                cur.execute("DELETE FROM euro_adverts WHERE rowid = ?", (seed_rid,))
        if cur.execute(
            "SELECT 1 FROM sqlite_sequence WHERE name = 'euro_adverts'"
        ).fetchone():
            cur.execute(
                "UPDATE sqlite_sequence SET seq = ? WHERE name = 'euro_adverts'",
                (target,),
            )
        else:
            cur.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES ('euro_adverts', ?)",
                (target,),
            )
        if own_conn:
            conn.commit()
    finally:
        if own_conn and conn:
            conn.close()


def negotiation_transcript_list(offer_id: int) -> list[dict]:
    """همهٔ خطوط مذاکرهٔ یک پیشنهاد (قدیمی‌ترین اول)، شکل: {\"from\", \"text\"}."""
    try:
        oid = int(offer_id)
    except (TypeError, ValueError):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT from_role, body
            FROM offer_negotiation_lines
            WHERE offer_id = ?
            ORDER BY id ASC
            """,
            (oid,),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        fr = str(r["from_role"] or "").strip().lower()
        if fr not in ("owner", "proposer", "system", "admin", "buyer", "seller"):
            fr = "other"
        out.append({"from": fr, "text": r["body"]})
    return out


def negotiation_transcript_append_line(
    offer_id: int, from_role: str, text: str, *, max_lines: int | None = None
) -> list[dict]:
    """یک خط به آرشیو پیشنهاد/مذاکره/معامله اضافه می‌کند (پیش‌فرض: بدون حذف خطوط قدیمی)."""
    try:
        oid = int(offer_id)
    except (TypeError, ValueError):
        return []
    fr = (from_role or "").strip().lower()
    allowed = ("owner", "proposer", "system", "admin", "buyer", "seller")
    if fr not in allowed:
        fr = "system"
    t = (text or "").strip()[:4000]
    if not t:
        return negotiation_transcript_list(oid)
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO offer_negotiation_lines (offer_id, from_role, body, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (oid, fr, t, now),
        )
        if max_lines is not None and int(max_lines) > 0:
            mx = max(1, min(int(max_lines), 500))
            cur.execute(
                "SELECT COUNT(*) FROM offer_negotiation_lines WHERE offer_id = ?",
                (oid,),
            )
            n = int(cur.fetchone()[0] or 0)
            if n > mx:
                excess = n - mx
                cur.execute(
                    """
                    DELETE FROM offer_negotiation_lines WHERE id IN (
                        SELECT id FROM offer_negotiation_lines WHERE offer_id = ? ORDER BY id ASC LIMIT ?
                    )
                    """,
                    (oid, excess),
                )
        conn.commit()
    return negotiation_transcript_list(oid)


def bot_outbound_log_insert(
    offer_id: int,
    recipient_telegram_id: int,
    party: str,
    tag: str,
    *,
    msg_type: str = "text",
    body_html: str = "",
    caption_html: str = "",
    photo_file_id: str = "",
) -> None:
    """ذخیرهٔ کپی پیام ارسالی ربات به کاربر (برای نمایش ادمین)."""
    try:
        oid = int(offer_id)
        rid = int(recipient_telegram_id)
    except (TypeError, ValueError):
        return
    pr = (party or "").strip().lower()
    if pr not in ("buyer", "seller", "user"):
        pr = "user"
    mt = (msg_type or "text").strip().lower()
    if mt not in ("text", "photo"):
        mt = "text"
    tg = (tag or "پیام").strip()[:120]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO offer_bot_outbound_log (
                offer_id, recipient_telegram_id, party, tag, msg_type,
                body_html, caption_html, photo_file_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                oid,
                rid,
                pr,
                tg,
                mt,
                (body_html or "")[:12000],
                (caption_html or "")[:4000],
                (photo_file_id or "").strip()[:256],
                int(time.time()),
            ),
        )
        conn.commit()


def bot_outbound_log_list(offer_id: int) -> list[dict]:
    try:
        oid = int(offer_id)
    except (TypeError, ValueError):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, recipient_telegram_id, party, tag, msg_type,
                   body_html, caption_html, photo_file_id, created_at
            FROM offer_bot_outbound_log
            WHERE offer_id = ?
            ORDER BY id ASC
            """,
            (oid,),
        ).fetchall()
    return [dict(r) for r in rows]


def display_name_exists(display_name: str) -> bool:
    if not display_name:
        return False
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT 1 FROM users WHERE LOWER(display_name) = LOWER(?) LIMIT 1",
            (display_name.strip(),),
        ).fetchone()
        return row is not None


def delete_user(telegram_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
        deleted = cur.rowcount > 0
        if deleted:
            cur.execute(
                "DELETE FROM dm_trackable_messages WHERE telegram_id = ?",
                (telegram_id,),
            )
            cur.execute(
                "DELETE FROM dm_main_menu_anchors WHERE telegram_id = ?",
                (telegram_id,),
            )
            conn.commit()
        return deleted


def record_dm_trackable_message(telegram_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        tid, mid = int(telegram_id), int(message_id)
    except (TypeError, ValueError):
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO dm_trackable_messages (telegram_id, message_id) VALUES (?, ?)",
            (tid, mid),
        )
        conn.commit()


def fetch_dm_trackable_messages(telegram_id: int) -> list[int]:
    try:
        tid = int(telegram_id)
    except (TypeError, ValueError):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT message_id FROM dm_trackable_messages WHERE telegram_id = ?",
            (tid,),
        ).fetchall()
    return [int(r[0]) for r in rows]


def clear_dm_trackable_messages(telegram_id: int) -> None:
    try:
        tid = int(telegram_id)
    except (TypeError, ValueError):
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM dm_trackable_messages WHERE telegram_id = ?",
            (tid,),
        )
        conn.commit()


def save_main_menu_anchor(telegram_id: int, chat_id: int, message_id: int) -> None:
    try:
        tid, cid, mid = int(telegram_id), int(chat_id), int(message_id)
    except (TypeError, ValueError):
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO dm_main_menu_anchors (telegram_id, chat_id, message_id)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                message_id = excluded.message_id
            """,
            (tid, cid, mid),
        )
        conn.commit()


def fetch_main_menu_anchor(telegram_id: int) -> tuple[int, int] | None:
    """(chat_id, message_id) or None."""
    try:
        tid = int(telegram_id)
    except (TypeError, ValueError):
        return None
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT chat_id, message_id FROM dm_main_menu_anchors WHERE telegram_id = ?",
            (tid,),
        ).fetchone()
    if not row:
        return None
    return int(row[0]), int(row[1])


def clear_main_menu_anchor(telegram_id: int) -> None:
    try:
        tid = int(telegram_id)
    except (TypeError, ValueError):
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM dm_main_menu_anchors WHERE telegram_id = ?", (tid,))
        conn.commit()


def update_user_field(telegram_id: int, field: str, value: str | None) -> bool:
    allowed = {"full_name", "last_name", "display_name", "username", "email", "address", "phone_number", "is_restricted"}
    if field not in allowed:
        return False
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET {field} = ? WHERE telegram_id = ?", (value, telegram_id))
        return cur.rowcount > 0


def is_user_restricted(telegram_id: int) -> bool:
    """اگر زمان restricted_until گذشته باشد، خودکار محدودیت برداشته می‌شود."""
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT COALESCE(is_restricted,0), restricted_until FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if not row:
            return False
        is_r, until = int(row[0] or 0), row[1]
        if is_r != 1:
            return False
        if until is not None and int(until) > 0 and now >= int(until):
            cur.execute(
                "UPDATE users SET is_restricted = 0, restricted_until = NULL WHERE telegram_id = ?",
                (telegram_id,),
            )
            conn.commit()
            return False
        return True


def get_restriction_block_message(telegram_id: int) -> str | None:
    """
    اگر کاربر هنوز محدود است، متن اخطار (با ذکر مدت باقی‌مانده برای محدودیت موقت).
    در صورت انقضا، همانند is_user_restricted رکورد را آزاد می‌کند و None برمی‌گرداند.
    """
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT COALESCE(is_restricted,0), restricted_until FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if not row:
            return None
        is_r, until_raw = int(row[0] or 0), row[1]
        if is_r != 1:
            return None
        until_ts: int | None
        if until_raw is None:
            until_ts = None
        else:
            try:
                until_ts = int(until_raw)
            except (TypeError, ValueError):
                until_ts = None
        if until_ts is not None and until_ts > 0 and now >= until_ts:
            cur.execute(
                "UPDATE users SET is_restricted = 0, restricted_until = NULL WHERE telegram_id = ?",
                (telegram_id,),
            )
            conn.commit()
            return None
        if until_ts is None or until_ts <= 0:
            return "⛔️ دسترسی شما توسط مدیر به‌صورت دائمی محدود شده است."
        left = until_ts - now
        if left <= 0:
            cur.execute(
                "UPDATE users SET is_restricted = 0, restricted_until = NULL WHERE telegram_id = ?",
                (telegram_id,),
            )
            conn.commit()
            return None
        days, rem = divmod(left, 86400)
        hours, rem2 = divmod(rem, 3600)
        minutes = rem2 // 60
        lines = ["⛔️ دسترسی شما توسط مدیر محدود شده است."]
        if days >= 1:
            lines.append(f"📅 تقریباً {days} روز تا رفع خودکار محدودیت باقی مانده است.")
        elif hours >= 1:
            lines.append(f"⏳ تقریباً {hours} ساعت تا رفع خودکار محدودیت باقی مانده است.")
        elif minutes >= 1:
            lines.append(f"⏳ تقریباً {minutes} دقیقه تا رفع خودکار محدودیت باقی مانده است.")
        else:
            lines.append("⏳ محدودیت شما به‌زودی به‌طور خودکار برداشته می‌شود.")
        return "\n".join(lines)


def set_user_restricted(telegram_id: int, restricted: bool) -> bool:
    """محدودیت دائمی (بدون تاریخ انقضا)."""
    return set_user_restriction(telegram_id, restricted, until_ts=None)


def set_user_restriction(telegram_id: int, restricted: bool, until_ts: int | None = None) -> bool:
    """
    restricted=True و until_ts=None → دائمی.
    restricted=True و until_ts=unix → تا آن زمان محدود، بعد خودکار آزاد.
    restricted=False → پاک کردن محدودیت.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if not restricted:
            cur.execute(
                "UPDATE users SET is_restricted = 0, restricted_until = NULL WHERE telegram_id = ?",
                (telegram_id,),
            )
        else:
            cur.execute(
                "UPDATE users SET is_restricted = 1, restricted_until = ? WHERE telegram_id = ?",
                (until_ts, telegram_id),
            )
        return cur.rowcount > 0


def get_setting(key: str, default: str | None = None) -> str | None:
    if not key:
        return default
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        row = cur.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row and row[0] is not None else default


def set_setting(key: str, value: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()


def is_bot_enabled() -> bool:
    """False وقتی ادمین ربات را از پنل غیرفعال کرده باشد."""
    raw = (get_setting("bot_enabled", "1") or "1").strip().lower()
    return raw in ("1", "true", "yes", "on", "enabled")


def _ensure_admin_audit_log_table(conn: sqlite3.Connection | None = None) -> None:
    """جدول لاگ ادمین — اگر سرور بدون ensure_schema قدیمی باشد، اینجا ساخته می‌شود."""
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_telegram_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_audit_created "
            "ON admin_audit_log (created_at DESC)"
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def log_admin_action(
    admin_telegram_id: int, action: str, detail: str | None = None
) -> None:
    try:
        aid = int(admin_telegram_id)
    except (TypeError, ValueError):
        aid = 0
    act = (action or "").strip()[:120]
    if not act:
        return
    det = (detail or "").strip()[:2000] or None
    try:
        _ensure_admin_audit_log_table()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO admin_audit_log (admin_telegram_id, action, detail)
                VALUES (?, ?, ?)
                """,
                (aid, act, det),
            )
            conn.commit()
    except sqlite3.Error as exc:
        _logger.warning("log_admin_action skipped: %s", exc)


def count_users() -> int:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    return int(row[0] or 0) if row else 0


def list_users_page(*, limit: int, offset: int) -> list[tuple]:
    lim = max(1, min(int(limit), 50))
    off = max(0, int(offset))
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """
            SELECT telegram_id, username, display_name, full_name, last_name,
                   phone_number, email, address
            FROM users
            ORDER BY rowid DESC
            LIMIT ? OFFSET ?
            """,
            (lim, off),
        ).fetchall()


def count_euro_adverts() -> int:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT COUNT(*) FROM euro_adverts").fetchone()
    return int(row[0] or 0) if row else 0


def list_euro_adverts_page(*, limit: int, offset: int) -> list[tuple]:
    lim = max(1, min(int(limit), 50))
    off = max(0, int(offset))
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """
            SELECT
                a.rowid,
                COALESCE(u.display_name, a.full_name) AS adv_name,
                u.username,
                a.euro_amount,
                a.rate_toman,
                a.operation
            FROM euro_adverts a
            LEFT JOIN users u ON u.telegram_id = a.user_id
            ORDER BY a.rowid DESC
            LIMIT ? OFFSET ?
            """,
            (lim, off),
        ).fetchall()


def list_recent_channel_advert_rowids(limit: int = 25) -> list[int]:
    lim = max(1, min(int(limit), 80))
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT rowid FROM euro_adverts
            WHERE channel_message_id IS NOT NULL
              AND channel_chat_id IS NOT NULL
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    return [int(r[0]) for r in rows]


def daily_stats_since_hours(hours: int = 24) -> dict:
    """Counts for admin daily report (SQLite datetime)."""
    h = max(1, min(int(hours), 168))
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        users_n = int(
            cur.execute(
                f"""
                SELECT COUNT(*) FROM users
                WHERE datetime(created_at) >= datetime('now', '-{h} hours')
                """
            ).fetchone()[0]
            or 0
        )
        adverts_n = int(
            cur.execute(
                f"""
                SELECT COUNT(*) FROM euro_adverts
                WHERE datetime(created_at) >= datetime('now', '-{h} hours')
                """
            ).fetchone()[0]
            or 0
        )
        offers_n = int(
            cur.execute(
                f"""
                SELECT COUNT(*) FROM advert_offers
                WHERE datetime(created_at) >= datetime('now', '-{h} hours')
                """
            ).fetchone()[0]
            or 0
        )
        accepted_n = int(
            cur.execute(
                f"""
                SELECT COUNT(*) FROM advert_offers
                WHERE lower(trim(coalesce(status,''))) = 'accepted'
                  AND datetime(created_at) >= datetime('now', '-{h} hours')
                """
            ).fetchone()[0]
            or 0
        )
        total_users = int(cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] or 0)
    return {
        "hours": h,
        "new_users": users_n,
        "new_adverts": adverts_n,
        "new_offers": offers_n,
        "accepted_offers": accepted_n,
        "total_users": total_users,
    }


def search_users(query: str, limit: int = 10):
    q = (query or "").strip()
    if not q:
        return []
    # normalize common admin inputs: "@username" or "t.me/username"
    q = q.strip()
    if q.lower().startswith("https://t.me/"):
        q = q.split("/")[-1].strip()
    elif q.lower().startswith("t.me/"):
        q = q.split("/")[-1].strip()
    q = q.lstrip("@").strip()
    # Normalize digits for phone searches (supports Persian/Arabic digits).
    _digit_map = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    )
    q_norm = q.translate(_digit_map).strip()
    q_lower = q_norm.lower()
    like = f"%{q_lower}%"
    q_digits = "".join(ch for ch in q_norm if ch.isdigit())
    like_digits = f"%{q_digits}%"
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        return cur.execute(
            """
            SELECT telegram_id, username, display_name, full_name, last_name, phone_number, email, address
            FROM users
            WHERE
                CAST(telegram_id AS TEXT) LIKE ?
                OR lower(COALESCE(username,'')) LIKE ?
                OR lower(COALESCE(display_name,'')) LIKE ?
                OR lower(COALESCE(full_name,'')) LIKE ?
                OR lower(COALESCE(last_name,'')) LIKE ?
                OR replace(replace(replace(replace(COALESCE(phone_number,''), ' ', ''), '-', ''), '+', ''), '۰', '0') LIKE ?
                OR lower(COALESCE(email,'')) LIKE ?
                OR lower(COALESCE(address,'')) LIKE ?
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (
                f"%{q_digits or q_norm}%",
                like,
                like,
                like,
                like,
                (like_digits if q_digits else like),
                like,
                like,
                int(limit),
            ),
        ).fetchall()


def get_all_registered_telegram_ids() -> list[int]:
    """همهٔ کاربران ثبت‌نام‌شده (برای اعلان/منو قبل از ری‌استارت و غیره)."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        rows = cur.execute("SELECT telegram_id FROM users ORDER BY telegram_id").fetchall()
    return [int(r[0]) for r in rows]


# 📥 گرفتن اطلاعات کامل کاربر بر اساس telegram_id به صورت دیکشنری
def get_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def set_user_channel_rules_acknowledged(telegram_user_id: int) -> None:
    """پس از باز کردن صفحهٔ «قوانین و روال کار کانال» برای فعال شدن دکمهٔ درخواست خدمات."""
    try:
        tid = int(telegram_user_id)
    except (TypeError, ValueError):
        return
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET channel_rules_ack = 1 WHERE telegram_id = ?",
            (tid,),
        )
        conn.commit()


def get_euro_advert_by_rowid(rowid: int) -> dict | None:
    """یک ردیف euro_adverts بر اساس rowid آگهی (همان شمارهٔ نمایش داده‌شده در کانال)."""
    try:
        rid = int(rowid)
    except (TypeError, ValueError):
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM euro_adverts WHERE rowid = ?", (rid,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["rowid"] = rid
            return d
    except sqlite3.Error:
        return None


def user_advert_has_active_offers(advert_rowid: int) -> bool:
    """پیشنهاد در انتظار یا پذیرفته — مدیریت آگهی توسط کاربر غیرمجاز."""
    try:
        aid = int(advert_rowid)
    except (TypeError, ValueError):
        return True
    if list_pending_offers_for_advert(aid):
        return True
    if list_accepted_offers_for_advert(aid):
        return True
    return False


def count_euro_adverts_owned_by_user(telegram_user_id: int) -> int:
    try:
        uid = int(telegram_user_id)
    except (TypeError, ValueError):
        return 0
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM euro_adverts WHERE user_id = ?", (uid,)
        ).fetchone()
    return int(row[0] or 0) if row else 0


def list_euro_adverts_owned_by_user(
    telegram_user_id: int,
    limit: int = LIST_RECENT_LIMIT,
    offset: int = 0,
) -> list[dict]:
    """آخرین آگهی‌های ثبت‌شده با این telegram_id."""
    try:
        uid = int(telegram_user_id)
    except (TypeError, ValueError):
        return []
    lim = max(1, min(int(limit), 50))
    off = max(0, int(offset))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT rowid AS advert_rowid, operation, euro_amount, rate_toman, description, user_id,
                   COALESCE(euro_exchange, 0) AS euro_exchange
            FROM euro_adverts
            WHERE user_id = ?
            ORDER BY advert_rowid DESC
            LIMIT ? OFFSET ?
            """,
            (uid, lim, off),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        # نام مستعار؛ بعضی دیتابیس‌ها/ستون‌های هم‌نام باعث می‌شوند r["rowid"] روی Row خطا بدهد.
        rid = int(r["advert_rowid"])
        d = dict(r)
        d.pop("advert_rowid", None)
        d["rowid"] = rid
        out.append(d)
    return out


def list_manageable_euro_adverts_for_user(
    telegram_user_id: int, limit: int = LIST_RECENT_LIMIT
) -> list[dict]:
    """آگهی‌های یورو/معاوضهٔ کاربر بدون پیشنهاد فعال (pending یا accepted)."""
    owned = list_euro_adverts_owned_by_user(telegram_user_id, limit=LIST_RECENT_LIMIT * 5)
    out: list[dict] = []
    for d in owned:
        rid = int(d["rowid"])
        if user_advert_has_active_offers(rid):
            continue
        out.append(d)
        if len(out) >= max(1, min(int(limit), 50)):
            break
    return out


def delete_euro_advert_for_owner(
    rowid: int,
    owner_telegram_id: int,
    *,
    skip_active_offer_guard: bool = False,
) -> tuple[bool, int | None, int | None]:
    """
    حذف آگهی توسط صاحب. معمولاً فقط بدون پیشنهاد pending/accepted؛
    با skip_active_offer_guard=True (فقط پس از تأیید ادمین بودن در هندلر) حذف با پیشنهاد فعال هم ممکن است.
    برمی‌گرداند (موفقیت، channel_message_id، channel_chat_id) برای حذف پیام کانال.
    """
    try:
        rid = int(rowid)
        uid = int(owner_telegram_id)
    except (TypeError, ValueError):
        return False, None, None
    adv = get_euro_advert_by_rowid(rid)
    if not adv or int(adv.get("user_id") or 0) != uid:
        return False, None, None
    if not skip_active_offer_guard and user_advert_has_active_offers(rid):
        return False, None, None
    ch_mid = adv.get("channel_message_id")
    ch_cid = adv.get("channel_chat_id")
    try:
        mid_i = int(ch_mid) if ch_mid is not None else None
    except (TypeError, ValueError):
        mid_i = None
    try:
        cid_i = int(ch_cid) if ch_cid is not None else None
    except (TypeError, ValueError):
        cid_i = None
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM offer_negotiation_lines
            WHERE offer_id IN (SELECT id FROM advert_offers WHERE advert_rowid = ?)
            """,
            (rid,),
        )
        cur.execute("DELETE FROM advert_offers WHERE advert_rowid = ?", (rid,))
        cur.execute(
            "DELETE FROM euro_adverts WHERE rowid = ? AND user_id = ?",
            (rid, uid),
        )
        ok = cur.rowcount > 0
        conn.commit()
    if not ok:
        return False, None, None
    return True, mid_i, cid_i


def update_euro_advert_field_for_owner(
    rowid: int,
    owner_telegram_id: int,
    field: str,
    value: str,
    *,
    skip_active_offer_guard: bool = False,
) -> bool:
    allowed = frozenset({"euro_amount", "rate_toman", "description"})
    if field not in allowed:
        return False
    try:
        rid = int(rowid)
        uid = int(owner_telegram_id)
    except (TypeError, ValueError):
        return False
    adv = get_euro_advert_by_rowid(rid)
    if not adv or int(adv.get("user_id") or 0) != uid:
        return False
    if not skip_active_offer_guard and user_advert_has_active_offers(rid):
        return False
    val = (value or "").strip()
    if field == "description":
        if len(val) < 2 or len(val) > 3500:
            return False
    elif field in ("euro_amount", "rate_toman"):
        try:
            n = int(val)
        except (TypeError, ValueError):
            return False
        if n <= 0:
            return False
        val = str(n)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        acols = _table_columns(conn, "euro_adverts")
        if field not in acols:
            return False
        cur.execute(
            f"UPDATE euro_adverts SET {field} = ? WHERE rowid = ? AND user_id = ?",
            (val, rid, uid),
        )
        conn.commit()
        return cur.rowcount > 0


def list_my_advert_offers(
    advert_rowid: int,
    proposer_telegram_id: int,
    limit: int = LIST_RECENT_LIMIT,
) -> list[tuple]:
    """آخرین پیشنهادهای همین کاربر روی یک آگهی (قدیمی‌ترین اول در خروجی)."""
    try:
        aid = int(advert_rowid)
        uid = int(proposer_telegram_id)
        lim = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cols = _table_columns(conn, "advert_offers")
        pe_sel = (
            ", COALESCE(proposed_euro_amount, 0)"
            if "proposed_euro_amount" in cols
            else ", 0"
        )
        if "description" in cols and "status" in cols and "seq_in_advert" in cols:
            rows = cur.execute(
                f"""
                SELECT id, rate_toman, created_at, description,
                       COALESCE(NULLIF(TRIM(status), ''), 'pending') AS offer_status,
                       COALESCE(seq_in_advert, id) AS seq_in_advert
                       {pe_sel}
                FROM advert_offers
                WHERE advert_rowid = ? AND proposer_telegram_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (aid, uid, lim),
            ).fetchall()
            return list(reversed(rows))
        if "description" in cols and "status" in cols:
            rows = cur.execute(
                f"""
                SELECT id, rate_toman, created_at, description,
                       COALESCE(NULLIF(TRIM(status), ''), 'pending') AS offer_status,
                       0 AS seq_in_advert
                       {pe_sel}
                FROM advert_offers
                WHERE advert_rowid = ? AND proposer_telegram_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (aid, uid, lim),
            ).fetchall()
            return list(reversed(rows))
        if "description" in cols:
            rows = cur.execute(
                """
                SELECT id, rate_toman, created_at, description FROM advert_offers
                WHERE advert_rowid = ? AND proposer_telegram_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (aid, uid, lim),
            ).fetchall()
            return list(reversed(rows))
        rows = cur.execute(
            """
            SELECT id, rate_toman, created_at FROM advert_offers
            WHERE advert_rowid = ? AND proposer_telegram_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (aid, uid, lim),
        ).fetchall()
        return list(reversed(rows))


def count_offers_for_advert(advert_rowid: int) -> int:
    try:
        aid = int(advert_rowid)
    except (TypeError, ValueError):
        return 0
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        try:
            row = cur.execute(
                "SELECT COUNT(*) FROM advert_offers WHERE advert_rowid = ?",
                (aid,),
            ).fetchone()
            return int(row[0] or 0)
        except Exception:
            return 0


def effective_offer_euro_amount_for_advert(
    advert_rowid: int, proposed_euro_amount: int | None = None
) -> int:
    """مقدار یوروی مؤثر پیشنهاد: جزئی (proposed) یا کل آگهی."""
    try:
        aid = int(advert_rowid)
    except (TypeError, ValueError):
        return 0
    pe = 0
    try:
        if proposed_euro_amount is not None:
            pe = int(proposed_euro_amount)
    except (TypeError, ValueError):
        pe = 0
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT euro_amount FROM euro_adverts WHERE rowid = ?",
            (aid,),
        ).fetchone()
    if not row:
        return pe if pe > 0 else 0
    try:
        adv_e = int(row[0] or 0)
    except (TypeError, ValueError):
        adv_e = 0
    return pe if pe > 0 else adv_e


def insert_advert_offer(
    advert_rowid: int,
    proposer_telegram_id: int,
    rate_toman: int,
    description: str | None = None,
    offer_alias_name: str | None = None,
    proposer_account_country: str | None = None,
    proposed_euro_amount: int | None = None,
    *,
    enforce_rejection_rules: bool = True,
) -> tuple[int, int] | None:
    """
    برمی‌گرداند (id ردیف در دیتابیس، شمارهٔ پیشنهاد داخل همان آگهی از ۱).
    """
    try:
        aid = int(advert_rowid)
        uid = int(proposer_telegram_id)
        rate = int(rate_toman)
    except (TypeError, ValueError):
        return None
    if rate < 0:
        return None
    eff_euro = effective_offer_euro_amount_for_advert(aid, proposed_euro_amount)
    if enforce_rejection_rules and rate > 0 and eff_euro > 0:
        adv_row = None
        with sqlite3.connect(DB_PATH) as conn:
            adv_row = conn.execute(
                "SELECT euro_amount, operation FROM euro_adverts WHERE rowid = ?",
                (aid,),
            ).fetchone()
        if adv_row:
            adv_e = int(adv_row[0] or 0)
            op = (adv_row[1] or "").strip()
            if (
                classify_proposer_rate_rejection(
                    aid,
                    uid,
                    rate,
                    target_euro_amount=eff_euro,
                    advert_total_euro=adv_e,
                    operation=op,
                )
                is not None
                or rejected_offer_same_rate_and_euro(aid, rate, eff_euro)
            ):
                return None
    from datetime import datetime

    created = datetime.now().isoformat(timespec="seconds")
    desc = (description or "").strip() or None
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cols = _table_columns(conn, "advert_offers")
        next_seq = 1
        if "seq_in_advert" in cols:
            row_mx = cur.execute(
                "SELECT COALESCE(MAX(seq_in_advert), 0) FROM advert_offers WHERE advert_rowid = ?",
                (aid,),
            ).fetchone()
            next_seq = int(row_mx[0] or 0) + 1
        fields = ["advert_rowid", "proposer_telegram_id", "rate_toman", "created_at"]
        values: list = [aid, uid, rate, created]
        if "description" in cols:
            fields.append("description")
            values.append(desc)
        if "status" in cols:
            fields.append("status")
            values.append("pending")
        if "seq_in_advert" in cols:
            fields.append("seq_in_advert")
            values.append(next_seq)
        if "offer_alias_name" in cols:
            fields.append("offer_alias_name")
            al = (offer_alias_name or "").strip() or None
            values.append(al)
        if "proposer_account_country" in cols:
            fields.append("proposer_account_country")
            pc = (proposer_account_country or "").strip() or None
            values.append(pc)
        if "proposed_euro_amount" in cols and proposed_euro_amount is not None:
            try:
                pe = int(proposed_euro_amount)
                if pe > 0:
                    fields.append("proposed_euro_amount")
                    values.append(pe)
            except (TypeError, ValueError):
                pass
        placeholders = ", ".join(["?"] * len(values))
        field_csv = ", ".join(fields)
        cur.execute(
            f"INSERT INTO advert_offers ({field_csv}) VALUES ({placeholders})",
            tuple(values),
        )
        conn.commit()
        new_id = int(cur.lastrowid)
        return (new_id, next_seq if "seq_in_advert" in cols else new_id)


def proposer_has_pending_offer_on_advert(advert_rowid: int, proposer_telegram_id: int) -> bool:
    """آیا این کاربر روی این آگهی پیشنهاد pending دارد؟ (برای معاوضهٔ یورو به یورو با نرخ ۰)."""
    try:
        aid = int(advert_rowid)
        uid = int(proposer_telegram_id)
    except (TypeError, ValueError):
        return False
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if "status" not in _table_columns(conn, "advert_offers"):
            return False
        row = cur.execute(
            """
            SELECT 1 FROM advert_offers
            WHERE advert_rowid = ? AND proposer_telegram_id = ?
              AND COALESCE(NULLIF(TRIM(status), ''), 'pending') = 'pending'
            LIMIT 1
            """,
            (aid, uid),
        ).fetchone()
        return row is not None


def proposer_offer_rate_exists(
    advert_rowid: int,
    proposer_telegram_id: int,
    rate_toman: int,
) -> bool:
    try:
        aid = int(advert_rowid)
        uid = int(proposer_telegram_id)
        rate = int(rate_toman)
    except (TypeError, ValueError):
        return False
    if rate < 0:
        return False
    if rate == 0:
        return proposer_has_pending_offer_on_advert(aid, uid)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        row = cur.execute(
            """
            SELECT 1 FROM advert_offers
            WHERE advert_rowid = ? AND proposer_telegram_id = ? AND rate_toman = ?
              AND LOWER(COALESCE(NULLIF(TRIM(status), ''), 'pending')) = 'pending'
            LIMIT 1
            """,
            (aid, uid, rate),
        ).fetchone()
        return row is not None


def proposer_offer_rate_exists_other_than(
    exclude_offer_id: int,
    advert_rowid: int,
    proposer_telegram_id: int,
    rate_toman: int,
) -> bool:
    """True if another offer (not exclude_offer_id) has the same advert/user/rate."""
    try:
        ex = int(exclude_offer_id)
        aid = int(advert_rowid)
        uid = int(proposer_telegram_id)
        rate = int(rate_toman)
    except (TypeError, ValueError):
        return False
    if rate < 0:
        return False
    if rate == 0:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            if "status" not in _table_columns(conn, "advert_offers"):
                return False
            row = cur.execute(
                """
                SELECT 1 FROM advert_offers
                WHERE advert_rowid = ? AND proposer_telegram_id = ?
                  AND COALESCE(NULLIF(TRIM(status), ''), 'pending') = 'pending'
                  AND id != ?
                LIMIT 1
                """,
                (aid, uid, ex),
            ).fetchone()
        return row is not None
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        row = cur.execute(
            """
            SELECT 1 FROM advert_offers
            WHERE advert_rowid = ? AND proposer_telegram_id = ? AND rate_toman = ?
              AND id != ?
            LIMIT 1
            """,
            (aid, uid, rate, ex),
        ).fetchone()
        return row is not None


def _pending_offer_status_sql(alias: str = "o") -> str:
    """شرط «هنوز باز» برای لیست پیشنهادها (بدون وابستگی سخت به فقط کلمهٔ pending)."""
    a = alias
    return (
        f"coalesce(lower(trim(cast({a}.status as text))), 'pending') "
        f"NOT IN ('accepted', 'rejected')"
    )


def list_my_pending_offers_all(
    proposer_telegram_id: int, limit: int = LIST_RECENT_LIMIT
) -> list[dict]:
    try:
        uid = int(proposer_telegram_id)
    except (TypeError, ValueError):
        return []
    lim = max(1, min(int(limit), 80))
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        acols = _table_columns(conn, "advert_offers")
        # بدون ستون status همهٔ ردیف‌ها «باز» فرض می‌شوند (اسکیمای قدیمی).
        st_wh = _pending_offer_status_sql("o") if "status" in acols else "1"
        seq_expr = "COALESCE(seq_in_advert, id)" if "seq_in_advert" in acols else "id"
        # از INNER JOIN استفاده نمی‌کنیم: اگر advert_rowid با id/rowid جدول قدیمی جور نباشد،
        # پیشنهاد ثبت‌شده نباید از لیست «پیشنهادهای من» ناپدید شود.
        rows = cur.execute(
            f"""
            SELECT o.id, o.advert_rowid, o.rate_toman, {seq_expr},
                   COALESCE(
                       (
                           SELECT ea.euro_exchange
                           FROM euro_adverts ea
                           WHERE ea.id = o.advert_rowid OR ea.rowid = o.advert_rowid
                           LIMIT 1
                       ),
                       0
                   ),
                   (
                       SELECT ea.operation
                       FROM euro_adverts ea
                       WHERE ea.id = o.advert_rowid OR ea.rowid = o.advert_rowid
                       LIMIT 1
                   )
            FROM advert_offers o
            WHERE o.proposer_telegram_id = ?
              AND ({st_wh})
            ORDER BY o.id DESC
            LIMIT ?
            """,
            (uid, lim),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        euro_ex = int(r[4] or 0)
        op = (r[5] or "").strip()
        legacy_ex = op == "معاوضه"
        hybrid = euro_ex == 1 and op in ("خرید", "فروش")
        skip_toman = hybrid or legacy_ex
        out.append(
            {
                "id": int(r[0]),
                "advert_rowid": int(r[1]),
                "rate_toman": int(r[2] or 0),
                "seq_in_advert": int(r[3] or 0),
                "skips_toman_rate_offer": skip_toman,
            }
        )
    out.reverse()
    return out


def list_incoming_pending_offers_for_advert_owner(
    owner_telegram_id: int, limit: int = LIST_RECENT_LIMIT
) -> list[dict]:
    """پیشنهادهای pending روی آگهی‌هایی که این کاربر صاحب آن است (نه پیشنهادهایی که خودش فرستاده)."""
    try:
        uid = int(owner_telegram_id)
    except (TypeError, ValueError):
        return []
    lim = max(1, min(int(limit), 80))
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        acols = _table_columns(conn, "advert_offers")
        st_wh = _pending_offer_status_sql("o") if "status" in acols else "1"
        seq_expr = "COALESCE(seq_in_advert, id)" if "seq_in_advert" in acols else "id"
        rows = cur.execute(
            f"""
            SELECT o.id, o.advert_rowid, o.rate_toman, {seq_expr},
                   COALESCE(
                       (
                           SELECT ea.euro_exchange
                           FROM euro_adverts ea
                           WHERE ea.id = o.advert_rowid OR ea.rowid = o.advert_rowid
                           LIMIT 1
                       ),
                       0
                   ),
                   (
                       SELECT ea.operation
                       FROM euro_adverts ea
                       WHERE ea.id = o.advert_rowid OR ea.rowid = o.advert_rowid
                       LIMIT 1
                   ),
                   o.proposer_telegram_id
            FROM advert_offers o
            WHERE o.proposer_telegram_id != ?
              AND ({st_wh})
              AND EXISTS (
                  SELECT 1
                  FROM euro_adverts a
                  WHERE (a.id = o.advert_rowid OR a.rowid = o.advert_rowid)
                    AND (a.user_id = ? OR cast(a.user_id as text) = cast(? as text))
              )
            ORDER BY o.id DESC
            LIMIT ?
            """,
            (uid, uid, uid, lim),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        euro_ex = int(r[4] or 0)
        op = (r[5] or "").strip()
        legacy_ex = op == "معاوضه"
        hybrid = euro_ex == 1 and op in ("خرید", "فروش")
        skip_toman = hybrid or legacy_ex
        out.append(
            {
                "id": int(r[0]),
                "advert_rowid": int(r[1]),
                "rate_toman": int(r[2] or 0),
                "seq_in_advert": int(r[3] or 0),
                "skips_toman_rate_offer": skip_toman,
                "proposer_telegram_id": int(r[6] or 0),
            }
        )
    return out


def update_proposer_pending_offer_rate(
    offer_id: int, proposer_telegram_id: int, rate_toman: int
) -> int | None:
    """اگر به‌روز شد، advert_rowid را برمی‌گرداند؛ وگرنه None."""
    try:
        oid = int(offer_id)
        uid = int(proposer_telegram_id)
        r = int(rate_toman)
    except (TypeError, ValueError):
        return None
    if r < 0:
        return None
    meta = get_advert_offer_joined(oid)
    if not meta or int(meta.get("proposer_telegram_id") or 0) != uid:
        return None
    st = (meta.get("status") or "pending").strip().lower()
    if st != "pending":
        return None
    advert_rowid = int(meta["advert_rowid"])
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if "status" not in _table_columns(conn, "advert_offers"):
            return None
        cur.execute(
            """
            UPDATE advert_offers
            SET rate_toman = ?
            WHERE id = ? AND proposer_telegram_id = ?
              AND COALESCE(NULLIF(TRIM(status), ''), 'pending') = 'pending'
            """,
            (r, oid, uid),
        )
        conn.commit()
        if cur.rowcount < 1:
            return None
    return advert_rowid


def list_advert_offers_joined_for_advert(
    advert_rowid: int, limit: int | None = LIST_RECENT_LIMIT
) -> list[dict]:
    """پیشنهادهای یک آگهی؛ limit=None همهٔ ردیف‌ها (گزارش مذاکره)."""
    try:
        aid = int(advert_rowid)
    except (TypeError, ValueError):
        return []
    lim_sql = ""
    lim_args: tuple = ()
    if limit is not None:
        lim_n = max(1, min(int(limit), 500))
        lim_sql = " LIMIT ?"
        lim_args = (lim_n,)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        acols = _table_columns(conn, "advert_offers")
        seq_sel = "o.seq_in_advert" if "seq_in_advert" in acols else "o.id"
        alias_sel = "o.offer_alias_name" if "offer_alias_name" in acols else "NULL AS offer_alias_name"
        prop_cty = (
            "o.proposer_account_country"
            if "proposer_account_country" in acols
            else "NULL AS proposer_account_country"
        )
        pe_sel = (
            "COALESCE(o.proposed_euro_amount, 0)"
            if "proposed_euro_amount" in acols
            else "0"
        )
        rows = cur.execute(
            f"""
            SELECT o.id, o.advert_rowid, o.proposer_telegram_id, o.rate_toman,
                   o.description, o.status, u.user_id AS owner_id,
                   COALESCE({seq_sel}, o.id) AS seq_in_advert,
                   {alias_sel},
                   {prop_cty},
                   COALESCE(u.account_country, '') AS advert_account_country,
                   {pe_sel} AS proposed_euro_amount
            FROM advert_offers o
            INNER JOIN euro_adverts u ON u.rowid = o.advert_rowid
            WHERE o.advert_rowid = ?
            ORDER BY o.id DESC
            {lim_sql}
            """,
            (aid, *lim_args),
        ).fetchall()
    if limit is None:
        return [dict(r) for r in reversed(rows)]
    return [dict(r) for r in reversed(rows)]


def get_advert_offer_joined(offer_id: int) -> dict | None:
    try:
        oid = int(offer_id)
    except (TypeError, ValueError):
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        acols = _table_columns(conn, "advert_offers")
        seq_sel = "o.seq_in_advert" if "seq_in_advert" in acols else "o.id"
        alias_sel = "o.offer_alias_name" if "offer_alias_name" in acols else "NULL AS offer_alias_name"
        prop_cty = (
            "o.proposer_account_country"
            if "proposer_account_country" in acols
            else "NULL AS proposer_account_country"
        )
        pe_sel = (
            "COALESCE(o.proposed_euro_amount, 0)"
            if "proposed_euro_amount" in acols
            else "0"
        )
        row = cur.execute(
            f"""
            SELECT o.id, o.advert_rowid, o.proposer_telegram_id, o.rate_toman,
                   o.description, o.status, u.user_id AS owner_id,
                   COALESCE({seq_sel}, o.id) AS seq_in_advert,
                   {alias_sel},
                   {prop_cty},
                   COALESCE(u.account_country, '') AS advert_account_country,
                   {pe_sel} AS proposed_euro_amount
            FROM advert_offers o
            INNER JOIN euro_adverts u ON u.rowid = o.advert_rowid
            WHERE o.id = ?
            """,
            (oid,),
        ).fetchone()
        if not row:
            return None
        return dict(row)


def reject_other_pending_offers_for_advert(
    advert_rowid: int, accepted_offer_id: int
) -> list[int]:
    """با پذیرش یک پیشنهاد، بقیهٔ pendingهای همان آگهی را رد می‌کند؛ شناسهٔ ردیف‌ها را برمی‌گرداند."""
    try:
        aid = int(advert_rowid)
        keep = int(accepted_offer_id)
    except (TypeError, ValueError):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if "status" not in _table_columns(conn, "advert_offers"):
            return []
        cur.execute(
            """
            SELECT id FROM advert_offers
            WHERE advert_rowid = ? AND id != ?
              AND COALESCE(NULLIF(TRIM(status), ''), 'pending') = 'pending'
            """,
            (aid, keep),
        )
        ids = [int(r[0]) for r in cur.fetchall()]
        if not ids:
            return []
        cur.execute(
            """
            UPDATE advert_offers SET status = 'rejected'
            WHERE advert_rowid = ? AND id != ?
              AND COALESCE(NULLIF(TRIM(status), ''), 'pending') = 'pending'
            """,
            (aid, keep),
        )
        conn.commit()
    return ids


def accept_advert_offer_atomically(offer_id: int) -> dict:
    """Select exactly one offer for an advert in one SQLite transaction.

    ``BEGIN IMMEDIATE`` serializes competing Accept callbacks before either
    callback reads the offer state.  The winner is accepted and every other
    pending offer is rejected in the same commit.  Existing deal gates keep
    the advert locked until the explicit admin reactivation flow deletes the
    gate.
    """
    result = {
        "accepted": False,
        "reason": "not_found",
        "advert_rowid": None,
        "winner_offer_id": None,
        "rejected_offer_ids": [],
    }
    try:
        oid = int(offer_id)
    except (TypeError, ValueError):
        return result

    conn = sqlite3.connect(DB_PATH, timeout=15.0, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, advert_rowid,
                   lower(trim(COALESCE(NULLIF(status, ''), 'pending')))
            FROM advert_offers
            WHERE id = ?
            """,
            (oid,),
        ).fetchone()
        if not row:
            conn.rollback()
            return result

        aid = int(row[1])
        current_status = (row[2] or "pending").strip().lower()
        result["advert_rowid"] = aid
        if current_status == "accepted":
            result["reason"] = "already_accepted"
            result["winner_offer_id"] = oid
            conn.commit()
            return result
        if current_status != "pending":
            result["reason"] = current_status or "not_pending"
            conn.commit()
            return result

        winner = conn.execute(
            """
            SELECT o.id
            FROM advert_offers o
            LEFT JOIN offer_deal_gates g ON g.offer_id = o.id
            WHERE o.advert_rowid = ?
              AND o.id != ?
              AND (
                    lower(trim(COALESCE(o.status, ''))) = 'accepted'
                    OR lower(trim(COALESCE(g.gate_status, ''))) IN (
                        'pending', 'accounts', 'completed', 'rejected', 'closed'
                    )
                  )
            ORDER BY o.id ASC
            LIMIT 1
            """,
            (aid, oid),
        ).fetchone()
        if winner:
            conn.execute(
                """
                UPDATE advert_offers SET status = 'rejected'
                WHERE id = ?
                  AND lower(trim(COALESCE(NULLIF(status, ''), 'pending'))) = 'pending'
                """,
                (oid,),
            )
            result["reason"] = "winner_exists"
            result["winner_offer_id"] = int(winner[0])
            conn.commit()
            return result

        other_rows = conn.execute(
            """
            SELECT id FROM advert_offers
            WHERE advert_rowid = ? AND id != ?
              AND lower(trim(COALESCE(NULLIF(status, ''), 'pending'))) = 'pending'
            ORDER BY id ASC
            """,
            (aid, oid),
        ).fetchall()
        rejected_ids = [int(item[0]) for item in other_rows]
        updated = conn.execute(
            """
            UPDATE advert_offers SET status = 'accepted'
            WHERE id = ?
              AND lower(trim(COALESCE(NULLIF(status, ''), 'pending'))) = 'pending'
            """,
            (oid,),
        )
        if updated.rowcount != 1:
            conn.rollback()
            result["reason"] = "changed_during_acceptance"
            return result
        conn.execute(
            """
            UPDATE advert_offers SET status = 'rejected'
            WHERE advert_rowid = ? AND id != ?
              AND lower(trim(COALESCE(NULLIF(status, ''), 'pending'))) = 'pending'
            """,
            (aid, oid),
        )
        conn.commit()
        result.update(
            {
                "accepted": True,
                "reason": "accepted",
                "winner_offer_id": oid,
                "rejected_offer_ids": rejected_ids,
            }
        )
        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def update_advert_offer_status(offer_id: int, status: str) -> bool:
    st = (status or "").strip().lower()
    if st not in (
        "pending",
        "accepted",
        "rejected",
        "gate_aborted",
        "gate_rejected",
        "gate_closed",
    ):
        return False
    try:
        oid = int(offer_id)
    except (TypeError, ValueError):
        return False
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if "status" not in _table_columns(conn, "advert_offers"):
            return False
        cur.execute("UPDATE advert_offers SET status = ? WHERE id = ?", (st, oid))
        conn.commit()
        return cur.rowcount > 0


def delete_advert_offer_if_pending(offer_id: int, proposer_telegram_id: int) -> tuple[bool, int | None]:
    try:
        oid = int(offer_id)
        uid = int(proposer_telegram_id)
    except (TypeError, ValueError):
        return False, None
    meta = get_advert_offer_joined(oid)
    if not meta or int(meta.get("proposer_telegram_id") or 0) != uid:
        return False, None
    st = (meta.get("status") or "pending").strip().lower()
    if st == "accepted":
        return False, None
    advert_rowid = int(meta["advert_rowid"])
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM offer_negotiation_lines WHERE offer_id = ?", (oid,))
        if "status" not in _table_columns(conn, "advert_offers"):
            cur.execute(
                "DELETE FROM advert_offers WHERE id = ? AND proposer_telegram_id = ?",
                (oid, uid),
            )
        else:
            cur.execute(
                """
                DELETE FROM advert_offers
                WHERE id = ? AND proposer_telegram_id = ?
                  AND COALESCE(NULLIF(TRIM(status), ''), 'pending') = 'pending'
                """,
                (oid, uid),
            )
        conn.commit()
        ok = cur.rowcount > 0
    if ok:
        renumber_advert_offer_sequences(advert_rowid)
        return True, advert_rowid
    return False, None


def list_pending_offers_for_advert(advert_rowid: int) -> list[dict]:
    """پیشنهادهای هنوز تأییدنشده برای نمایش در کانال (بدون توضیحات)."""
    try:
        aid = int(advert_rowid)
    except (TypeError, ValueError):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        acols = _table_columns(conn, "advert_offers")
        if "status" not in acols:
            return []
        seq_expr = "COALESCE(seq_in_advert, id)" if "seq_in_advert" in acols else "id"
        alias_sel = "COALESCE(offer_alias_name, '')" if "offer_alias_name" in acols else "''"
        pe_sel = (
            "COALESCE(proposed_euro_amount, 0)"
            if "proposed_euro_amount" in acols
            else "0"
        )
        rows = cur.execute(
            f"""
            SELECT id, rate_toman, description, proposer_telegram_id, {seq_expr}, {alias_sel},
                   {pe_sel}
            FROM advert_offers
            WHERE advert_rowid = ?
              AND COALESCE(NULLIF(TRIM(status), ''), 'pending') = 'pending'
            ORDER BY id ASC
            """,
            (aid,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "rate_toman": r[1],
            "description": r[2],
            "proposer_telegram_id": r[3],
            "seq_in_advert": int(r[4]),
            "offer_alias_name": (r[5] or "").strip() if len(r) > 5 else "",
            "proposed_euro_amount": int(r[6] or 0) if len(r) > 6 else 0,
        }
        for r in rows
    ]


def list_accepted_offers_for_advert(advert_rowid: int) -> list[dict]:
    """Return the selected deal offer, including its live gate stage.

    The historical name is kept for compatibility. A selected offer remains
    locked while its deal gate is pending, collecting accounts, paying,
    rejected-awaiting-admin, or closed. Reactivation deletes the gate and
    changes the offer to ``gate_aborted``, so it naturally leaves this list.
    """
    try:
        aid = int(advert_rowid)
    except (TypeError, ValueError):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        acols = _table_columns(conn, "advert_offers")
        if "status" not in acols:
            return []
        seq_expr = (
            "COALESCE(o.seq_in_advert, o.id)"
            if "seq_in_advert" in acols
            else "o.id"
        )
        alias_sel = (
            "COALESCE(o.offer_alias_name, '')"
            if "offer_alias_name" in acols
            else "''"
        )
        pe_sel = (
            "COALESCE(o.proposed_euro_amount, 0)"
            if "proposed_euro_amount" in acols
            else "0"
        )
        rows = cur.execute(
            f"""
            SELECT o.id, o.rate_toman, o.description, o.proposer_telegram_id,
                   {seq_expr}, {alias_sel}, {pe_sel},
                   COALESCE(o.status, ''),
                   COALESCE(g.gate_status, ''),
                   COALESCE(g.seller_toman_settled_at, 0)
            FROM advert_offers o
            LEFT JOIN offer_deal_gates g ON g.offer_id = o.id
            WHERE o.advert_rowid = ?
              AND (
                    lower(trim(COALESCE(o.status, ''))) = 'accepted'
                    OR lower(trim(COALESCE(g.gate_status, ''))) IN (
                        'pending', 'accounts', 'completed', 'rejected', 'closed'
                    )
                  )
            ORDER BY o.id ASC
            """,
            (aid,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "rate_toman": r[1],
            "description": r[2],
            "proposer_telegram_id": r[3],
            "seq_in_advert": int(r[4]),
            "offer_alias_name": (r[5] or "").strip() if len(r) > 5 else "",
            "proposed_euro_amount": int(r[6] or 0) if len(r) > 6 else 0,
            "offer_status": (r[7] or "").strip().lower() if len(r) > 7 else "",
            "gate_status": (r[8] or "").strip().lower() if len(r) > 8 else "",
            "seller_toman_settled_at": int(r[9] or 0) if len(r) > 9 else 0,
        }
        for r in rows
    ]


def list_rejected_offers_for_advert(advert_rowid: int) -> list[dict]:
    try:
        aid = int(advert_rowid)
    except (TypeError, ValueError):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        acols = _table_columns(conn, "advert_offers")
        if "status" not in acols:
            return []
        seq_expr = "COALESCE(seq_in_advert, id)" if "seq_in_advert" in acols else "id"
        alias_sel = "COALESCE(offer_alias_name, '')" if "offer_alias_name" in acols else "''"
        pe_sel = (
            "COALESCE(proposed_euro_amount, 0)"
            if "proposed_euro_amount" in acols
            else "0"
        )
        rows = cur.execute(
            f"""
            SELECT id, rate_toman, description, proposer_telegram_id, {seq_expr}, {alias_sel},
                   {pe_sel}
            FROM advert_offers
            WHERE advert_rowid = ? AND status = 'rejected'
            ORDER BY id ASC
            """,
            (aid,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "rate_toman": r[1],
            "description": r[2],
            "proposer_telegram_id": r[3],
            "seq_in_advert": int(r[4]),
            "offer_alias_name": (r[5] or "").strip() if len(r) > 5 else "",
            "proposed_euro_amount": int(r[6] or 0) if len(r) > 6 else 0,
        }
        for r in rows
    ]


def _offer_effective_euro_sql(*, offer_alias: str = "o", advert_alias: str = "a") -> str:
    """عبارت SQL: مقدار یوروی مؤثر پیشنهاد (جزئی یا کل آگهی)."""
    o, a = offer_alias, advert_alias
    pe = f"COALESCE({o}.proposed_euro_amount, 0)"
    return (
        f"CASE WHEN CAST({pe} AS INTEGER) > 0 "
        f"THEN CAST({pe} AS INTEGER) "
        f"ELSE CAST({a}.euro_amount AS INTEGER) END"
    )


def _row_euro_amount_for_rejection(
    proposed_euro: int, advert_total_euro: int
) -> int:
    pe = int(proposed_euro or 0)
    if pe > 0:
        return pe
    return int(advert_total_euro or 0)


def classify_proposer_rate_rejection(
    advert_rowid: int,
    proposer_telegram_id: int,
    rate_toman: int,
    *,
    target_euro_amount: int,
    advert_total_euro: int,
    operation: str,
) -> str | None:
    """
    همان منطق لیست «پیشنهادهای قبلی» — برای اعتبارسنجی مرحله نرخ.
    برمی‌گرداند: 'exact' | 'sell_low' | 'buy_high' یا None.
    """
    try:
        aid = int(advert_rowid)
        uid = int(proposer_telegram_id)
        rate = int(rate_toman)
        target = int(target_euro_amount)
        adv_e = int(advert_total_euro)
    except (TypeError, ValueError):
        return None
    if aid <= 0 or uid <= 0 or rate <= 0 or target <= 0:
        return None
    op = (operation or "").strip()
    if op not in ("خرید", "فروش"):
        return None
    rows = list_my_advert_offers(aid, uid, limit=50)
    last_rej_rate: int | None = None
    last_rej_id = -1
    for row in rows:
        st = str(row[4] if len(row) > 4 else "pending").strip().lower()
        if st != "rejected":
            continue
        try:
            oid = int(row[0])
            rt = int(row[1] or 0)
        except (TypeError, ValueError):
            continue
        if rt <= 0:
            continue
        pe_row = 0
        if len(row) > 6:
            try:
                pe_row = int(row[6] or 0)
            except (TypeError, ValueError):
                pe_row = 0
        row_euro = _row_euro_amount_for_rejection(pe_row, adv_e)
        if row_euro != target:
            continue
        if rt == rate:
            return "exact"
        if oid >= last_rej_id:
            last_rej_id = oid
            last_rej_rate = rt
    if last_rej_rate is None:
        return None
    if op == "فروش" and rate <= last_rej_rate:
        return "sell_low"
    if op == "خرید" and rate >= last_rej_rate:
        return "buy_high"
    return None


def rejected_offer_rate_and_proposed_euro(
    advert_rowid: int,
    rate_toman: int,
    proposed_euro_amount: int,
) -> bool:
    """همان نرخ + همان proposed_euro_amount (ستون) — هر کاربر، وضعیت رد."""
    try:
        aid = int(advert_rowid)
        rate = int(rate_toman)
        pe = int(proposed_euro_amount)
    except (TypeError, ValueError):
        return False
    if aid <= 0 or rate <= 0 or pe <= 0:
        return False
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cols = _table_columns(conn, "advert_offers")
        if "status" not in cols or "proposed_euro_amount" not in cols:
            return False
        row = cur.execute(
            """
            SELECT 1 FROM advert_offers
            WHERE advert_rowid = ?
              AND LOWER(TRIM(COALESCE(status, ''))) = 'rejected'
              AND rate_toman = ?
              AND CAST(COALESCE(proposed_euro_amount, 0) AS INTEGER) = ?
            LIMIT 1
            """,
            (aid, rate, pe),
        ).fetchone()
    return row is not None


def rejected_offer_same_rate_and_euro(
    advert_rowid: int,
    rate_toman: int,
    effective_euro_amount: int,
) -> bool:
    """هر پیشنهاددهنده — همان نرخ + همان مقدار یورو قبلاً رد شده باشد."""
    try:
        aid = int(advert_rowid)
        rate = int(rate_toman)
        eff = int(effective_euro_amount)
    except (TypeError, ValueError):
        return False
    if aid <= 0 or rate <= 0 or eff <= 0:
        return False
    euro_sql = _offer_effective_euro_sql()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if "status" not in _table_columns(conn, "advert_offers"):
            return False
        row = cur.execute(
            f"""
            SELECT 1 FROM advert_offers o
            INNER JOIN euro_adverts a ON a.rowid = o.advert_rowid
            WHERE o.advert_rowid = ?
              AND LOWER(TRIM(COALESCE(o.status, ''))) = 'rejected'
              AND o.rate_toman = ?
              AND ({euro_sql}) = ?
            LIMIT 1
            """,
            (aid, rate, eff),
        ).fetchone()
    return row is not None


def get_last_rejected_offer_rate_toman(
    advert_rowid: int,
    *,
    proposer_telegram_id: int | None = None,
    effective_euro_amount: int | None = None,
) -> int | None:
    """آخرین نرخ تومانی پیشنهاد رد‌شده (جدیدترین id) — اختیاری: فقط همان پیشنهاددهنده و/یا همان مقدار یورو."""
    try:
        aid = int(advert_rowid)
    except (TypeError, ValueError):
        return None
    try:
        pid = int(proposer_telegram_id) if proposer_telegram_id is not None else None
    except (TypeError, ValueError):
        pid = None
    try:
        eff = int(effective_euro_amount) if effective_euro_amount is not None else None
    except (TypeError, ValueError):
        eff = None
    if eff is not None and eff <= 0:
        eff = None
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if "status" not in _table_columns(conn, "advert_offers"):
            return None
        euro_sql = _offer_effective_euro_sql()
        params: list = [aid]
        where = [
            "o.advert_rowid = ?",
            "LOWER(TRIM(COALESCE(o.status, ''))) = 'rejected'",
        ]
        if pid is not None:
            where.append("o.proposer_telegram_id = ?")
            params.append(pid)
        if eff is not None:
            where.append(f"({euro_sql}) = ?")
            params.append(eff)
        row = cur.execute(
            f"""
            SELECT o.rate_toman FROM advert_offers o
            INNER JOIN euro_adverts a ON a.rowid = o.advert_rowid
            WHERE {' AND '.join(where)}
            ORDER BY o.id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    if not row:
        return None
    try:
        r = int(row[0])
    except (TypeError, ValueError):
        return None
    return r if r > 0 else None


def delete_pending_offers_for_proposer_on_advert(
    advert_rowid: int, proposer_telegram_id: int
) -> list[dict]:
    """حذف همهٔ پیشنهادهای pending این کاربر روی این آگهی (جایگزینی با پیشنهاد جدید)."""
    try:
        aid = int(advert_rowid)
        uid = int(proposer_telegram_id)
    except (TypeError, ValueError):
        return []
    deleted_meta: list[dict] = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if "status" not in _table_columns(conn, "advert_offers"):
            return []
        rows = cur.execute(
            """
            SELECT o.id, o.advert_rowid, o.proposer_telegram_id, u.user_id AS owner_id
            FROM advert_offers o
            INNER JOIN euro_adverts u ON u.rowid = o.advert_rowid
            WHERE o.advert_rowid = ? AND o.proposer_telegram_id = ?
              AND LOWER(COALESCE(NULLIF(TRIM(o.status), ''), 'pending')) = 'pending'
            """,
            (aid, uid),
        ).fetchall()
        if not rows:
            return []
        for r in rows:
            deleted_meta.append(
                {
                    "id": int(r["id"]),
                    "advert_rowid": int(r["advert_rowid"]),
                    "proposer_telegram_id": int(r["proposer_telegram_id"]),
                    "owner_id": int(r["owner_id"]),
                }
            )
        ids = [int(r["id"]) for r in rows]
        qmarks = ",".join(["?"] * len(ids))
        cur.execute(f"DELETE FROM offer_negotiation_lines WHERE offer_id IN ({qmarks})", ids)
        cur.execute(f"DELETE FROM advert_offers WHERE id IN ({qmarks})", ids)
        conn.commit()
    renumber_advert_offer_sequences(aid)
    return deleted_meta


def renumber_advert_offer_sequences(advert_rowid: int) -> None:
    try:
        aid = int(advert_rowid)
    except (TypeError, ValueError):
        return
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if "seq_in_advert" not in _table_columns(conn, "advert_offers"):
            return
        cur.execute(
            "SELECT id FROM advert_offers WHERE advert_rowid = ? ORDER BY id ASC",
            (aid,),
        )
        rows = cur.fetchall()
        for i, (rid,) in enumerate(rows, start=1):
            cur.execute(
                "UPDATE advert_offers SET seq_in_advert = ? WHERE id = ?",
                (i, rid),
            )
        conn.commit()


def get_offer_by_advert_and_seq(advert_rowid: int, seq_in_advert: int) -> dict | None:
    try:
        aid = int(advert_rowid)
        seq = int(seq_in_advert)
    except (TypeError, ValueError):
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if "seq_in_advert" not in _table_columns(conn, "advert_offers"):
            return None
        row = cur.execute(
            """
            SELECT * FROM advert_offers
            WHERE advert_rowid = ? AND seq_in_advert = ?
            LIMIT 1
            """,
            (aid, seq),
        ).fetchone()
        if not row:
            return None
        return dict(row)


def admin_update_offer_rate(offer_id: int, rate_toman: int) -> bool:
    try:
        oid = int(offer_id)
        r = int(rate_toman)
    except (TypeError, ValueError):
        return False
    if r < 0:
        return False
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE advert_offers SET rate_toman = ? WHERE id = ?", (r, oid))
        conn.commit()
        return cur.rowcount > 0


def admin_update_offer_proposed_euro(offer_id: int, proposed_euro_amount: int | None) -> bool:
    """مقدار یوروی پیشنهادی (counter-offer); None برای پاک کردن."""
    try:
        oid = int(offer_id)
    except (TypeError, ValueError):
        return False
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cols = _table_columns(conn, "advert_offers")
        if "proposed_euro_amount" not in cols:
            return False
        if proposed_euro_amount is None:
            cur.execute(
                "UPDATE advert_offers SET proposed_euro_amount = NULL WHERE id = ?",
                (oid,),
            )
        else:
            try:
                pe = int(proposed_euro_amount)
            except (TypeError, ValueError):
                return False
            if pe <= 0:
                return False
            cur.execute(
                "UPDATE advert_offers SET proposed_euro_amount = ? WHERE id = ?",
                (pe, oid),
            )
        conn.commit()
        return cur.rowcount > 0


def admin_delete_offer_by_id(offer_id: int) -> dict | None:
    try:
        oid = int(offer_id)
    except (TypeError, ValueError):
        return None
    meta = get_advert_offer_joined(oid)
    if not meta:
        return None
    advert_rowid = int(meta["advert_rowid"])
    out = {
        "offer_id": oid,
        "advert_rowid": advert_rowid,
        "owner_id": int(meta.get("owner_id") or 0),
        "proposer_telegram_id": int(meta.get("proposer_telegram_id") or 0),
    }
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM offer_negotiation_lines WHERE offer_id = ?", (oid,))
        cur.execute("DELETE FROM advert_offers WHERE id = ?", (oid,))
        deleted_offer = cur.rowcount
        conn.commit()
        if deleted_offer == 0:
            return None
    renumber_advert_offer_sequences(advert_rowid)
    return out


def get_user_by_id(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
        return cur.fetchone()

def get_user_by_phone(phone_number):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE phone_number = ?", (phone_number,))
        return cur.fetchone()

def save_user(user_id, full_name, last_name, email, address, phone_number, display_name: str | None = None, username: str | None = None):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cols = _table_columns(conn, "users")
        if "created_at" in cols:
            cur.execute(
                """
                INSERT INTO users (
                    telegram_id, full_name, last_name, email, address, phone_number,
                    display_name, username, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (user_id, full_name, last_name, email, address, phone_number, display_name, username),
            )
        else:
            cur.execute(
                """
                INSERT INTO users (telegram_id, full_name, last_name, email, address, phone_number, display_name, username)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, full_name, last_name, email, address, phone_number, display_name, username),
            )
        conn.commit()

def update_euro_advert_status(advert_rowid: int, status: str) -> bool:
    try:
        rid = int(advert_rowid)
    except (TypeError, ValueError):
        return False
    st = (status or "").strip()
    if not st:
        return False
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE euro_adverts SET status = ? WHERE rowid = ?", (st, rid))
        conn.commit()
        return cur.rowcount > 0


# =============================================================================
# Deal Gate CRUD | offer_deal_gates (one row per offer_id)
# EN: get/upsert/delete; receipt logs as JSON strings.
# FA: خواندن/نوشتن gate؛ فیش تومان و یورو و تأیید نشستن به‌صورت JSON.
# See: docs/DEAL_GATE.md
# =============================================================================


def _deal_gate_row_to_dict(row: sqlite3.Row | tuple, cols: list[str]) -> dict:
    if hasattr(row, "keys"):
        return dict(row)
    return {cols[i]: row[i] for i in range(len(cols))}


def deal_gate_get(offer_id: int) -> dict | None:
    try:
        oid = int(offer_id)
    except (TypeError, ValueError):
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = cur.execute(
            "SELECT * FROM offer_deal_gates WHERE offer_id = ?",
            (oid,),
        ).fetchone()
    return dict(row) if row else None


def deal_gate_upsert(
    *,
    offer_id: int,
    advert_rowid: int,
    buyer_telegram_id: int,
    seller_telegram_id: int,
    gate_status: str | None = None,
    **fields,
) -> None:
    oid = int(offer_id)
    now = int(time.time())
    if "gate_status" in fields:
        gate_status = fields.pop("gate_status")
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        existing = cur.execute(
            "SELECT 1 FROM offer_deal_gates WHERE offer_id = ?",
            (oid,),
        ).fetchone()
        if not existing:
            cur.execute(
                """
                INSERT INTO offer_deal_gates (
                    offer_id, advert_rowid, buyer_telegram_id, seller_telegram_id,
                    gate_status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    oid,
                    int(advert_rowid),
                    int(buyer_telegram_id),
                    int(seller_telegram_id),
                    (gate_status or "pending").strip(),
                    now,
                ),
            )
        elif gate_status is not None:
            cur.execute(
                "UPDATE offer_deal_gates SET gate_status = ? WHERE offer_id = ?",
                ((gate_status or "pending").strip(), oid),
            )
        allowed = {
            "gate_status",
            "buyer_response",
            "seller_response",
            "buyer_confirmed_at",
            "seller_confirmed_at",
            "reminder_count",
            "admin_escalated_at",
            "admin_decision",
            "buyer_gate_mid",
            "seller_gate_mid",
            "buyer_accounts_text",
            "seller_accounts_text",
            "buyer_accounts_photo_file_id",
            "seller_accounts_photo_file_id",
            "completed_at",
            "admin_notify_mids",
            "admin_notify_photo_mids",
            "admin_escalation_mids",
            "buyer_receipt_log",
            "buyer_toman_card_sent_at",
            "seller_receipt_log",
            "seller_eur_account_sent_at",
            "buyer_toman_settled_at",
            "seller_toman_admin_log",
            "seller_toman_settled_at",
            "seller_toman_close_enabled_at",
        }
        for key, val in fields.items():
            if key not in allowed:
                continue
            cur.execute(
                f"UPDATE offer_deal_gates SET {key} = ? WHERE offer_id = ?",
                (val, oid),
            )
        conn.commit()


def _deal_gate_receipt_list_raw(gate: dict | None) -> list:
    import json

    raw = (gate or {}).get("buyer_receipt_log") or "[]"
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def deal_gate_buyer_receipt_list(offer_id: int) -> list[dict]:
    gate = deal_gate_get(offer_id)
    return _deal_gate_receipt_list_raw(gate)


def deal_gate_append_buyer_receipt(
    offer_id: int,
    *,
    entry_type: str,
    text: str = "",
    file_id: str = "",
) -> list[dict]:
    """یک فیش واریز خریدار — برمی‌گرداند لیست کامل."""
    import json

    gate = deal_gate_get(offer_id)
    if not gate:
        return []
    items = _deal_gate_receipt_list_raw(gate)
    items.append(
        {
            "type": (entry_type or "text").strip().lower(),
            "text": (text or "")[:2000],
            "file_id": (file_id or "").strip()[:256],
            "at": int(time.time()),
        }
    )
    oid = int(offer_id)
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=int(gate["buyer_telegram_id"]),
        seller_telegram_id=int(gate["seller_telegram_id"]),
        buyer_receipt_log=json.dumps(items, ensure_ascii=False),
    )
    return items


def _deal_gate_seller_receipt_list_raw(gate: dict | None) -> list:
    import json

    raw = (gate or {}).get("seller_receipt_log") or "[]"
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def deal_gate_seller_receipt_list(offer_id: int) -> list[dict]:
    gate = deal_gate_get(offer_id)
    return _deal_gate_seller_receipt_list_raw(gate)


def deal_gate_append_seller_receipt(
    offer_id: int,
    *,
    entry_type: str,
    text: str = "",
    file_id: str = "",
) -> list[dict]:
    """یک فیش واریز یورو فروشنده — برمی‌گرداند لیست کامل."""
    import json

    gate = deal_gate_get(offer_id)
    if not gate:
        return []
    items = _deal_gate_seller_receipt_list_raw(gate)
    items.append(
        {
            "type": (entry_type or "text").strip().lower(),
            "text": (text or "")[:2000],
            "file_id": (file_id or "").strip()[:256],
            "at": int(time.time()),
            "buyer_confirmed_at": 0,
        }
    )
    oid = int(offer_id)
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=int(gate["buyer_telegram_id"]),
        seller_telegram_id=int(gate["seller_telegram_id"]),
        seller_receipt_log=json.dumps(items, ensure_ascii=False),
    )
    return items


def deal_gate_confirm_seller_receipt_buyer(
    offer_id: int,
    receipt_index: int,
    *,
    confirmed_by: str = "buyer",
) -> bool:
    """ثبت تأیید «یورو نشست» برای یک فیش فروشنده (خریدار یا ادمین)."""
    import json

    gate = deal_gate_get(offer_id)
    if not gate:
        return False
    items = _deal_gate_seller_receipt_list_raw(gate)
    try:
        idx = int(receipt_index)
    except (TypeError, ValueError):
        return False
    if idx < 0 or idx >= len(items):
        return False
    if int(items[idx].get("buyer_confirmed_at") or 0) > 0:
        return True
    by = (confirmed_by or "buyer").strip().lower()
    if by not in ("buyer", "admin"):
        by = "buyer"
    items[idx]["buyer_confirmed_at"] = int(time.time())
    items[idx]["confirmed_by"] = by
    oid = int(offer_id)
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=int(gate["buyer_telegram_id"]),
        seller_telegram_id=int(gate["seller_telegram_id"]),
        seller_receipt_log=json.dumps(items, ensure_ascii=False),
    )
    return True


def _deal_gate_seller_toman_admin_list_raw(gate: dict | None) -> list:
    import json

    raw = (gate or {}).get("seller_toman_admin_log") or "[]"
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def deal_gate_seller_toman_admin_list(offer_id: int) -> list[dict]:
    gate = deal_gate_get(offer_id)
    return _deal_gate_seller_toman_admin_list_raw(gate)


def deal_gate_append_seller_toman_admin(
    offer_id: int,
    *,
    entry_type: str,
    text: str = "",
    file_id: str = "",
) -> list[dict]:
    """فیش واریز تومان ادمین به فروشنده."""
    import json

    gate = deal_gate_get(offer_id)
    if not gate:
        return []
    items = _deal_gate_seller_toman_admin_list_raw(gate)
    items.append(
        {
            "type": (entry_type or "text").strip().lower(),
            "text": (text or "")[:2000],
            "file_id": (file_id or "").strip()[:256],
            "at": int(time.time()),
        }
    )
    oid = int(offer_id)
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=int(gate["buyer_telegram_id"]),
        seller_telegram_id=int(gate["seller_telegram_id"]),
        seller_toman_admin_log=json.dumps(items, ensure_ascii=False),
    )
    return items


def deal_gate_enable_seller_toman_close(offer_id: int) -> int:
    """فعال‌سازی فلو «تومان نشست — پایان معامله» — فقط پس از ارسال جدید فیش تومان."""
    gate = deal_gate_get(offer_id)
    if not gate:
        return 0
    now = int(time.time())
    oid = int(offer_id)
    deal_gate_upsert(
        offer_id=oid,
        advert_rowid=int(gate["advert_rowid"]),
        buyer_telegram_id=int(gate["buyer_telegram_id"]),
        seller_telegram_id=int(gate["seller_telegram_id"]),
        seller_toman_close_enabled_at=now,
    )
    return now


def deal_gate_delete(offer_id: int) -> None:
    try:
        oid = int(offer_id)
    except (TypeError, ValueError):
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.cursor().execute("DELETE FROM offer_deal_gates WHERE offer_id = ?", (oid,))
        conn.commit()


def deal_gate_accounts_for_user(user_id: int) -> list[dict]:
    """All account-stage deals still awaiting this user's account details."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return []
    out: list[dict] = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT * FROM offer_deal_gates
            WHERE (buyer_telegram_id = ? OR seller_telegram_id = ?)
              AND gate_status IN ('accounts', 'pending')
            ORDER BY started_at DESC
            """,
            (uid, uid),
        ).fetchall()
        for row in rows:
            d = dict(row)
            st = (d.get("gate_status") or "").strip().lower()
            br = (d.get("buyer_response") or "").strip().lower()
            sr = (d.get("seller_response") or "").strip().lower()
            if st == "pending":
                if br != "yes" or sr != "yes":
                    continue
                oid = int(d["offer_id"])
                cur.execute(
                    "UPDATE offer_deal_gates SET gate_status = 'accounts' WHERE offer_id = ?",
                    (oid,),
                )
                conn.commit()
                d["gate_status"] = "accounts"
            elif st != "accounts":
                continue
            buyer_id = int(d.get("buyer_telegram_id") or 0)
            seller_id = int(d.get("seller_telegram_id") or 0)
            if uid == buyer_id and (d.get("buyer_accounts_text") or "").strip():
                continue
            if uid == seller_id and (d.get("seller_accounts_text") or "").strip():
                continue
            out.append(d)
    return out


def deal_gate_active_for_user(user_id: int) -> dict | None:
    """Most recent account-stage deal awaiting this user's account details."""
    rows = deal_gate_accounts_for_user(user_id)
    return rows[0] if rows else None


def deal_gate_list_for_admin(*, limit: int = 25) -> list[dict]:
    """معاملات باز، تکمیل‌شده و رد/بستهٔ اخیر برای پنل ادمین."""
    lim = max(1, min(int(limit), 50))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM offer_deal_gates
            WHERE gate_status IN (
                'pending', 'accounts', 'completed', 'rejected', 'closed'
            )
            ORDER BY
                CASE gate_status
                    WHEN 'pending' THEN 0
                    WHEN 'accounts' THEN 1
                    WHEN 'completed' THEN 2
                    WHEN 'rejected' THEN 3
                    WHEN 'closed' THEN 4
                    ELSE 5
                END,
                COALESCE(completed_at, started_at) DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
        return [dict(r) for r in rows]


def deal_gate_list_awaiting_seller_toman_confirm() -> list[dict]:
    """معاملات completed با فیش تومان ادمین و بدون تأیید پایان فروشنده."""
    import json

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM offer_deal_gates
            WHERE gate_status = 'completed'
            ORDER BY offer_id ASC
            """
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        d = dict(row)
        if int(d.get("seller_toman_settled_at") or 0) > 0:
            continue
        if int(d.get("seller_toman_close_enabled_at") or 0) <= 0:
            continue
        raw = (d.get("seller_toman_admin_log") or "[]").strip()
        try:
            items = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            items = []
        if isinstance(items, list) and items:
            d["offer_id"] = int(d["offer_id"])
            out.append(d)
    return out


def deal_gate_lookup_for_admin(
    *,
    offer_id: int | None = None,
    advert_rowid: int | None = None,
) -> dict | None:
    """جستجوی معامله برای ادمین با offer_id یا advert_rowid."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if offer_id is not None:
            try:
                oid = int(offer_id)
            except (TypeError, ValueError):
                return None
            row = conn.execute(
                "SELECT * FROM offer_deal_gates WHERE offer_id = ?",
                (oid,),
            ).fetchone()
            return dict(row) if row else None
        if advert_rowid is not None:
            try:
                aid = int(advert_rowid)
            except (TypeError, ValueError):
                return None
            row = conn.execute(
                """
                SELECT * FROM offer_deal_gates
                WHERE advert_rowid = ?
                ORDER BY COALESCE(completed_at, started_at) DESC
                LIMIT 1
                """,
                (aid,),
            ).fetchone()
            return dict(row) if row else None
    return None


@contextmanager
def get_db():
    """EN: SQLite connection with auto-commit. FA: اتصال با commit خودکار."""
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
