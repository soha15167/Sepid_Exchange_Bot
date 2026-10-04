# banking_recognition

سیستم استخراج اطلاعات از عکس کارت و رسید بانکی ایران برای ربات صرافی سپید.

## خط لوله

1. پیش‌پردازش (OpenCV)
2. OCR: PaddleOCR → EasyOCR → Tesseract (موجود)
3. استخراج عمومی (regex + منطق پروژه)
4. اعتبارسنجی (Luhn، شبا MOD97، …)
5. امتیاز اطمینان
6. اگر confidence < 85 → **Gemini 3.6 Flash** (با راهنمای OCR)

## استفاده در ربات

```python
from banking_recognition import process_image

data = await process_image("/path/to/receipt.jpg")
print(data["amount"], data["confidence"], data["document_type"])
```

## env

```env
GEMINI_API_KEY=...
BANKING_GEMINI_MODEL=gemini-3.6-flash
BANKING_GEMINI_MODEL_FALLBACKS=gemini-3.5-flash-lite,gemini-3.1-flash-lite
BANKING_LLM_CONFIDENCE_THRESHOLD=85
BANKING_USE_PADDLE_OCR=1
BANKING_USE_EASYOCR=0
BANKING_GOOGLE_VISION_ENABLED=1
GOOGLE_APPLICATION_CREDENTIALS=/etc/sepid/google-vision.json
```

Google Cloud Vision independently reads Persian receipt text while Gemini
interprets layout and bank logos. When their amounts disagree, the admin draft
is blocked for review; neither engine silently overwrites the other.

## API

```bash
pip install -r banking_recognition/requirements-extra.txt
uvicorn banking_recognition.api.main:app --port 8090
curl -F file=@receipt.jpg http://127.0.0.1:8090/v1/process-image
```

## یکپارچه‌سازی

- `/txin` / `/txout`: فعلاً `handlers/iran_panel_sync.py` + `receipt_vision`
- حساب فروشنده در deal_gate: فعلاً فوروارد عکس — می‌توان با `BANKING_USE_IN_DEAL_GATE=1` وصل کرد

## نصب سبک (بدون Paddle)

فقط Tesseract + Gemini: `BANKING_USE_PADDLE_OCR=0` و `BANKING_USE_EASYOCR=0`
