"""Read-only operations checks plus opt-in encrypted backup replication.

Nothing in this module sends Telegram messages or mutates production deal data.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_TABLES = {
    "advert_offers",
    "euro_adverts",
    "offer_deal_gates",
    "deal_delivery_queue",
    "settings",
}


def verify_database_copy(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Open a database read-only and verify integrity/schema without restoring it."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    uri = f"{source.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30.0) as conn:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(REQUIRED_TABLES - tables)
        counts = {}
        for table in sorted(REQUIRED_TABLES & tables):
            counts[table] = int(
                conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
    return {
        "ok": integrity == "ok" and not missing,
        "path": str(source),
        "integrity": integrity,
        "missing_tables": missing,
        "row_counts": counts,
        "size_bytes": source.stat().st_size,
    }


def run_restore_drill(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Copy a backup to a disposable directory and prove it can be opened."""
    source = Path(path).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="sepid-restore-drill-") as tmp:
        drill_copy = Path(tmp) / "restored.db"
        shutil.copy2(source, drill_copy)
        result = verify_database_copy(drill_copy)
    result["source_path"] = str(source)
    result["drill_copy_removed"] = True
    return result


def operational_status(
    database_path: str | os.PathLike[str],
    backup_dir: str | os.PathLike[str],
    *,
    max_backup_age_hours: int = 12,
    now: int | None = None,
) -> dict[str, Any]:
    """Return machine-readable status for cron/systemd/external monitoring."""
    ts = int(now or time.time())
    db = verify_database_copy(database_path)
    directory = Path(backup_dir).expanduser().resolve()
    backups = sorted(
        directory.glob("*.bak") if directory.is_dir() else [],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    latest = backups[0] if backups else None
    backup_age = ts - int(latest.stat().st_mtime) if latest else None
    backup_ok = backup_age is not None and backup_age <= max(1, max_backup_age_hours) * 3600

    database = Path(database_path).expanduser().resolve()
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as conn:
        queue = conn.execute(
            """
            SELECT
              SUM(CASE WHEN status IN ('pending','failed','sending') THEN 1 ELSE 0 END),
              SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),
              MIN(CASE WHEN status IN ('pending','failed','sending') THEN created_at END)
            FROM deal_delivery_queue
            """
        ).fetchone()
    open_count = int((queue or (0, 0, 0))[0] or 0)
    failed_count = int((queue or (0, 0, 0))[1] or 0)
    oldest = int((queue or (0, 0, 0))[2] or 0)
    queue_stale = bool(oldest and ts - oldest > 3600)
    checks = {
        "database": bool(db["ok"]),
        "recent_backup": backup_ok,
        "delivery_queue": failed_count == 0 and not queue_stale,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "database": db,
        "backup": {
            "latest_path": str(latest) if latest else "",
            "age_seconds": backup_age,
            "max_age_hours": max(1, int(max_backup_age_hours)),
        },
        "delivery_queue": {
            "open": open_count,
            "failed": failed_count,
            "oldest_age_seconds": max(0, ts - oldest) if oldest else 0,
        },
        "checked_at": ts,
    }


def build_reconciliation_report(
    database_path: str | os.PathLike[str],
) -> list[dict[str, Any]]:
    """Produce one finance/control row per deal without changing its state."""
    path = Path(database_path).expanduser().resolve()
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT g.offer_id, g.advert_rowid, g.gate_status,
                   g.buyer_toman_settled_at, g.seller_toman_settled_at,
                   g.completed_at, g.started_at,
                   o.status AS offer_status, o.rate_toman,
                   COALESCE(o.proposed_euro_amount, a.euro_amount, 0) AS euro_amount,
                   a.status AS advert_status, a.fee_override_eur,
                   a.operation
            FROM offer_deal_gates g
            LEFT JOIN advert_offers o ON o.id = g.offer_id
            LEFT JOIN euro_adverts a ON a.id = g.advert_rowid
            ORDER BY g.offer_id
            """
        ).fetchall()
    report: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        issues: list[str] = []
        gate_status = str(row.get("gate_status") or "")
        if gate_status == "closed":
            if not int(row.get("buyer_toman_settled_at") or 0):
                issues.append("buyer_toman_not_settled")
            if not int(row.get("seller_toman_settled_at") or 0):
                issues.append("seller_toman_not_settled")
            if str(row.get("offer_status") or "").lower() != "gate_closed":
                issues.append("offer_not_closed")
            if str(row.get("advert_status") or "").strip() != "بسته":
                issues.append("advert_not_closed")
        euro = int(row.get("euro_amount") or 0)
        rate = int(row.get("rate_toman") or 0)
        override = row.get("fee_override_eur")
        if override is None or str(override).strip() == "":
            fee_eur = 2.5 if 0 < euro <= 500 else euro * 0.005
        else:
            fee_eur = max(0.0, float(override))
        fee_toman = int(round(fee_eur * rate))
        base_toman = euro * rate
        report.append(
            {
                "offer_id": int(row["offer_id"]),
                "advert_rowid": int(row["advert_rowid"]),
                "gate_status": gate_status,
                "offer_status": row.get("offer_status") or "",
                "advert_status": row.get("advert_status") or "",
                "euro_amount": euro,
                "rate_toman": rate,
                "gross_toman": base_toman,
                "buyer_expected_toman": base_toman + fee_toman,
                "seller_expected_toman": max(0, base_toman - fee_toman),
                "buyer_toman_settled": bool(row.get("buyer_toman_settled_at")),
                "seller_toman_settled": bool(row.get("seller_toman_settled_at")),
                "closed_at": int(
                    row.get("seller_toman_settled_at")
                    or row.get("completed_at")
                    or 0
                ),
                "issues": issues,
            }
        )
    return report


def _ledger_direction(transaction: dict[str, Any]) -> str:
    values = [
        str(transaction.get(key) or "").strip().lower()
        for key in ("iran_type", "type")
    ]
    if any("خروج" in value for value in values) or any(
        value in {"withdrawal", "withdraw", "output", "out", "expense"}
        for value in values
    ):
        return "out"
    if any("ورود" in value for value in values) or any(
        value in {"deposit", "input", "in", "income"} for value in values
    ):
        return "in"
    return ""


def _ledger_amount(transaction: dict[str, Any]) -> int:
    for key in ("iran_amount", "toman_amount", "amount"):
        try:
            value = int(round(float(transaction.get(key) or 0)))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _ledger_timestamp(transaction: dict[str, Any]) -> int:
    raw = str(transaction.get("date") or "").strip()
    if not raw:
        return 0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return 0


def reconcile_with_iran_panel(
    rows: list[dict[str, Any]], transactions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Annotate report rows; never approve, reject, or mutate a transaction."""
    for row in rows:
        if row.get("gate_status") != "closed":
            row.update({
                "panel_buyer_status": "not_due",
                "panel_buyer_ids": "",
                "panel_buyer_unit": "",
                "panel_seller_status": "not_due",
                "panel_seller_ids": "",
                "panel_seller_unit": "",
            })
            continue
        for party, direction, amount_key in (
            ("buyer", "in", "buyer_expected_toman"),
            ("seller", "out", "seller_expected_toman"),
        ):
            expected = int(row.get(amount_key) or 0)
            exact: list[dict[str, Any]] = []
            for transaction in transactions:
                if _ledger_direction(transaction) != direction:
                    continue
                ledger_amount = _ledger_amount(transaction)
                if ledger_amount not in {expected, expected * 10}:
                    continue
                reference_text = str(transaction.get("description") or "")
                has_reference = any(
                    bool(value) and bool(re.search(rf"(?<!\d){value}(?!\d)", reference_text))
                    for value in (
                        int(row.get("offer_id") or 0),
                        int(row.get("advert_rowid") or 0),
                    )
                )
                tx_at = _ledger_timestamp(transaction)
                closed_at = int(row.get("closed_at") or 0)
                if not has_reference and tx_at and closed_at:
                    if abs(tx_at - closed_at) > 7 * 86400:
                        continue
                exact.append(transaction)
            ids = [str(item.get("id")) for item in exact if item.get("id") is not None]
            status = "matched" if len(exact) == 1 else "missing" if not exact else "ambiguous"
            row[f"panel_{party}_status"] = status
            row[f"panel_{party}_ids"] = ",".join(ids)
            units = {
                "toman" if _ledger_amount(item) == expected else "rial"
                for item in exact
            }
            row[f"panel_{party}_unit"] = units.pop() if len(units) == 1 else ""
            if status != "matched":
                row.setdefault("issues", []).append(f"panel_{party}_{status}")
    return rows


def write_reconciliation_csv(
    rows: list[dict[str, Any]], destination: str | os.PathLike[str]
) -> Path:
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "offer_id", "advert_rowid", "gate_status", "offer_status",
        "advert_status", "euro_amount", "rate_toman", "gross_toman",
        "buyer_expected_toman", "seller_expected_toman",
        "buyer_toman_settled", "seller_toman_settled", "closed_at", "issues",
        "panel_buyer_status", "panel_buyer_ids",
        "panel_buyer_unit", "panel_seller_status", "panel_seller_ids",
        "panel_seller_unit",
    ]
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            item = dict(row)
            for field in fields:
                item.setdefault(field, "")
            item["issues"] = ";".join(item.get("issues") or [])
            writer.writerow(item)
    return target


