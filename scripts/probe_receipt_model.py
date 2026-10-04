"""Safely probe receipt extraction without printing API keys or account identifiers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", nargs="?")
    parser.add_argument("--mode", choices=("in", "out"))
    parser.add_argument("--model-only", action="store_true")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--ocr-only", action="store_true")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--fallbacks", default="")
    args = parser.parse_args()

    if args.env_file:
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=True)
    if args.model:
        os.environ["BANKING_GEMINI_MODEL"] = args.model
    if args.fallbacks:
        os.environ["BANKING_GEMINI_MODEL_FALLBACKS"] = args.fallbacks
    if args.ocr_only:
        os.environ["BANKING_GEMINI_ENABLED"] = "0"

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    if args.model_only or args.list_models:
        import httpx
        import signal

        key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or os.getenv("BANKING_GEMINI_API_KEY")
            or ""
        ).strip()
        if not key:
            print(json.dumps({"ok": False, "error": "missing_key"}))
            return 1
        model = args.model or os.getenv("BANKING_GEMINI_MODEL") or "gemini-3.6-flash"
        if args.list_models:
            response = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": key},
                timeout=httpx.Timeout(20, connect=10),
            )
            available = {
                str(item.get("name") or "").removeprefix("models/")
                for item in response.json().get("models", [])
                if "generateContent" in (item.get("supportedGenerationMethods") or [])
            } if response.is_success else set()
            print(json.dumps({"ok": response.is_success, "model": model, "available": model in available, "status": response.status_code}))
            return 0 if response.is_success and model in available else 1
        signal.alarm(25)
        try:
            response = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": key},
                json={
                    "contents": [{"parts": [{"text": "Return only JSON: {\"ok\": true}"}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
                timeout=httpx.Timeout(20, connect=10),
            )
        finally:
            signal.alarm(0)
        print(json.dumps({"ok": response.is_success, "model": model, "status": response.status_code}))
        return 0 if response.is_success else 1

    if not args.image or not args.mode:
        parser.error("image and --mode are required unless --model-only is used")

    from banking_recognition import process_image_for_receipt
    from banking_recognition.vision.gemini_fallback import get_last_gemini_http_status

    result = asyncio.run(process_image_for_receipt(args.image, mode=args.mode))
    safe = {
        key: result.get(key)
        for key in (
            "source",
            "document_type",
            "amount",
            "date",
            "bank_name",
            "transfer_type",
            "status",
            "confidence",
            "validation_errors",
            "meta",
        )
    }
    safe["last_http_status"] = get_last_gemini_http_status()
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0 if result.get("source") in ("gemini", "ocr+gemini") else 1


if __name__ == "__main__":
    raise SystemExit(main())
