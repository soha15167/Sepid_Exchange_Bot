from __future__ import annotations

from pydantic import BaseModel, Field


class LookupRequest(BaseModel):
    login: str = Field(..., min_length=3, description="Phone (+989...) or email")


class OtpSendRequest(BaseModel):
    login: str
    purpose: str = "login"


class OtpVerifyRequest(BaseModel):
    challenge_id: str
    code: str = Field(..., min_length=4, max_length=8)


class PasswordSetRequest(BaseModel):
    challenge_id: str
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    login: str
    password: str


class OtpLoginRequest(BaseModel):
    challenge_id: str
    otp_code: str = Field(..., min_length=4, max_length=8)


class RegisterRequest(BaseModel):
    challenge_id: str
    full_name: str = Field(..., min_length=2, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    display_name: str = Field(..., min_length=2, max_length=40)
    email: str
    address: str = Field(..., min_length=3, max_length=300)
    phone_number: str
    password: str = Field(..., min_length=6, max_length=128)
    accept_terms: bool = False


class AdvertCreateRequest(BaseModel):
    operation: str = Field(..., pattern="^(خرید|فروش)$")
    euro_amount: int = Field(..., gt=0)
    rate_toman: int = Field(..., gt=0)
    description: str = Field(..., min_length=3, max_length=2000)
    methods: list[str] = Field(..., min_length=1)
    account_country: str = Field(..., min_length=2, max_length=80)
    instant_transfer: str | None = None


class AdvertUpdateRequest(BaseModel):
    euro_amount: int | None = Field(default=None, gt=0)
    rate_toman: int | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, min_length=3, max_length=2000)
    methods: list[str] | None = None
    account_country: str | None = Field(default=None, min_length=2, max_length=80)
    instant_transfer: str | None = None
    city_ir: str | None = Field(default=None, min_length=2, max_length=80)
    city_int: str | None = Field(default=None, max_length=80)


class AdvertExchangeCreateRequest(BaseModel):
    side: str = Field(..., pattern="^(خرید|فروش)$")
    delivery: str = Field(..., pattern="^(transfer|in_person)$")
    euro_amount: int = Field(..., gt=0)
    account_country: str = Field(..., min_length=2, max_length=80)
    city_ir: str = Field(..., min_length=2, max_length=80)
    city_int: str | None = Field(default=None, max_length=80)
    description: str = Field(..., min_length=2, max_length=2000)
    instant_transfer: str | None = None


class OfferCreateRequest(BaseModel):
    mode: str = Field(..., pattern="^(agree|custom)$")
    rate_toman: int = Field(default=0, ge=0)
    description: str = Field(..., min_length=2, max_length=2000)
    proposed_euro_amount: int | None = Field(default=None, gt=0)
    proposer_account_country: str | None = Field(default=None, max_length=80)


class OfferRateUpdateRequest(BaseModel):
    rate_toman: int = Field(..., gt=0)


class DealResponseRequest(BaseModel):
    response: str = Field(..., pattern="^(yes|no)$")


class DealAccountRequest(BaseModel):
    text: str = Field(..., min_length=2, max_length=2000)


class DealReceiptRequest(BaseModel):
    text: str = Field(..., min_length=2, max_length=2000)


class NegotiationPostRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
