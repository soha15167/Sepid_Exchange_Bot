"""Gemini Vision — استخراج فیلد از رسید/کارت (با retry روی 429)."""



from __future__ import annotations



import asyncio

import base64

import json

import logging

import re

from pathlib import Path



import httpx



from banking_recognition.config import (

    GEMINI_API_KEY,

    GEMINI_ENABLED,

    GEMINI_MAX_RETRIES,

    GEMINI_MODEL,

    GEMINI_MODEL_FALLBACKS,

    GEMINI_RETRY_BASE_SEC,

    GEMINI_TIMEOUT_SEC,

)



logger = logging.getLogger(__name__)



# آخرین خطای API برای پیام کاربر (بدون ذخیرهٔ کلید)

_last_gemini_http_status: int | None = None



_EXTRACTION_PROMPT = """You extract structured data from Iranian bank card photos, payment receipts, and banking app screenshots.

The image may be cropped, low quality, or an unknown bank layout. Do NOT assume a fixed template.



Return ONLY valid JSON (no markdown):

{

  "document_type": "BANK_CARD|BANK_RECEIPT|SHEBA_DOCUMENT|ACCOUNT_INFORMATION|PAYMENT_CONFIRMATION|UNKNOWN",

  "bank_name": "",

  "card_number": "16 digits or empty",

  "sheba": "IR + 24 digits",

  "account_number": "",

  "owner_name": "",

  "sender_name": "",

  "receiver_name": "",

  "amount": integer Rials without commas or null,

  "date": "YYYY/MM/DD Jalali if visible",

  "time": "HH:MM or HH:MM:SS",

  "tracking_number": "",

  "transaction_id": "",

  "status": "موفق|ناموفق|",

  "confidence": 0-100

}



Rules:

- iran_amount / amount: from «مبلغ» or «ریال» line only, NOT tracking number.

- Persian and English text both possible.

- If unsure, use empty string or null."""





def get_last_gemini_http_status() -> int | None:

    return _last_gemini_http_status





def get_last_gemini_user_hint_fa() -> str:

    if _last_gemini_http_status == 429:

        return (

            "سقف درخواست Gemini پر شده (429).\n"

            "۱–۲ دقیقه صبر کنید و دوباره بفرستید، یا مبلغ را متنی بفرستید.\n"

            "در <a href=\"https://aistudio.google.com/apikey\">AI Studio</a> کلید "

            "<code>AIzaSy...</code> بسازید (نه کلید کوتاه دیگر)."

        )

    if _last_gemini_http_status in (401, 403):

        return "کلید Gemini نامعتبر یا بدون دسترسی است — <code>GEMINI_API_KEY</code> را در .env عوض کنید."

    if _last_gemini_http_status == 404:

        return (

            "مدل Gemini پیدا نشد — در .env مثلاً "

            "<code>BANKING_GEMINI_MODEL=gemini-2.0-flash-lite</code> بگذارید."

        )

    return ""





def _models_to_try() -> list[str]:

    models: list[str] = []

    for name in (GEMINI_MODEL, *GEMINI_MODEL_FALLBACKS):

        n = (name or "").strip()

        if n and n not in models:

            models.append(n)

    return models





def _retry_delay_sec(response: httpx.Response, attempt: int) -> float:

    ra = (response.headers.get("retry-after") or "").strip()

    if ra.isdigit():

        return min(float(ra), 45.0)

    try:

        return min(float(ra), 45.0)

    except ValueError:

        pass

    return min(GEMINI_RETRY_BASE_SEC * (2**attempt), 30.0)





def _parse_gemini_response(data: dict) -> dict | None:

    try:

        text = data["candidates"][0]["content"]["parts"][0]["text"]

    except (KeyError, IndexError, TypeError):

        logger.warning("banking_recognition gemini bad response shape")

        return None



    text = (text or "").strip()

    if text.startswith("```"):

        text = re.sub(r"^```(?:json)?\s*", "", text)

        text = re.sub(r"\s*```$", "", text)

    try:

        parsed = json.loads(text)

        return parsed if isinstance(parsed, dict) else None

    except json.JSONDecodeError:

        logger.warning("banking_recognition gemini json parse failed")

        return None





async def _call_model(

    client: httpx.AsyncClient,

    *,

    model: str,

    body: dict,

) -> dict | None:

    global _last_gemini_http_status

    url = (

        f"https://generativelanguage.googleapis.com/v1beta/models/"

        f"{model}:generateContent"

    )

    params = {"key": GEMINI_API_KEY}



    for attempt in range(GEMINI_MAX_RETRIES):

        try:

            resp = await client.post(url, params=params, json=body)

            if resp.status_code == 429:

                _last_gemini_http_status = 429

                delay = _retry_delay_sec(resp, attempt)

                logger.warning(

                    "banking_recognition gemini 429 model=%s attempt=%s wait=%.1fs",

                    model,

                    attempt + 1,

                    delay,

                )

                if attempt + 1 < GEMINI_MAX_RETRIES:

                    await asyncio.sleep(delay)

                    continue

                return None

            if resp.status_code >= 400:

                _last_gemini_http_status = resp.status_code

                logger.warning(

                    "banking_recognition gemini HTTP %s model=%s: %s",

                    resp.status_code,

                    model,

                    (resp.text or "")[:300],

                )

                return None

            _last_gemini_http_status = None

            return _parse_gemini_response(resp.json())

        except httpx.TimeoutException:

            logger.warning("banking_recognition gemini timeout model=%s", model)

            return None

        except Exception as e:

            logger.warning("banking_recognition gemini failed model=%s: %s", model, e)

            return None

    return None





async def extract_with_gemini(image_path: str, *, ocr_hint: str = "") -> dict | None:

    global _last_gemini_http_status

    if not GEMINI_ENABLED or not GEMINI_API_KEY:

        return None

    path = Path(image_path)

    if not path.is_file():

        return None



    try:

        raw_bytes = path.read_bytes()

    except OSError as e:

        logger.warning("gemini: read failed: %s", e)

        return None



    mime = "image/jpeg"

    if path.suffix.lower() in (".png",):

        mime = "image/png"

    b64 = base64.standard_b64encode(raw_bytes).decode("ascii")



    hint = ""

    if ocr_hint.strip():

        hint = f"\nOCR hint (may contain errors):\n{ocr_hint[:2500]}\n"



    body = {

        "contents": [

            {

                "parts": [

                    {"text": _EXTRACTION_PROMPT + hint},

                    {

                        "inline_data": {

                            "mime_type": mime,

                            "data": b64,

                        }

                    },

                ]

            }

        ],

        "generationConfig": {

            "temperature": 0.1,

            "responseMimeType": "application/json",

        },

    }



    models = _models_to_try()

    async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT_SEC) as client:

        for model in models:

            parsed = await _call_model(client, model=model, body=body)

            if parsed:

                if model != GEMINI_MODEL:

                    logger.info("banking_recognition gemini ok via fallback model=%s", model)

                return parsed

            if _last_gemini_http_status not in (429, None):

                break

    return None


