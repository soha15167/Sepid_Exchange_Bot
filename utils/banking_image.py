"""
utils/banking_image.py — رابط ساده برای ربات.

    from utils.banking_image import recognize_banking_image
    data = await recognize_banking_image(path)
"""

from __future__ import annotations

from typing import Any


async def recognize_banking_image(image_path: str) -> dict[str, Any]:
    from banking_recognition import process_image

    return await process_image(image_path)


def format_account_from_recognition(data: dict[str, Any]) -> str:
    """متن حساب برای ثبت در deal_gate از خروجی recognition."""
    lines: list[str] = []
    if data.get("owner_name"):
        lines.append(f"نام: {data['owner_name']}")
    if data.get("card_number"):
        lines.append(f"کارت: {data['card_number']}")
    if data.get("sheba"):
        lines.append(f"شبا: {data['sheba']}")
    if data.get("bank_name"):
        lines.append(f"بانک: {data['bank_name']}")
    if not lines and data.get("raw_text"):
        return (data.get("raw_text") or "")[:2000]
    return "\n".join(lines)[:2000]
