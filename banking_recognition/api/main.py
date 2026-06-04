"""
FastAPI service — اجرا:
  uvicorn banking_recognition.api.main:app --host 0.0.0.0 --port 8090
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

from banking_recognition.service import process_image

app = FastAPI(
    title="Sepid Banking Image Recognition",
    version="1.0.0",
    description="Persian banking card & receipt OCR + validation + Gemini fallback",
)


@app.get("/health")
async def health():
    from banking_recognition.config import GEMINI_ENABLED, USE_PADDLE_OCR

    return {
        "status": "ok",
        "gemini": GEMINI_ENABLED,
        "paddle": USE_PADDLE_OCR,
    }


@app.post("/v1/process-image")
async def process_image_endpoint(file: UploadFile = File(...)):
    suffix = Path(file.filename or "img.jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        data = await file.read()
        tmp.write(data)
        path = tmp.name
    try:
        result = await process_image(path)
        return JSONResponse(result)
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
