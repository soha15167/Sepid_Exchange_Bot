"""
utils/app_logging.py — Central logging setup / راه‌اندازی لاگ

EN: File + console logging for production debugging.
FA: لاگ فایل و کنسول برای عیب‌یابی روی سرور.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_app_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_sepid_logging_ready", False):
        return

    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)

    log_dir = (os.getenv("LOG_DIR") or "logs").strip()
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "bot.log")
        fh = RotatingFileHandler(
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
