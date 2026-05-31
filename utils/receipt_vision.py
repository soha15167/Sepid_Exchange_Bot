"""
utils/receipt_vision.py — استخراج فیلد رسید با مدل بینایی (OpenAI-compatible).

نیاز: OPENAI_API_KEY در .env (یا RECEIPT_VISION_API_KEY)
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def receipt_vision_available() -> bool:
    from config.settings import RECEIPT_VISION_API_KEY, RECEIPT_VISION_ENABLED

    return RECEIPT_VISION_ENABLED and bool(RECEIPT_VISION_API_KEY)


def _is_ollama_backend(base_url: str) -> bool:
    u = (base_url or "").lower()
    return "11434" in u or "ollama" in u


def receipt_vision_uses_ollama() -> bool:
    if not receipt_vision_available():
        return False
    from config.settings import RECEIPT_VISION_BASE_URL

    return _is_ollama_backend(RECEIPT_VISION_BASE_URL)


def receipt_vision_should_run() -> bool:
    """روی Ollama/CPU معمولاً خیلی کند است — مگر RECEIPT_VISION_USE_OLLAMA=1."""
    if not receipt_vision_available():
        return False
    if receipt_vision_uses_ollama():
        from config.settings import RECEIPT_VISION_USE_OLLAMA

        return RECEIPT_VISION_USE_OLLAMA
    return True


def _vision_prompt(mode: str) -> str:
    kind_en = "deposit (txin)" if mode == "in" else "withdrawal/transfer (txout)"
    month_hint = (
        "فروردین=01 اردیبهشت=02 خرداد=03 تیر=04 مرداد=05 شهریور=06 "
        "مهر=07 آبان=08 آذر=09 دی=10 بهمن=11 اسفند=12"
    )
    if mode == "out":
        name_fields = """
  "recipient_name": "withdrawer / account holder at TOP (صاحب حساب — NOT انتقال دهنده)",
  "sender_name": "only «انتقال دهنده» if shown, else null",
  "depositor_name": "same as recipient_name (panel: نام برداشت‌کننده)","""
    else:
        name_fields = """
  "depositor_name": "depositor / واریزکننده name","""

    return f"""You read Iranian bank app receipt screenshots (Baam/BMI, Blu, Saman logos, dark/light UI).
Transaction: {kind_en}.

Return ONLY valid JSON:
{{
  "iran_amount": integer Rials, no commas (e.g. 58800000 for «58,800,000 ریال» — do NOT add extra zeros),
  "jdate": "YYYY/MM/DD from «زمان» line; {month_hint}",
  "bank_name": "SOURCE bank from logo/app (Baam/bmi.ir → ملی, Blu app → بلو)",
  "dest_bank": "DESTINATION bank from card logo or «به کارت» BIN (621986→سامان, 603799→ملی) or null",{name_fields}
  "transfer_type": "e.g. کارت به کارت for Baam card transfer",
  "description": null
}}

Rules:
- iran_amount: «مبلغ» / «ریال» line ONLY — NOT tracking number.
- Do NOT add trailing zero (58800000 not 588000000).
- Baam (baam.bmi.ir) receipts: bank_name=ملی, transfer_type=کارت به کارت; dest from «به کارت» logo/BIN.
- dest_bank must be bank name (سامان، ملت…) — never the word «کارت» alone.
- jdate: month from Persian month NAME (خرداد → 03), not the day number.
- For txout: recipient_name is the prominent top name; sender is انتقال دهنده only.
- Use null if unsure."""


_AMOUNT_JDATE_RETRY_PROMPT = """Iranian bank transfer receipt (any bank app, any layout).
Return ONLY JSON:
{"iran_amount": integer Rials from «مبلغ … ریال» ONLY (Persian digits OK; e.g. 58800000 for «۵۸,۸۰۰,۰۰۰ ریال» — do NOT add extra zero),
 "jdate": "YYYY/MM/DD from «زمان» (خرداد=03, شهریور=06 — use month NAME not day)"}
