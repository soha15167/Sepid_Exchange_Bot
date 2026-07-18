#!/usr/bin/env python3
"""Run Sepid web API on WEB_API_PORT (default 8100 — avoids Iran panel on 8000)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from config.settings import WEB_API_HOST, WEB_API_PORT


def main() -> None:
    uvicorn.run(
        "web_api.main:app",
        host=WEB_API_HOST,
        port=WEB_API_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
