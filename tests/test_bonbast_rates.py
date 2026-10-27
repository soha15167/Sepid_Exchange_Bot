"""Combined Bonbast market posts use one snapshot and one channel message."""

from __future__ import annotations

import html
import re
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import main as bot_main
from handlers import bonbast_daily
from utils import bonbast_rates


CHANNEL_ID = -100123456


def market_snapshot():
    """Synthetic prices with the published client's actual Bonbast field names."""
    return {
        "usd1": "100001", "usd2": "99999",
        "eur1": "200003", "eur2": "199997",
        "gbp1": "300007", "gbp2": "299993",
        "gol18": "9876543", "mithqal": "42345678", "ounce": "4140.61",
        "emami1": "111111111", "emami12": "101010101",
        "azadi1": "88888888", "azadi12": "77777777",
        "azadi1_2": "66666666", "azadi1_22": "55555555",
        "azadi1_4": "44444444", "azadi1_42": "33333333",
        "azadi1g": "22222222", "azadi1g2": "11111111",
        # These plausible but incorrect inferred keys must not be used.
        "emami2": "909090909", "azadi2": "808080808",
    }


def visible_text(text):
    return html.unescape(re.sub(r"<[^>]*>", "", text))


def quote_line(text, label):
    return next(line for line in visible_text(text).splitlines() if label in line)


