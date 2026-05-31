#!/usr/bin/env python3
"""تست یک‌بارهٔ Vision API برای رسید — روی سرور بعد از شارژ OpenAI.

Usage:
  cd /root/telegram_bot_project2
  ./venv/bin/python3 scripts/test_receipt_openai_api.py /path/to/receipt.jpg
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not path or not Path(path).is_file():
        print("Usage: python3 scripts/test_receipt_openai_api.py RECEIPT.jpg")
        sys.exit(1)

    from config.settings import (
        RECEIPT_VISION_API_KEY,
        RECEIPT_VISION_BASE_URL,
        RECEIPT_VISION_ENABLED,
        RECEIPT_VISION_MODEL,
    )
    from utils.receipt_vision import extract_receipt_with_vision, receipt_vision_should_run

    print("enabled:", RECEIPT_VISION_ENABLED)
    print("model:", RECEIPT_VISION_MODEL)
    print("base:", RECEIPT_VISION_BASE_URL)
    print("key set:", bool(RECEIPT_VISION_API_KEY))
    print("will run vision:", receipt_vision_should_run())
    print("reading:", path)

    from handlers.iran_panel_sync import _coerce_vision_amount, _payload_from_vision

    data = await extract_receipt_with_vision(path, mode="out")
    if not data:
        print("FAIL: no data (check logs / billing / key)")
        sys.exit(2)
    print("OK raw JSON:", data)
    amt = _coerce_vision_amount(data.get("iran_amount"))
    payload = _payload_from_vision(data, "out")
    print("normalized amount:", f"{amt:,}" if amt else amt)
    print("panel payload:", payload)


if __name__ == "__main__":
    asyncio.run(main())
