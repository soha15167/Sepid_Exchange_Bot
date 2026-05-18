# قوانین کانال و نمایش نرخ کارمزد (منوی اصلی)

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config.settings import ADMIN_IDS
from database.db import get_user, set_user_channel_rules_acknowledged
from models.enums import UserState
from state import user_data_store
from utils.telegram_utils import normalize_telegram_callback_data, send_or_replace_main_menu

_RTL = "\u200f"

INFO_CLOSE_CALLBACK = "info_close"

BACK_TO_MAIN_KB = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data=INFO_CLOSE_CALLBACK)]]
)


def channel_rules_html() -> str:
    """متن قوانین و روال کار کانال — بازنویسی خوانا از متن ارسالی مدیر."""
    return (
        f"{_RTL}<b>سلام دوستان و همراهان گرامی</b>\n\n"
        f"{_RTL}در ادامه، نکات مهم مربوط به <b>نحوهٔ معامله در کانال</b> و "
        f"<b>روال پرداخت تومان و یورو</b> به‌صورت خلاصه آمده است. لطفاً قبل از هر معامله، "
        f"این موارد را با دقت بخوانید.\n\n"
        f"{_RTL}<b>۱) واریز ریال پس از نشستن یورو</b>\n"
        f"{_RTL}بعد از انجام معامله و <b>نشستن یورو</b>، واریز ریال از سوی ما در <b>کوتاه‌ترین زمان ممکن</b> "
        f"انجام می‌شود؛ به‌محض واریز، <b>فیش</b> برای شما ارسال خواهد شد.\n\n"
        f"{_RTL}<b>۲) تعطیلات رسمی ایران</b>\n"
        f"{_RTL}در <b>روزهای تعطیل رسمی ایران</b>، واریز ریال انجام نمی‌شود.\n\n"
        f"{_RTL}<b>۳) ممنوعیت آیدی و شماره تماس در آگهی</b>\n"
        f"{_RTL}قرار دادن هرگونه <b>آیدی</b> و <b>شمارهٔ تماس</b> در آگهی ممنوع است. "
        f"در صورت مشاهده، آگهیٔ مربوط حذف و فعالیت کاربر <b>محدود</b> می‌شود.\n\n"
        f"{_RTL}<b>۴) رفرنس و اشاره به ایران</b>\n"
        f"{_RTL}به‌دلیل <b>تحریم‌ها</b>، در رفرنس و متن حواله از نوشتن هرگونه اشاره به "
        f"<b>ایران</b> و کلماتی مانند «ریال»، «تومان»، «چنج پول» و موارد مشابه <b>خودداری کنید</b>. "
        f"در غیر این صورت احتمال <b>مسدود شدن حساب فرستنده و گیرنده</b> وجود دارد و "
        f"در صورت بروز مشکل، فرد خاطی <b>ملزم به جبران خسارت</b> است.\n\n"
        f"{_RTL}<b>۵) آیدی ادمین</b>\n"
        f"{_RTL}آیدی ادمین در <b>توضیحات کانال</b> درج شده است. هر آیدی مشابه یا غیررسمی "
        f"<b>کلاهبرداری</b> محسوب می‌شود و مورد تأیید ما نیست.\n"
        f"{_RTL}آیدی رسمی ادمین: <code>@Sepid_Group_Admin</code>\n\n"
        f"{_RTL}<b>۶) بعد از تأیید آگهی و تیک سبز</b>\n"
        f"{_RTL}برای انجام معامله، به <b>ادمین</b> پیام دهید و <b>پیام ربات</b> (آگهی / پیشنهاد) را "
        f"برای ایشان ارسال کنید تا هماهنگی سریع‌تر انجام شود.\n\n"
        f"{_RTL}<b>۷) پیشنهاد و آگهی متناسب با مقدار یورو</b>\n"
        f"{_RTL}به‌ازای هر مقدار یورویی که برای <b>فروش</b> دارید یا قصد <b>خرید</b> دارید، "
        f"همان مقدار را در <b>یک پیشنهاد</b> یا <b>یک آگهی</b> ثبت کنید. "
        f"در صورت ثبت چند پیشنهاد، فقط <b>اولین پیشنهاد تأییدشده</b> معتبر است.\n\n"
        f"{_RTL}<b>۸) پاسخ‌گویی به‌موقع</b>\n"
        f"{_RTL}در صورت عدم پاسخ‌گویی منطقی از سوی هر یک از طرفین، مسئولیت ناشی از تأخیر "
        f"بر عهدهٔ کسی است که <b>در دسترس نبوده</b>؛ لطفاً بعد از توافق، در زمان معقول پاسخگو باشید.\n\n"
        f"{_RTL}<b>۹) کشور بانک در آگهی و مذاکره</b>\n"
        f"{_RTL}هنگام ثبت آگهی، <b>کشور مربوط به بانک خود</b> را دقیق انتخاب کنید. "
        f"در پیشنهاد و مذاکره نیز کشور بانک خود را به طرف مقابل اعلام کنید؛ "
        f"در غیر این صورت طرف مقابل حق <b>کنسلی</b> دارد و این کنسلی برای فرد خاطی اعمال می‌شود.\n\n"
        f"{_RTL}<b>🛎 روال کار کانال (خلاصهٔ سه مرحله)</b>\n\n"
        f"{_RTL}<b>مرحلهٔ ۱ — تومان نزد ادمین (امانت)</b>\n"
        f"{_RTL}ابتدا خریدار، <b>تومان</b> را به <b>حساب ریالی ادمین</b> واریز می‌کند تا به‌صورت "
        f"<b>امانت</b> نزد ادمین بماند.\n\n"
        f"{_RTL}<b>مرحلهٔ ۲ — پرداخت یورو از فروشنده به خریدار</b>\n"
        f"{_RTL}پس از اینکه تومان به حساب ادمین <b>نشست</b>، به فروشنده اطلاع داده می‌شود تا "
        f"<b>مقدار یورو</b> را به خریدار پرداخت کند (حضوری، حواله به حساب، پی‌پال و … طبق توافق).\n\n"
        f"{_RTL}<b>مرحلهٔ ۳ — واریز تومان به فروشنده</b>\n"
        f"{_RTL}وقتی خریدار، <b>اصالت و مقدار یورو</b> را (به‌ویژه در تحویل حضوری) تأیید کرد، "
        f"ادمین <b>تومان</b> را به حساب فروشنده واریز می‌کند."
    )


