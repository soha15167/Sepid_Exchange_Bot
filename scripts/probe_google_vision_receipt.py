"""Privacy-minimized Google Vision receipt probe for staging."""

from __future__ import annotations

import argparse
import json
import os
import tempfile

from PIL import Image

from banking_recognition.extractors.universal_extractor import extract_all_fields
from banking_recognition.ocr.google_vision import run_google_vision_ocr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--crop", help="left,top,right,bottom")
    args = parser.parse_args()

    image_path = args.image
    cropped_path = ""
    if args.crop:
        box = tuple(int(value) for value in args.crop.split(","))
        if len(box) != 4:
            raise ValueError("crop must be left,top,right,bottom")
        with Image.open(args.image) as source:
            cropped = source.crop(box)
            handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            cropped_path = handle.name
            handle.close()
            cropped.save(cropped_path)
        image_path = cropped_path

    try:
        run = run_google_vision_ocr(image_path)
    finally:
        if cropped_path:
            try:
                os.remove(cropped_path)
            except OSError:
                pass
    if not run:
        print(json.dumps({"ok": False}))
        return 1
    fields = extract_all_fields(run.text)
    print(
        json.dumps(
            {
                "ok": True,
                "engine": run.engine,
                "score": round(run.score, 1),
                "text_chars": len(run.text),
                "amount": fields.get("amount"),
                "bank": fields.get("bank_name"),
                "date": fields.get("date"),
                "has_sheba": bool(fields.get("sheba")),
                "has_tracking": bool(fields.get("tracking_number")),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
