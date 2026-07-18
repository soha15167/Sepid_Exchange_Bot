import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts.backup_db import configured_backup_retention, create_verified_backup


class VerifiedDatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source.db"
        self.backup_dir = self.root / "backups"
        with closing(sqlite3.connect(self.source)) as database:
            database.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
            database.executemany(
                "INSERT INTO items (value) VALUES (?)",
                [("one",), ("two",), ("three",)],
            )
            database.commit()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_backup_is_complete_verified_and_private(self):
        destination = create_verified_backup(self.source, self.backup_dir)

        with closing(
            sqlite3.connect(f"{destination.as_uri()}?mode=ro", uri=True)
        ) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM items").fetchone()[0], 3)

        if os.name != "nt":
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.backup_dir.stat().st_mode & 0o777, 0o700)

    def test_retention_runs_only_after_successful_backups(self):
        start = datetime(2026, 7, 18, 12, 0, 0)
        for offset in range(3):
            create_verified_backup(
                self.source,
                self.backup_dir,
                keep=2,
                now=start + timedelta(seconds=offset),
            )

        backups = list(self.backup_dir.glob("source.db.*.bak"))
        self.assertEqual(len(backups), 2)

    def test_retention_can_be_configured_for_hourly_backups(self):
        with patch.dict(os.environ, {"BACKUP_KEEP": "168"}):
            self.assertEqual(configured_backup_retention(), 168)

    def test_invalid_retention_is_rejected(self):
        for value in ("0", "-1", "not-a-number"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"BACKUP_KEEP": value}):
                    with self.assertRaisesRegex(ValueError, "positive integer"):
                        configured_backup_retention()


if __name__ == "__main__":
    unittest.main()
