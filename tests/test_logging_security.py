import logging
import sys
import unittest

from utils.app_logging import RedactingFormatter, redact_sensitive_text


class TestLoggingRedaction(unittest.TestCase):
    def test_redacts_secrets_and_financial_pii(self):
        sensitive_values = (
            "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
            "token=super-secret-value",
            "IR120170000000100000000001",
            "6037 9975 1234 5678",
            "+989121234567",
            "person@example.com",
        )

        rendered = redact_sensitive_text(" | ".join(sensitive_values))

        for value in sensitive_values:
            self.assertNotIn(value, rendered)
        self.assertIn("[REDACTED_BOT_TOKEN]", rendered)
        self.assertIn("[REDACTED_IBAN]", rendered)
        self.assertIn("[REDACTED_CARD]", rendered)
        self.assertIn("[REDACTED_PHONE]", rendered)
        self.assertIn("[REDACTED_EMAIL]", rendered)

    def test_formatter_redacts_exception_traceback(self):
        token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
        formatter = RedactingFormatter("%(levelname)s %(message)s")
        try:
            raise RuntimeError(f"request failed for bot{token}")
        except RuntimeError:
            exc_info = sys.exc_info()
            record = logging.getLogger("test").makeRecord(
                "test",
                logging.ERROR,
                __file__,
                1,
                "request failed",
                (),
                exc_info=exc_info,
            )

        rendered = formatter.format(record)

        self.assertNotIn(token, rendered)
        self.assertIn("[REDACTED_BOT_TOKEN]", rendered)


if __name__ == "__main__":
    unittest.main()
