"""لاگ پردازش تصاویر در SQLite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from banking_recognition.config import LOG_DB_PATH


def _ensure_db(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS banking_image_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_hash TEXT NOT NULL,
                ocr_text TEXT,
                parsed_json TEXT,
                confidence REAL,
                fraud_score REAL,
                processing_ms INTEGER,
                source TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_banking_logs_hash ON banking_image_logs(image_hash)"
        )
        conn.commit()


def image_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def log_processing(
    image_path: str,
    *,
    ocr_text: str,
    result_dict: dict,
    processing_ms: int,
) -> None:
    path = LOG_DB_PATH
    try:
        _ensure_db(path)
        ih = image_sha256(image_path)
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                INSERT INTO banking_image_logs (
                    image_hash, ocr_text, parsed_json, confidence, fraud_score,
                    processing_ms, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ih,
                    (ocr_text or "")[:20000],
                    json.dumps(result_dict, ensure_ascii=False)[:50000],
                    float(result_dict.get("confidence") or 0),
                    float(result_dict.get("fraud_score") or 0),
                    int(processing_ms),
                    str(result_dict.get("source") or ""),
                    int(time.time()),
                ),
            )
            conn.commit()
    except Exception:
        pass
