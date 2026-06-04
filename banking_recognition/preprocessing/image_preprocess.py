"""پیش‌پردازش تصویر با OpenCV (در صورت نصب)."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def preprocess_image_path(image_path: str) -> tuple[str, dict]:
    """
    مسیر تصویر پیش‌پردازش‌شده را برمی‌گرداند.
    اگر OpenCV نباشد همان مسیر ورودی.
    """
    path = Path(image_path)
    if not path.is_file():
        return image_path, {"opencv": False, "reason": "missing_file"}

    try:
        import cv2
        import numpy as np
    except ImportError:
        return image_path, {"opencv": False, "reason": "no_cv2"}

    try:
        img = cv2.imread(str(path))
        if img is None:
            return image_path, {"opencv": False, "reason": "imread_failed"}

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        max_side = 2000
        if max(h, w) > max_side:
            scale = max_side / float(max(h, w))
            gray = cv2.resize(
                gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
            )

        gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        gray = cv2.convertScaleAbs(gray, alpha=1.15, beta=8)
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        gray = cv2.filter2D(gray, -1, kernel)

        coords = np.column_stack(np.where(gray > 0))
        if coords.size > 0:
            angle = cv2.minAreaRect(coords)[-1]
            if angle < -45:
                angle = 90 + angle
            elif angle > 45:
                angle = angle - 90
            if abs(angle) > 0.5:
                M = cv2.getRotationMatrix2D(
                    (gray.shape[1] / 2, gray.shape[0] / 2), angle, 1.0
                )
                gray = cv2.warpAffine(
                    gray,
                    M,
                    (gray.shape[1], gray.shape[0]),
                    flags=cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_REPLICATE,
                )

        out = path.parent / f".br_{path.stem}.png"
        cv2.imwrite(str(out), gray)
        return str(out), {"opencv": True, "rotated": True}
    except Exception as e:
        logger.warning("banking_recognition preprocess failed: %s", e)
        return image_path, {"opencv": False, "reason": str(e)}