class BonbastFormatterTests(unittest.TestCase):
    def setUp(self):
        footer = patch("utils.channel_format.format_channel_ad_footer", return_value="\n\nTest footer")
        footer.start()
        self.addCleanup(footer.stop)

    def test_verified_coin_pairs_are_rendered_as_sell_then_buy(self):
        text = bonbast_rates.format_bonbast_channel_html(
            market_snapshot(), currency_codes=["eur", "usd"],
        )
        self.assertIn("ارز، طلا و سکه", visible_text(text))
        for label, sell, buy in (
            ("امامی", "111,111,111", "101,010,101"),
            ("بهار آزادی", "88,888,888", "77,777,777"),
            ("نیم", "66,666,666", "55,555,555"),
            ("ربع", "44,444,444", "33,333,333"),
            ("گرمی", "22,222,222", "11,111,111"),
        ):
            with self.subTest(label=label):
                line = quote_line(text, label)
                self.assertIn(sell, line)
                self.assertIn(buy, line)
                self.assertLess(line.index(sell), line.index(buy))
        self.assertNotIn("909,090,909", text)
        self.assertNotIn("808,080,808", text)
        self.assertIn("فروش", text)
        self.assertIn("خرید", text)

    def test_gold_units_and_ounce_decimal_remain_distinct(self):
        text = bonbast_rates.format_bonbast_channel_html(market_snapshot(), currency_codes=["usd"])
        gram = quote_line(text, "گرم")
        self.assertRegex(gram, r"(?:۱۸|18)")
        self.assertIn("9,876,543", gram)
        self.assertIn("تومان", gram)
        mithqal = quote_line(text, "مثقال")
        self.assertIn("42,345,678", mithqal)
        self.assertIn("تومان", mithqal)
        ounce = quote_line(text, "اونس")
        self.assertIn("4140.61", ounce.replace(",", ""))
        self.assertRegex(ounce, r"(?i)(?:دلار|USD)")
        self.assertNotIn("تومان", ounce)

    def test_currency_filter_and_order_do_not_filter_metals(self):
        text = bonbast_rates.format_bonbast_channel_html(
            market_snapshot(), currency_codes=["eur", "usd"],
        )
        plain = visible_text(text)
        self.assertLess(plain.index("یورو"), plain.index("دلار آمریکا"))
        self.assertIn("200,003", quote_line(text, "یورو"))
        self.assertIn("100,001", quote_line(text, "دلار آمریکا"))
        self.assertNotIn("پوند انگلیس", plain)
        self.assertIn("امامی", plain)
        self.assertIn("مثقال", plain)

    def test_missing_buy_quote_is_unavailable_and_never_a_fabricated_zero(self):
        snapshot = {"usd1": "100001", "emami1": "111111111"}
        text = bonbast_rates.format_bonbast_channel_html(snapshot, currency_codes=["usd"])
        for label, amount in (("دلار آمریکا", "100,001"), ("امامی", "111,111,111")):
            with self.subTest(label=label):
                line = quote_line(text, label)
                self.assertIn(amount, line)
                self.assertIn("—", line)
                self.assertNotRegex(line, r"(?:\||:)\s*0(?:\s|$)")

    def test_missing_metal_sections_have_explicit_unavailable_notice(self):
        text = bonbast_rates.format_bonbast_channel_html(
            {"usd1": "100001", "usd2": "99999"}, currency_codes=["usd"],
        )
        plain = visible_text(text)
        self.assertIn("100,001", plain)
        self.assertRegex(plain, r"(?:در دسترس نیست|ناموجود|موجود نیست|اعلام نشده)")
        self.assertNotIn("امامی:", plain)
        self.assertNotIn("مثقال:", plain)

    def test_invalid_zero_and_non_finite_quotes_do_not_become_prices_or_html(self):
        for invalid in (None, "", "0", 0, -1, "-500", True, "NaN", "Infinity",
                        float("nan"), float("inf"), "<b>spoof</b>&"):
            with self.subTest(invalid=invalid):
                text = bonbast_rates.format_bonbast_channel_html(
                    {"usd1": invalid, "usd2": "99999"}, currency_codes=["usd"],
                )
                line = quote_line(text, "دلار آمریکا")
                self.assertIn("—", line)
                self.assertIn("99,999", line)
                self.assertNotIn("spoof", text)
                self.assertNotIn("NaN", text)
                self.assertNotIn("Infinity", text)

    def test_custom_currency_label_is_escaped_without_inserting_html(self):
        code = "x<b>y&z"
        text = bonbast_rates.format_bonbast_channel_html(
            {code + "1": "123456", code + "2": "123450"}, currency_codes=[code],
        )
        self.assertIn(html.escape(code.upper()), text)
        self.assertNotIn(code.upper(), text)
        self.assertIn("123,456", text)

    def test_usable_metals_can_be_posted_when_currency_fields_are_missing(self):
        text = bonbast_rates.format_bonbast_channel_html(
            {"gol18": "9876543", "emami1": "111111111", "emami12": "101010101"},
            currency_codes=["usd"],
        )
        plain = visible_text(text)
        self.assertIn("9,876,543", plain)
        self.assertIn("111,111,111", plain)
        self.assertNotIn("دلار آمریکا", plain)
        self.assertIn("نرخ ارز در پاسخ بن‌بست موجود نیست", plain)

    def test_completely_unusable_snapshot_is_rejected(self):
        for snapshot in ({}, {"rest": "1"}, {"usd1": "oops", "usd2": "0"},
                         {"gol18": "NaN", "emami1": "-100"}):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                bonbast_rates.format_bonbast_channel_html(snapshot, currency_codes=["usd"])

    def test_complete_supported_currency_list_and_metals_fit_one_message(self):
        snapshot = market_snapshot()
        for code in bonbast_rates.CURRENCY_LABELS:
            snapshot.setdefault(code + "1", "123456")
            snapshot.setdefault(code + "2", "123450")
        text = bonbast_rates.format_bonbast_channel_html(
            snapshot, currency_codes=list(bonbast_rates.CURRENCY_LABELS),
        )
        self.assertIn("امامی", text)
        self.assertIn("مثقال", text)
        self.assertLessEqual(len(visible_text(text).encode("utf-16-le")) // 2, 4096)
        self.assertEqual(text.count("Test footer"), 1)
        self.assertEqual(text.count("https://www.bonbast.com"), 1)

    def test_extreme_upstream_value_cannot_produce_an_oversized_channel_post(self):
        snapshot = {"usd1": "9" * 6000, "usd2": "99999"}
        try:
            text = bonbast_rates.format_bonbast_channel_html(snapshot, currency_codes=["usd"])
        except ValueError:
            return
        self.assertLessEqual(len(visible_text(text).encode("utf-16-le")) // 2, 4096)


class BonbastPostingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.snapshot = market_snapshot()
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.context = SimpleNamespace(bot=self.bot)
        self.stack.enter_context(patch.object(bonbast_daily, "BONBAST_CHANNEL_ID", str(CHANNEL_ID)))
        self.stack.enter_context(patch.object(bonbast_daily, "BONBAST_CURRENCY_CODES", ["eur", "usd"]))
        self.stack.enter_context(patch.object(bonbast_daily, "BONBAST_DAILY_POST_ENABLED", True))
        self.stack.enter_context(patch.object(bonbast_daily, "ADMIN_IDS", [9001]))
        self.stack.enter_context(patch("utils.channel_format.format_channel_ad_footer", return_value="\n\nTest footer"))
        self.fetch = self.stack.enter_context(patch.object(
            bonbast_daily, "fetch_bonbast_rates", return_value=self.snapshot,
        ))

    async def _post(self, mode):
        if mode == "daily":
            return await bonbast_daily.post_daily_bonbast_rates(self.context)
        return await bonbast_daily.post_bonbast_rates_now(self.bot)

    def _channel_calls(self):
        return [call for call in self.bot.send_message.await_args_list
                if call.kwargs.get("chat_id") == CHANNEL_ID
                or (call.args and call.args[0] == CHANNEL_ID)]

    async def test_daily_and_manual_each_fetch_one_snapshot_and_send_one_combined_message(self):
        for mode in ("daily", "manual"):
            with self.subTest(mode=mode):
                self.fetch.reset_mock()
                self.bot.send_message.reset_mock()
                with patch.object(bonbast_daily, "format_bonbast_channel_html",
                                  wraps=bonbast_rates.format_bonbast_channel_html) as formatter:
                    result = await self._post(mode)
                self.fetch.assert_called_once_with()
                formatter.assert_called_once_with(self.snapshot, currency_codes=["eur", "usd"])
                self.bot.send_message.assert_awaited_once()
                call = self._channel_calls()[0]
                self.assertEqual(call.kwargs["parse_mode"], "HTML")
                self.assertTrue(call.kwargs["disable_web_page_preview"])
                text = call.kwargs["text"]
                self.assertIn("200,003", text)
                self.assertIn("9,876,543", text)
                self.assertIn("111,111,111", text)
                self.assertNotIn("پوند انگلیس", text)
                if mode == "manual":
                    self.assertTrue(result)

    async def test_manual_post_remains_available_when_daily_schedule_is_disabled(self):
        with patch.object(bonbast_daily, "BONBAST_DAILY_POST_ENABLED", False):
            await self._post("daily")
            self.fetch.assert_not_called()
            self.bot.send_message.assert_not_awaited()
            self.assertTrue(await self._post("manual"))
        self.fetch.assert_called_once_with()
        self.assertEqual(len(self._channel_calls()), 1)

    def test_daily_schedule_keeps_configured_tehran_time(self):
        jobs = SimpleNamespace(run_daily=Mock())
        application = SimpleNamespace(job_queue=jobs)
        with (
            patch.object(bot_main, "BONBAST_DAILY_POST_ENABLED", True),
            patch.object(bot_main, "BONBAST_DAILY_HOUR", 9),
            patch.object(bot_main, "BONBAST_DAILY_MINUTE", 17),
        ):
            bot_main._setup_bonbast_daily_job(application)
        jobs.run_daily.assert_called_once()
        self.assertIs(jobs.run_daily.call_args.args[0], bonbast_daily.post_daily_bonbast_rates)
        when = jobs.run_daily.call_args.kwargs["time"]
        self.assertEqual((when.hour, when.minute), (9, 17))
        self.assertEqual(when.tzinfo.key, "Asia/Tehran")

    async def test_missing_channel_never_fetches_or_sends(self):
        with patch.object(bonbast_daily, "BONBAST_CHANNEL_ID", ""):
            await self._post("daily")
            self.assertFalse(await self._post("manual"))
        self.fetch.assert_not_called()
        self.bot.send_message.assert_not_awaited()

    async def test_fetch_retry_still_posts_one_successful_snapshot(self):
        self.fetch.side_effect = [TimeoutError("temporary"), self.snapshot]
        with patch.object(bonbast_daily.asyncio, "sleep", new=AsyncMock()) as pause:
            self.assertTrue(await self._post("manual"))
        self.assertEqual(self.fetch.call_count, 2)
        pause.assert_awaited_once()
        self.assertEqual(len(self._channel_calls()), 1)

    async def test_unusable_snapshot_reports_failure_without_partial_channel_message(self):
        for mode in ("daily", "manual"):
            with self.subTest(mode=mode):
                self.fetch.reset_mock()
                self.fetch.return_value = {}
                self.bot.send_message.reset_mock()
                result = await self._post(mode)
                self.fetch.assert_called_once_with()
                self.assertEqual(self._channel_calls(), [])
                self.bot.send_message.assert_awaited_once()
                self.assertEqual(self.bot.send_message.await_args.args[0], 9001)
                if mode == "manual":
                    self.assertFalse(result)

    async def test_fetch_failure_notifies_admin_without_publishing_partial_rates(self):
        for mode in ("daily", "manual"):
            with self.subTest(mode=mode):
                self.bot.send_message.reset_mock()
                with patch.object(bonbast_daily, "_fetch_rates_with_retry", new=AsyncMock(
                    side_effect=TimeoutError("upstream unavailable"),
                )):
                    result = await self._post(mode)
                self.assertEqual(self._channel_calls(), [])
                self.bot.send_message.assert_awaited_once()
                if mode == "manual":
                    self.assertFalse(result)

    async def test_channel_send_failure_is_not_reported_as_manual_success(self):
        self.bot.send_message.side_effect = [RuntimeError("channel unavailable"), None]
        self.assertFalse(await self._post("manual"))
        self.fetch.assert_called_once_with()
        self.assertEqual(len(self._channel_calls()), 1)
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_admin_command_does_not_announce_success_when_post_returns_false(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(reply_text=AsyncMock(return_value=status))
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=9001))
        with (
            patch.object(bot_main, "ADMIN_IDS", [9001]),
            patch.object(bonbast_daily, "post_bonbast_rates_now", new=AsyncMock(return_value=False)) as post,
        ):
            await bot_main.admin_post_bonbast_rates_cmd(update, self.context)
        post.assert_awaited_once_with(self.bot)
        response = status.edit_text.await_args.args[0]
        self.assertNotIn("✅", response)
        self.assertNotIn("منتشر شد", response)


if __name__ == "__main__":
    unittest.main()
