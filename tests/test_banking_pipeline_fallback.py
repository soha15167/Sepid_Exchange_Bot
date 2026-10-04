"""Regression coverage for the non-Gemini receipt fallback path."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from banking_recognition import pipeline


class BankingPipelineFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_ocr_fallback_serializes_dataclass_without_pydantic(self):
        with (
            patch.object(pipeline, "preprocess_image_path", return_value=("receipt.jpg", {})),
            patch.object(
                pipeline,
                "run_best_ocr",
                return_value=SimpleNamespace(text="", score=0, engine="test"),
            ),
            patch.object(pipeline, "extract_all_fields", return_value={}),
            patch.object(pipeline, "classify_document", return_value="UNKNOWN"),
            patch.object(pipeline, "cross_validate_fields", return_value=[]),
            patch.object(pipeline, "compute_confidence", return_value=0),
            patch.object(pipeline, "compute_fraud_score", return_value=0),
        ):
            result = await pipeline.run_pipeline("receipt.jpg", skip_gemini=True)

        self.assertEqual(result.source, "ocr")
        self.assertIsInstance(result.to_telegram_dict(), dict)


if __name__ == "__main__":
    unittest.main()
