"""
models/enums.py — Conversation states / حالت‌های مکالمه

EN:
  `UserState` names are stored in `context.user_data["state"]` as strings
  (e.g. UserState.OFFER_RATE.name). main.py routes messages by these values.

FA:
  هر مقدار enum نام مرحلهٔ فعلی کاربر است؛ main.py بر اساس آن هندلر را انتخاب می‌کند.
"""

from enum import Enum, auto


class UserState(Enum):
    START = auto()
    MAIN_MENU = auto()
    SERVICE_SELECTION = auto()

    # Registration / ثبت‌نام
    START_REGISTRATION = auto()
    TERMS = auto()
    STEPS_INFO = auto()
    FIRST_NAME = auto()
    LAST_NAME = auto()
    EMAIL = auto()
    ADDRESS = auto()
    PHONE = auto()
    VERIFYING_PHONE = auto()

    # Euro buy/sell advert / آگهی خرید و فروش یورو
    EURO_BUY_SELL = auto()
    EURO_SERVICE_OPTIONS = auto()
    EURO_AMOUNT = auto()
    EURO_RATE = auto()
    EURO_DESCRIPTION = auto()
    EURO_ACCOUNT_COUNTRY = auto()
    EURO_INSTANT_TRANSFER = auto()
    EURO_CONFIRM_ADVERT = auto()
    EURO_FOREIGN_CITY = auto()
    EURO_IRAN_CITY = auto()
    EURO_DEPOSIT_OPTION = auto()

    # Euro-to-Euro exchange / معاوضه Euro به Euro
    EXCHANGE_INIT = auto()
    EXCHANGE_INSTANT_TRANSFER = auto()
    EXCHANGE_AMOUNT = auto()
    EXCHANGE_COUNTRY_INT = auto()
    EXCHANGE_CITY_INT = auto()
    EXCHANGE_CITY_IR = auto()
    EXCHANGE_DESCRIPTION = auto()
    EXCHANGE_CONFIRM = auto()
    EXCHANGE_CHOICE = auto()

    # Offer on channel ad / پیشنهاد روی آگهی
    OFFER_ADVERT_ID = auto()
    OFFER_COUNTER_EURO = auto()
    OFFER_RATE = auto()
    OFFER_ACCOUNT_COUNTRY = auto()
    OFFER_DESCRIPTION = auto()
    OFFER_PREVIEW = auto()
    OFFER_EDIT_RATE = auto()
    NEGOTIATION = auto()
    NEGOTIATION_GATE = auto()

    # End-user panel / پنل کاربر عادی
    USER_SETTINGS_VIEW = auto()
    USER_EDIT_OWN_ADVERT = auto()

    # Admin panel / پنل ادمین
    ADMIN_MENU = auto()
    ADMIN_DELETE_USER_ID = auto()
    ADMIN_DELETE_CONFIRM = auto()
    ADMIN_EDIT_USER_ID = auto()
    ADMIN_EDIT_FIELD = auto()
    ADMIN_EDIT_VALUE = auto()
    ADMIN_EDIT_PHONE_VERIFY = auto()
    ADMIN_ADD_USER_ID = auto()
    ADMIN_ADD_USER_FIELD = auto()
    ADMIN_RESTRICT_USER_ID = auto()
    ADMIN_RESTRICT_DAYS = auto()
    # Legacy alias / نام قدیمی — same as ADMIN_RESTRICT_DAYS
    ADMIN_RESTRICT_LEVEL = ADMIN_RESTRICT_DAYS
    ADMIN_LIST_ADVERTS = auto()
    ADMIN_EDIT_ADVERT_ID = auto()
    ADMIN_EDIT_ADVERT_FIELD = auto()
    ADMIN_EDIT_ADVERT_VALUE = auto()
    ADMIN_EDIT_ADVERT_METHODS = auto()
    ADMIN_EDIT_ADVERT_INSTANT = auto()
    ADMIN_EDIT_ADVERT_RATE = auto()
    ADMIN_DELETE_ADVERT_ID = auto()
    ADMIN_DELETE_ADVERT_CONFIRM = auto()
    ADMIN_ADD_ADVERT = auto()
    ADMIN_VIEW_OFFERS = auto()
    ADMIN_SEARCH_USER = auto()
    ADMIN_SEARCH_ADVERT = auto()
    ADMIN_NEG_VIEW_ADVERT = auto()
    ADMIN_EXCH_EDIT_FLOW = auto()
    ADMIN_MANAGE_OFFER_ADVERT = auto()
    ADMIN_MANAGE_OFFER_SEQ = auto()
    ADMIN_MANAGE_OFFER_CMD = auto()
    ADMIN_MANAGE_OFFER_RATE_INPUT = auto()
    ADMIN_MANAGE_OFFER_EURO_INPUT = auto()
    ADMIN_FEE_ADVERT_ID = auto()
    ADMIN_FEE_VALUE = auto()
    # Admin proxy offer (demo) / پیشنهاد نمایشی ادمین
    ADMIN_PROXY_OFFER_ADVERT = auto()
    ADMIN_PROXY_OFFER_NAME = auto()
    ADMIN_PROXY_OFFER_RATE = auto()
    ADMIN_PROXY_OFFER_DESC = auto()
