"""Benchmark local PaddleOCR on a receipt while redacting long identifiers."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from pathlib import Path


def _find_rec_texts(value) -> list[str]:
    if isinstance(value, dict):
        if isinstance(value.get("rec_texts"), list):
            return [str(x) for x in value["rec_texts"] if str(x).strip()]
        for child in value.values():
            found = _find_rec_texts(child)
            if found:
                return found
    if isinstance(value, (list, tuple)):
        for child in value:
            found = _find_rec_texts(child)
            if found:
                return found
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--crop", help="left,top,right,bottom")
    args = parser.parse_args()
    image_path = Path(args.image)
    temp_path: Path | None = None
    if args.crop:
        from PIL import Image

        box = tuple(int(x) for x in args.crop.split(","))
        with Image.open(image_path) as image:
            cropped = image.crop(box)
            handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            temp_path = Path(handle.name)
            handle.close()
            cropped.save(temp_path)
        image_path = temp_path

    from paddleocr import PaddleOCR

    started = time.perf_counter()
    ocr = PaddleOCR(
        lang="fa",
        ocr_version="PP-OCRv5",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
    )
    results = list(ocr.predict(str(image_path)))
    elapsed = time.perf_counter() - started
    texts: list[str] = []
    for result in results:
        payload = getattr(result, "json", result)
        if callable(payload):
            payload = payload()
        texts.extend(_find_rec_texts(payload))

    safe_lines: list[str] = []
    for line in texts:
        if "مبلغ" not in line and "ریال" not in line:
            line = re.sub(r"[0-9۰-۹٠-٩][0-9۰-۹٠-٩\s-]{6,}", "[redacted]", line)
        safe_lines.append(line)
    print(
        json.dumps(
            {"elapsed_sec": round(elapsed, 2), "line_count": len(texts), "lines": safe_lines},
            ensure_ascii=False,
            indent=2,
        )
    )
    if temp_path:
        temp_path.unlink(missing_ok=True)
    return 0 if texts else 1


if __name__ == "__main__":
    raise SystemExit(main())
