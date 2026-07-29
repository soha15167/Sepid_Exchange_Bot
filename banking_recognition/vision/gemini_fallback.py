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
_last_gemini_failures: list[tuple[str, int]] = []



_EXTRACTION_PROMPT = """You extract structured data from Iranian bank receipts, transaction-detail screens, account activity/statement screenshots, payment confirmations, and bank documents.

The image may come from ANY Iranian bank or payment app. It may be cropped, low quality, dark/light themed, or an unknown layout. Do NOT assume a bank, app, or fixed template.



Return ONLY valid JSON (no markdown):

{

  "document_type": "BANK_CARD|BANK_RECEIPT|TRANSACTION_DETAIL|ACCOUNT_STATEMENT|SHEBA_DOCUMENT|ACCOUNT_INFORMATION|PAYMENT_CONFIRMATION|UNKNOWN",

  "bank_name": "",

  "card_number": "16 digits or empty",

  "sheba": "IR + 24 digits",

  "account_number": "",

  "owner_name": "",

  "sender_name": "",

  "receiver_name": "",

  "transfer_type": "Persian transaction type visible in image, or unknown",

  "source_bank": "",

  "destination_bank": "",

  "source_bank_evidence": "text|logo|card_bin|iban|unknown",

  "destination_bank_evidence": "text|logo|card_bin|iban|unknown",

  "detected_direction": "in|out|unknown",

  "amount": integer exactly as printed, without commas, or null,

  "currency": "rial|toman|unknown",

  "date": "YYYY/MM/DD Jalali if visible",

  "time": "HH:MM or HH:MM:SS",

  "tracking_number": "",

  "transaction_id": "",

  "status": "موفق|ناموفق|",

  "confidence": 0-100

}



Rules:

- amount: copy the transaction amount exactly as printed, NOT a balance, fee,
  tracking number, card number, account number, or reference number.
- currency: use rial for «ریال», toman for «تومان», otherwise unknown.
- detected_direction describes the account movement shown by the receipt:
  in for incoming/credit/واریز/بستانکار and out for outgoing/debit/برداشت/بدهکار.
- source_bank and sender_name belong to the paying/source account.
- destination_bank and receiver_name belong to the receiving/destination account.
- On receipts that show both «برداشت از حساب» and «واریز به حساب», read each
  section independently: sender_name is the person after «به نام» in the
  debited/withdrawal section, and receiver_name is the person after «به نام»
  in the credited/deposit section.
- A phrase such as «کوتاه مدت اشخاص حقیقی انفرادی» or «سپرده قرض الحسنه» is
  an account/product type, never a person's name.
- When two account holders are printed, do not put either name in owner_name;
  use sender_name and receiver_name for their exact transaction roles.
- For txout, the panel name field needs receiver_name (the destination account
  holder). Always identify it from the destination/«واریز به حساب» section;
  sender_name may be absent and is not required.
- Inspect logos next to source/destination account details. A clearly recognizable
  bank logo is valid evidence even when the bank name is not printed.
- Infer an Iranian bank from its card BIN or the three-digit bank code after the
  IBAN check digits. For example, IRxx016... is بانک کشاورزی.
- owner_name is the explicitly labelled account/card owner; do not infer it.
- A transfer screen is not successful unless a successful status is visibly shown.
- transfer_type may be کارت به کارت، پایا، ساتنا، پل، حساب به حساب، سپرده به سپرده،
  انتقال شبا، واریز/برداشت نقدی، خودپرداز، خرید، پرداخت اینترنتی، قبض، چک، حقوق،
  سود، کارمزد، برگشت وجه، برداشت مستقیم, or another exact visible Persian type.
- For account statements, use only the selected/clearly highlighted transaction row.
- A receipt that shows a debit «برداشت از حساب» and a credit «واریز به حساب»
  inside the same bank is حساب به حساب. Do not return a branded receipt title
  such as «انتقال وجه ملت» as the transaction type. In this case source_bank
  and destination_bank are the same bank even if the destination name is not
  printed a second time.
- Never infer a bank merely because an example bank is common.

- Persian and English text both possible.

- If unsure, use empty string or null."""





def get_last_gemini_http_status() -> int | None:

    return _last_gemini_http_status





def get_last_gemini_user_hint_fa() -> str:

    statuses = {status for _model, status in _last_gemini_failures}

    if 429 in statuses:

        return (

            "سرویس خواندن تصویر فعلاً به سقف درخواست رسیده است. "

            "چند دقیقه بعد دوباره امتحان کنید یا سهمیهٔ Gemini را بررسی کنید."

        )

    if _last_gemini_http_status == 429:

        return (

            "سقف درخواست Gemini پر شده (429).\n"

            "۱–۲ دقیقه صبر کنید و دوباره بفرستید، یا مبلغ را متنی بفرستید.\n"

            "در <a href=\"https://aistudio.google.com/apikey\">AI Studio</a> کلید "

            "<code>AIzaSy...</code> بسازید (نه کلید کوتاه دیگر)."

        )

    if statuses.intersection({401, 403}) or _last_gemini_http_status in (401, 403):

        return "کلید Gemini نامعتبر یا بدون دسترسی است — <code>GEMINI_API_KEY</code> را در .env عوض کنید."

    if statuses and statuses == {404}:

        return (

            "مدل Gemini پیدا نشد — در .env مثلاً "

            "<code>BANKING_GEMINI_MODEL=gemini-3.6-flash</code> بگذارید."

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

                _last_gemini_failures.append((model, 429))

                return None

            if resp.status_code >= 400:

                _last_gemini_http_status = resp.status_code

                logger.warning(

                    "banking_recognition gemini HTTP %s model=%s: %s",

                    resp.status_code,

                    model,

                    (resp.text or "")[:300],

                )

                _last_gemini_failures.append((model, resp.status_code))

                return None

            _last_gemini_http_status = None

            _last_gemini_failures.clear()

            return _parse_gemini_response(resp.json())

        except httpx.TimeoutException:

            logger.warning("banking_recognition gemini timeout model=%s", model)

            return None

        except Exception as e:

            logger.warning("banking_recognition gemini failed model=%s: %s", model, e)

            return None

    return None





async def extract_with_gemini(
    image_path: str, *, ocr_hint: str = "", mode: str = ""
) -> dict | None:

    global _last_gemini_http_status

    _last_gemini_failures.clear()

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
    elif path.suffix.lower() == ".pdf":

        mime = "application/pdf"

    b64 = base64.standard_b64encode(raw_bytes).decode("ascii")



    hint = ""

    if ocr_hint.strip():

        hint = f"\nOCR hint (may contain errors):\n{ocr_hint[:2500]}\n"

    mode_hint = ""
    if mode == "in":
        mode_hint = (
            "\nThis image was submitted through /txin. Extract an incoming credit only "
            "if the image itself supports that direction. The panel bank is the "
            "destination/credited bank and the depositor is the sender. If the image "
            "shows an outgoing debit, still report detected_direction=out.\n"
        )
    elif mode == "out":
        mode_hint = (
            "\nThis image was submitted through /txout. The panel bank and owner_name "
            "must describe the debited/source account; receiver_name and "
            "destination_bank describe the payee. If the image shows an incoming "
            "credit, still report detected_direction=in.\n"
        )



    body = {

        "contents": [

            {

                "parts": [

                    {"text": _EXTRACTION_PROMPT + mode_hint + hint},

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

            if _last_gemini_http_status in (401, 403):

                break

    return None
