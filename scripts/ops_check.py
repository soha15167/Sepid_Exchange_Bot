#!/usr/bin/env python3
"""Sepid operational CLI. Read-only by default; replication is explicit."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--database", default=os.getenv("DATABASE_NAME", "eurobot.db"))
    status.add_argument("--backup-dir", default=os.getenv("DEAL_BACKUP_DIR", "backups"))
    status.add_argument("--max-backup-age-hours", type=int, default=int(os.getenv("DEAL_BACKUP_MAX_AGE_HOURS", "12")))
    drill = sub.add_parser("restore-drill")
    drill.add_argument("backup")
    report = sub.add_parser("reconcile")
    report.add_argument("--database", default=os.getenv("DATABASE_NAME", "eurobot.db"))
    report.add_argument("--output", default="")
    replicate = sub.add_parser("replicate-backup")
    replicate.add_argument("backup")
    replicate.add_argument("--destination-dir", default=os.getenv("DEAL_OFFSITE_BACKUP_DIR", ""))
    return parser


def main(argv: list[str] | None = None) -> int:
    from utils.operational_readiness import (
        build_reconciliation_report,
        encrypt_and_replicate_backup,
        operational_status,
        run_restore_drill,
        write_reconciliation_csv,
    )

    args = _parser().parse_args(argv)
    if args.command == "status":
        result = operational_status(args.database, args.backup_dir, max_backup_age_hours=args.max_backup_age_hours)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    if args.command == "restore-drill":
        result = run_restore_drill(args.backup)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    if args.command == "reconcile":
        rows = build_reconciliation_report(args.database)
        output = args.output or str(ROOT / "reports" / f"reconciliation-{datetime.now():%Y%m%d-%H%M%S}.csv")
        target = write_reconciliation_csv(rows, output)
        print(json.dumps({"ok": True, "rows": len(rows), "issues": sum(bool(r["issues"]) for r in rows), "output": str(target)}, ensure_ascii=False))
        return 0
    if not args.destination_dir:
        print("ERROR: set DEAL_OFFSITE_BACKUP_DIR or --destination-dir", file=sys.stderr)
        return 2
    key = (os.getenv("DEAL_BACKUP_ENCRYPTION_KEY") or "").strip()
    if not key:
        print("ERROR: set DEAL_BACKUP_ENCRYPTION_KEY", file=sys.stderr)
        return 2
    target = encrypt_and_replicate_backup(args.backup, args.destination_dir, key)
    print(json.dumps({"ok": True, "output": str(target)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