If unreadable use null."""


def _vision_parsed_amount_ok(parsed: dict) -> bool:
    try:
        from utils.receipt_amount import normalize_transfer_amount, parse_rial_amount_text

        v = parsed.get("iran_amount")
        if v is None:
            return False
        if isinstance(v, str):
            n = parse_rial_amount_text(v)
        else:
            n = normalize_transfer_amount(int(v))
        return n >= 1_000_000
    except (TypeError, ValueError):
        return False


def _normalize_vision_parsed(parsed: dict | None) -> dict | None:
    if not parsed:
        return parsed
    from utils.receipt_amount import normalize_transfer_amount, parse_rial_amount_text
    from utils.iran_digits import normalize_digits

    raw = parsed.get("iran_amount")
    if raw is None:
        return parsed
    if isinstance(raw, str):
        s = raw.strip()
        if s.lower() in ("", "null", "none", "-"):
            parsed.pop("iran_amount", None)
            return parsed
        n = parse_rial_amount_text(s)
        if not n:
            digits = re.sub(r"\D", "", normalize_digits(s))
            n = normalize_transfer_amount(int(digits)) if digits else 0
    else:
        n = normalize_transfer_amount(int(raw))
    if n >= 1_000_000:
        parsed["iran_amount"] = n
    else:
        parsed.pop("iran_amount", None)
    return parsed


def _parse_json_object(text: str) -> dict | None:
    s = (text or "").strip()
    if not s:
        return None
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        s = m.group(0)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _image_media_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in (".png",):
        return "image/png"
    if ext in (".webp",):
        return "image/webp"
    return "image/jpeg"


def _openai_model_profile(model: str) -> str:
    """gpt-5 / o-series: بدون temperature؛ gpt-4o: کلاسیک."""
    m = (model or "").lower()
    if "gpt-5" in m or re.match(r"^o[1-4](-|$)", m):
        return "reasoning"
    return "classic"


def _build_vision_request_body(
    *,
    model: str,
    prompt: str,
    b64: str,
    mime: str,
    ollama: bool,
    json_mode: bool = True,
) -> dict:
    image_part: dict = {"url": f"data:{mime};base64,{b64}"}
    if not ollama:
        profile = _openai_model_profile(model)
        image_part["detail"] = "original" if profile == "reasoning" else "high"
    body: dict = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": image_part},
                ],
            }
        ],
    }
    if ollama:
        body["options"] = {"num_predict": 350, "temperature": 0}
        return body
    profile = _openai_model_profile(model)
    if profile == "classic":
        body["temperature"] = 0
        if json_mode:
            body["response_format"] = {"type": "json_object"}
    else:
        body["max_completion_tokens"] = 1200
    return body


def _image_bytes_for_api(path: str, *, max_side: int = 1024) -> tuple[bytes, str]:
    """کوچک‌کردن تصویر برای Ollama روی CPU — سریع‌تر و کم‌حافظه‌تر."""
    try:
        from io import BytesIO

        from PIL import Image  # type: ignore

        im = Image.open(path).convert("RGB")
        w, h = im.size
        longest = max(w, h)
        if longest > max_side:
            scale = max_side / longest
            im = im.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.debug("receipt_vision: resize skipped: %s", e)
        return Path(path).read_bytes(), _image_media_type(path)


async def extract_receipt_with_vision(
    image_path: str,
    *,
    mode: str,
) -> dict | None:
    """
    استخراج ساختاریافته از رسید. در خطا None برمی‌گرداند.
    """
    from config.settings import (
        RECEIPT_VISION_API_KEY,
        RECEIPT_VISION_BASE_URL,
        RECEIPT_VISION_MODEL,
        RECEIPT_VISION_TIMEOUT_SEC,
    )

    if not RECEIPT_VISION_API_KEY or not image_path or not Path(image_path).is_file():
        return None

    ollama = _is_ollama_backend(RECEIPT_VISION_BASE_URL)
    try:
        if ollama:
            raw_bytes, mime = _image_bytes_for_api(image_path, max_side=1024)
        else:
            raw_bytes, mime = _image_bytes_for_api(image_path, max_side=1280)
    except OSError as e:
        logger.warning("receipt_vision: read failed: %s", e)
        return None

    b64 = base64.standard_b64encode(raw_bytes).decode("ascii")
    url = f"{RECEIPT_VISION_BASE_URL.rstrip('/')}/chat/completions"
    ollama = _is_ollama_backend(RECEIPT_VISION_BASE_URL)
    body = _build_vision_request_body(
        model=RECEIPT_VISION_MODEL,
        prompt=_vision_prompt(mode),
        b64=b64,
        mime=mime,
        ollama=ollama,
        json_mode=True,
    )

    headers = {
        "Authorization": f"Bearer {RECEIPT_VISION_API_KEY}",
        "Content-Type": "application/json",
    }

    import asyncio

    timeout = httpx.Timeout(RECEIPT_VISION_TIMEOUT_SEC)

    async def _post_once(req_body: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, headers=headers, json=req_body)

    async def _post_with_retries(req_body: dict) -> httpx.Response | None:
        delays = (0, 3, 8)
        resp: httpx.Response | None = None
        for i, delay in enumerate(delays):
            if delay:
                logger.warning("receipt_vision: rate limited, retry in %ss", delay)
                await asyncio.sleep(delay)
            resp = await _post_once(req_body)
            if resp.status_code != 429:
                return resp
        return resp

    def _content_from_resp(resp: httpx.Response) -> str:
        data = resp.json()
        return (
            (data.get("choices") or [{}])[0]
            .get("message", {})
            .get("content", "")
        )

    async def _request(body_in: dict) -> httpx.Response | None:
        resp = await _post_with_retries(body_in)
        if resp is None or resp.status_code < 400:
            return resp
        err_body = (resp.text or "")[:800]
        logger.warning("receipt_vision: api %s %s", resp.status_code, err_body)
        if resp.status_code != 400 or ollama:
            return resp
        fallback = dict(body_in)
        fallback.pop("temperature", None)
        fallback.pop("response_format", None)
        if "max_completion_tokens" not in fallback:
            fallback["max_completion_tokens"] = 1200
        img = fallback["messages"][0]["content"][1]["image_url"]
        if isinstance(img, dict) and img.get("detail") == "high":
            img["detail"] = "auto"
        logger.info("receipt_vision: retrying without unsupported params")
        return await _post_with_retries(fallback)

    try:
        resp = await _request(body)
        if resp is None or resp.status_code >= 400:
            if resp is not None:
                err_body = (resp.text or "")[:800]
                if "model" in err_body.lower() and (
                    "not found" in err_body.lower() or "does not exist" in err_body.lower()
                ):
                    logger.error(
                        "receipt_vision: مدل %s در API نیست — "
                        "RECEIPT_VISION_MODEL=gpt-4o-mini یا gpt-4o",
                        RECEIPT_VISION_MODEL,
                    )
                if "system memory" in err_body.lower() or "more memory" in err_body.lower():
                    logger.error(
                        "receipt_vision: RAM کافی نیست — RECEIPT_VISION_ENABLED=0"
                    )
            return None
        parsed = _parse_json_object(_content_from_resp(resp))
        if (
            parsed
            and not ollama
            and not _vision_parsed_amount_ok(parsed)
        ):
            retry_body = _build_vision_request_body(
                model=RECEIPT_VISION_MODEL,
                prompt=_AMOUNT_JDATE_RETRY_PROMPT,
                b64=b64,
                mime=mime,
                ollama=ollama,
                json_mode=True,
            )
            resp2 = await _post_with_retries(retry_body)
            if resp2 and resp2.status_code < 400:
                extra = _parse_json_object(_content_from_resp(resp2))
                if extra:
                    for key in ("iran_amount", "jdate"):
                        val = extra.get(key)
                        if val is not None and str(val).strip().lower() not in (
                            "",
                            "null",
                            "none",
                        ):
                            parsed[key] = val
                    logger.info(
                        "receipt_vision: amount retry amount=%s jdate=%s",
                        parsed.get("iran_amount"),
                        parsed.get("jdate"),
                    )
        parsed = _normalize_vision_parsed(parsed)
        if parsed:
            logger.info(
                "receipt_vision: ok amount=%s jdate=%s",
                parsed.get("iran_amount"),
                parsed.get("jdate"),
            )
        return parsed
    except Exception as e:
        logger.warning("receipt_vision: request failed: %s", e)
        return None