def fee_schedule_html() -> str:
    """جدول کارمزد یورویی (هر طرف) مطابق منطق برنامه."""
    return (
        f"{_RTL}<b>🧾 نرخ کارمزد معاملات (یورو)</b>\n\n"
        f"{_RTL}کارمزد به‌ازای <b>هر طرف</b> به‌صورت زیر محاسبه می‌شود "
        f"(نیمی از طرف دیگر منظور نیست؛ هر طرف مبلغ زیر را می‌پردازد):\n\n"
        f"{_RTL}• برای آگهی <b>۱ تا ۵۰ یورو</b>: <b>۱ یورو</b> کارمزد هر طرف\n"
        f"{_RTL}• برای آگهی <b>۵۱ تا ۵۰۰ یورو</b>: <b>۲٫۵ یورو</b> کارمزد هر طرف\n"
        f"{_RTL}• برای آگهی <b>بالای ۵۰۰ یورو</b>: <b>نیم‌درصد (۰٫۵٪)</b> مبلغ آگهی به‌عنوان کارمزد هر طرف\n\n"
        f"{_RTL}<i>تذکر:</i> ادمین می‌تواند برای بعضی آگهی‌ها مبلغ <b>ثابت</b> یا <b>صفر</b> تعیین کند؛ "
        f"در آن صورت همان مبلغ درج‌شده روی آگهی معتبر است و این پلکان اعمال نمی‌شود."
    )


async def handle_info_close_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return
    if normalize_telegram_callback_data(q.data) != INFO_CLOSE_CALLBACK:
        return
    uid = q.from_user.id
    chat_id = q.message.chat_id if q.message else update.effective_chat.id
    if uid not in set(ADMIN_IDS or []) and get_user(uid) is None:
        try:
            await q.answer()
        except Exception:
            pass
        if q.message:
            try:
                await q.message.delete()
            except Exception:
                pass
        return
    try:
        await q.answer()
    except Exception:
        pass
    if q.message:
        try:
            await q.message.delete()
        except Exception:
            pass
    context.user_data["state"] = UserState.MAIN_MENU.name
    await send_or_replace_main_menu(
        context.bot,
        chat_id=chat_id,
        user_id=uid,
        store=user_data_store,
    )


async def _send_rules_or_fees(
    *,
    bot,
    chat_id: int,
    html: str,
) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=html,
        parse_mode=ParseMode.HTML,
        reply_markup=BACK_TO_MAIN_KB,
        disable_web_page_preview=True,
    )


async def handle_main_rules_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    if normalize_telegram_callback_data(q.data) != "main_rules":
        return
    try:
        await q.answer()
    except Exception:
        pass
    uid = q.from_user.id
    if get_user(uid):
        set_user_channel_rules_acknowledged(uid)
    try:
        await q.edit_message_text(
            channel_rules_html(),
            parse_mode=ParseMode.HTML,
            reply_markup=BACK_TO_MAIN_KB,
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await q.message.delete()
        except Exception:
            pass
        await _send_rules_or_fees(
            bot=context.bot, chat_id=q.message.chat_id, html=channel_rules_html()
        )


async def handle_main_fees_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    if normalize_telegram_callback_data(q.data) != "main_fees":
        return
    try:
        await q.answer()
    except Exception:
        pass
    try:
        await q.edit_message_text(
            fee_schedule_html(),
            parse_mode=ParseMode.HTML,
            reply_markup=BACK_TO_MAIN_KB,
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await q.message.delete()
        except Exception:
            pass
        await _send_rules_or_fees(
            bot=context.bot, chat_id=q.message.chat_id, html=fee_schedule_html()
        )


async def handle_main_rules_reply_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    m = update.message
    if not m or not update.effective_user:
        return
    uid = update.effective_user.id
    if get_user(uid):
        set_user_channel_rules_acknowledged(uid)
    chat_id = m.chat_id
    try:
        await m.delete()
    except Exception:
        pass
    await _send_rules_or_fees(bot=context.bot, chat_id=chat_id, html=channel_rules_html())


async def handle_main_fees_reply_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    m = update.message
    if not m or not update.effective_user:
        return
    chat_id = m.chat_id
    try:
        await m.delete()
    except Exception:
        pass
    await _send_rules_or_fees(bot=context.bot, chat_id=chat_id, html=fee_schedule_html())
