"""
banking_recognition — استخراج اطلاعات از عکس کارت/رسید بانکی ایران.

استفاده در ربات:
    from banking_recognition import process_image
    result = await process_image("/path/to.jpg")
"""

from banking_recognition.service import (
    process_image,
    process_image_for_receipt,
    process_image_sync,
)

__all__ = ["process_image", "process_image_for_receipt", "process_image_sync"]
