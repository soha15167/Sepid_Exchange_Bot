"""
utils/app_logging.py — Central logging setup / راه‌اندازی لاگ

EN: File + console logging for production debugging.
FA: لاگ فایل و کنسول برای عیب‌یابی روی سرور.
"""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler


_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)(?<!\d)(?:bot)?\d{6,12}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"),
        "[REDACTED_BOT_TOKEN]",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password)=([^&\s]+)"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"(?i)\bIR(?:[\s-]*\d){24}\b"),
        "[REDACTED_IBAN]",
    ),
    (
        re.compile(r"(?<!\d)(?:\d[ -]?){15}\d(?!\d)"),
        "[REDACTED_CARD]",
    ),
    (
        re.compile(r"(?<!\d)(?:\+?98|0098|0)?9\d{9}(?!\d)"),
        "[REDACTED_PHONE]",
    ),
    (
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
)


def redact_sensitive_text(value: object) -> str:
    """Return a log-safe representation of common secrets and financial PII."""
    text = str(value)
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Redact the complete rendered record, including exception tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Keep current and newly rotated log files private on POSIX systems."""

    def _open(self):
        stream = super()._open()
        if os.name != "nt":
            try:
                os.chmod(self.baseFilename, 0o600)
            except OSError:
                pass
        return stream


def setup_app_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_sepid_logging_ready", False):
        return

    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    # httpx logs the complete Telegram API URL at INFO, including the bot token.
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    fmt = RedactingFormatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)

    log_dir = (os.getenv("LOG_DIR") or "logs").strip()
    try:
        os.makedirs(log_dir, mode=0o700, exist_ok=True)
        if os.name != "nt":
            os.chmod(log_dir, 0o700)
        log_path = os.path.join(log_dir, "bot.log")
        fh = PrivateRotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        logging.getLogger(__name__).warning("File logging disabled: %s", exc)

    root._sepid_logging_ready = True  # type: ignore[attr-defined]
