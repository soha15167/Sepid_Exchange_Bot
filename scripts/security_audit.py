#!/usr/bin/env python3
"""Local, read-only security checks. Secret values are never printed."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run() -> dict:
    findings: list[dict[str, str]] = []
    env_path = ROOT / ".env"
    if env_path.exists() and os.name != "nt":
        mode = stat.S_IMODE(env_path.stat().st_mode)
        if mode & 0o077:
            findings.append({
                "severity": "high",
                "code": "env_permissions",
                "detail": f".env mode is {mode:o}; expected 600",
            })
    tracked = subprocess.run(
        ["git", "ls-files", ".env", "*.db", "*.sqlite", "*.sqlite3"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.stdout.strip():
        findings.append({
            "severity": "high",
            "code": "sensitive_file_tracked",
            "detail": "a local environment or database file is tracked by git",
        })
    return {
        "ok": not any(item["severity"] == "high" for item in findings),
        "findings": findings,
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 2)
