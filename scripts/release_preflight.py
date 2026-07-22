#!/usr/bin/env python3
"""Build a local release evidence record; does not deploy or restart anything."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def main() -> int:
    from scripts.security_audit import run as security_audit
    from utils.deal_operations import run_deal_verified_backup
    from utils.operational_readiness import run_restore_drill

    security = security_audit()
    backup = run_deal_verified_backup()
    restore = run_restore_drill(backup)
    evidence = {
        "ok": bool(security["ok"] and restore["ok"]),
        "created_at": int(time.time()),
        "git_revision": _git("rev-parse", "HEAD"),
        "git_status": _git("status", "--short").splitlines(),
        "verified_backup": str(backup),
        "restore_drill": restore,
        "security": security,
        "note": "No deployment or service restart was performed.",
    }
    output_dir = ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"release-preflight-{time.strftime('%Y%m%d-%H%M%S')}.json"
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target)
    return 0 if evidence["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