def _decode_backup_key(value: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:
        raise ValueError("DEAL_BACKUP_ENCRYPTION_KEY must be URL-safe base64") from exc
    if len(key) != 32:
        raise ValueError("DEAL_BACKUP_ENCRYPTION_KEY must decode to 32 bytes")
    return key


def encrypt_and_replicate_backup(
    source_path: str | os.PathLike[str],
    destination_dir: str | os.PathLike[str],
    encryption_key: str,
) -> Path:
    """Encrypt a verified backup with AES-GCM, then atomically copy it off-site.

    The destination may be a mounted remote volume. Network upload is deliberately
    left to the mount/host, so the bot never handles cloud credentials.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - dependency error is explicit
        raise RuntimeError("install the 'cryptography' package first") from exc

    source = Path(source_path).expanduser().resolve()
    verification = verify_database_copy(source)
    if not verification["ok"]:
        raise RuntimeError("refusing to replicate an invalid backup")
    target_dir = Path(destination_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    key = _decode_backup_key(encryption_key)
    nonce = os.urandom(12)
    plaintext = source.read_bytes()
    header = b"SEPIDBK1"
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, header)
    target = target_dir / f"{source.name}.aesgcm"
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(header + nonce + ciphertext)
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    os.chmod(target, 0o600)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix + ".sha256").write_text(
        f"{digest}  {target.name}\n", encoding="ascii"
    )
    return target


def decrypt_backup(
    encrypted_path: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    encryption_key: str,
) -> Path:
    """Decrypt an off-site artifact for an explicit restore drill."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    source = Path(encrypted_path).expanduser().resolve()
    data = source.read_bytes()
    header, nonce, ciphertext = data[:8], data[8:20], data[20:]
    if header != b"SEPIDBK1":
        raise ValueError("unsupported encrypted backup format")
    plaintext = AESGCM(_decode_backup_key(encryption_key)).decrypt(
        nonce, ciphertext, header
    )
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(plaintext)
    os.chmod(target, 0o600)
    return target
