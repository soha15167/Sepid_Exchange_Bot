"""Quiet maintenance for deal backups, privacy retention, and health metadata."""

from __future__ import annotations

import time
from pathlib import Path

from config.settings import (
    DB_PATH,
    DEAL_BACKUP_DIR,
    DEAL_BACKUP_ENCRYPTION_KEY,
    DEAL_FINANCIAL_RETENTION_DAYS,
    DEAL_OFFSITE_BACKUP_DIR,
    DEAL_RECONCILIATION_DIR,
)
from database.db import deal_privacy_redact_expired, set_setting
from scripts.backup_db import (
    ROOT,
    configured_backup_retention,
    create_verified_backup,
    resolve_database_path,
)
from utils.operational_readiness import (
    build_reconciliation_report,
    encrypt_and_replicate_backup,
    run_restore_drill,
    write_reconciliation_csv,
    reconcile_with_iran_panel,
)
from utils.iran_panel_client import get_transactions
from config.settings import IRAN_PANEL_BASE_URL


def run_deal_verified_backup() -> Path:
    """Create and verify an online backup, recording status for the health page."""
    try:
        backup_dir = Path(DEAL_BACKUP_DIR).expanduser()
        if not backup_dir.is_absolute():
            backup_dir = ROOT / backup_dir
        destination = create_verified_backup(
            resolve_database_path(DB_PATH),
            backup_dir,
            keep=configured_backup_retention(),
        )
        set_setting("deal_last_backup_at", str(int(time.time())))
        set_setting("deal_last_backup_path", str(destination))
        set_setting("deal_last_backup_error", "")
        if DEAL_OFFSITE_BACKUP_DIR and DEAL_BACKUP_ENCRYPTION_KEY:
            try:
                replica = encrypt_and_replicate_backup(
                    destination, DEAL_OFFSITE_BACKUP_DIR, DEAL_BACKUP_ENCRYPTION_KEY
                )
                set_setting("deal_last_offsite_backup_at", str(int(time.time())))
                set_setting("deal_last_offsite_backup_path", str(replica))
                set_setting("deal_last_offsite_backup_error", "")
            except Exception as exc:
                # A remote-volume problem must not invalidate the verified local copy.
                set_setting("deal_last_offsite_backup_error", str(exc)[:500])
        return destination
    except Exception as exc:
        set_setting("deal_last_backup_error", str(exc)[:500])
        raise


def run_deal_privacy_retention() -> int:
    """Redact expired financial artifacts without messaging any user."""
    count = deal_privacy_redact_expired(
        retention_days=DEAL_FINANCIAL_RETENTION_DAYS
    )
    set_setting("deal_last_privacy_run_at", str(int(time.time())))
    set_setting("deal_last_privacy_redacted", str(int(count)))
    return count


def run_deal_restore_drill() -> dict:
    """Verify the newest backup through a disposable restore, without replacing DB."""
    backup_dir = Path(DEAL_BACKUP_DIR).expanduser()
    if not backup_dir.is_absolute():
        backup_dir = ROOT / backup_dir
    backups = sorted(
        backup_dir.glob("*.bak") if backup_dir.is_dir() else [],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not backups:
        raise FileNotFoundError(f"no backup found in {backup_dir}")
    try:
        result = run_restore_drill(backups[0])
        if not result.get("ok"):
            raise RuntimeError(f"restore drill failed: {result}")
        set_setting("deal_last_restore_drill_at", str(int(time.time())))
        set_setting("deal_last_restore_drill_path", str(backups[0]))
        set_setting("deal_last_restore_drill_error", "")
        return result
    except Exception as exc:
        set_setting("deal_last_restore_drill_error", str(exc)[:500])
        raise


def run_deal_reconciliation() -> Path:
    """Write a daily control report silently for admin/operator review."""
    report_dir = Path(DEAL_RECONCILIATION_DIR).expanduser()
    if not report_dir.is_absolute():
        report_dir = ROOT / report_dir
    rows = build_reconciliation_report(resolve_database_path(DB_PATH))
    destination = report_dir / time.strftime("deal-reconciliation-%Y%m%d.csv")
    try:
        panel_ok, panel_result = get_transactions(base_url=IRAN_PANEL_BASE_URL)
        if panel_ok and isinstance(panel_result, list):
            rows = reconcile_with_iran_panel(rows, panel_result)
            set_setting("deal_last_panel_reconciliation_error", "")
        else:
            panel_error = str(panel_result)[:500]
            for row in rows:
                row["panel_buyer_status"] = "unavailable"
                row["panel_buyer_ids"] = ""
                row["panel_buyer_unit"] = ""
                row["panel_seller_status"] = "unavailable"
                row["panel_seller_ids"] = ""
                row["panel_seller_unit"] = ""
                row.setdefault("issues", []).append("panel_unavailable")
            set_setting("deal_last_panel_reconciliation_error", panel_error)
        result = write_reconciliation_csv(rows, destination)
        set_setting("deal_last_reconciliation_at", str(int(time.time())))
        set_setting("deal_last_reconciliation_path", str(result))
        set_setting(
            "deal_last_reconciliation_issues",
            str(sum(bool(row.get("issues")) for row in rows)),
        )
        set_setting("deal_last_reconciliation_error", "")
        return result
    except Exception as exc:
        set_setting("deal_last_reconciliation_error", str(exc)[:500])
        raise
