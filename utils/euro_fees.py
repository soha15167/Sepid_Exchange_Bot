"""
utils/euro_fees.py — Fee calculation / محاسبهٔ کارمزد

EN: Tiered EUR fee per party; optional admin override on advert row.
FA: پلکان کارمزد یورو برای هر طرف؛ override ادمین روی آگهی.

Tiers / پله‌ها (per party EUR, not split / هر طرف، بدون نصف):
- تا ۵۰۰ یورو (شامل ۵۰۰): ۲٫۵ یورو برای هر طرف
- ۵۰۱ یورو به بالا: نیم‌درصد (۰٫۵٪) مبلغ یورو برای هر طرف

اگر برای آگهی `fee_override_eur` تنظیم شده باشد، همان مقدار به‌عنوان کارمزد هر طرف (یورو)
در نظر گرفته می‌شود (شامل **۰** برای «بدون کارمزد اما نمایش صریح ۰ یورو»؛ `NULL`/خالی = فرمول خودکار).
"""


def advert_fee_override_eur(advert: dict | None) -> float | None:
    """مقدار کارمزد دستی (یورو) برای هر طرف؛ None یعنی فرمول پلکانی. مقدار ۰ یعنی کارمزد ثابت صفر (مجزا از خودکار)."""
    if not advert:
        return None
    v = advert.get("fee_override_eur")
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x < 0:
        return None
    return x


def fee_total_eur(amount: int | None, override_total_eur: float | None = None) -> float:
    if override_total_eur is not None:
        return max(0.0, float(override_total_eur))
    if not amount or amount <= 0:
        return 0.0
    if amount <= 500:
        return 2.5
    return float(amount) * 0.005


def fee_per_side_eur(amount: int | None, override_total_eur: float | None = None) -> float:
    """هم‌معنی `fee_total_eur`: هر طرف همان مبلغ کارمزد را می‌پردازد (نیمه‌سازی حذف شده)."""
    return fee_total_eur(amount, override_total_eur)


def format_fee_eur(amount: int | None, override_total_eur: float | None = None) -> str:
    if override_total_eur is not None:
        fee = max(0.0, float(override_total_eur))
        s = f"{fee:.2f}".rstrip("0").rstrip(".")
        return f"{s} یورو"
    if not amount or amount <= 0:
        return "—"
    fee = fee_total_eur(amount, None)
    s = f"{fee:.2f}".rstrip("0").rstrip(".")
    return f"{s} یورو"
