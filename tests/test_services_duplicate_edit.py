import asyncio
import unittest

from telegram.error import BadRequest

from handlers.services import _edit_service_payment_selection


class _FailingQuery:
    def __init__(self, error: BadRequest):
        self.error = error
        self.calls = 0

    async def edit_message_text(self, *args, **kwargs):
        self.calls += 1
        raise self.error


class ServiceDuplicateEditTests(unittest.TestCase):
    def test_message_not_modified_is_treated_as_success(self):
        query = _FailingQuery(BadRequest("Message is not modified"))

        asyncio.run(_edit_service_payment_selection(query, "خرید"))

        self.assertEqual(query.calls, 1)

    def test_other_bad_request_is_not_hidden(self):
        query = _FailingQuery(BadRequest("Message to edit not found"))

        with self.assertRaisesRegex(BadRequest, "Message to edit not found"):
            asyncio.run(_edit_service_payment_selection(query, "فروش"))

        self.assertEqual(query.calls, 1)


if __name__ == "__main__":
    unittest.main()
