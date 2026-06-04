"""مدل خروجی — بدون وابستگی اجباری به pydantic."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class DocumentType(str, Enum):
    BANK_CARD = "BANK_CARD"
    BANK_RECEIPT = "BANK_RECEIPT"
    SHEBA_DOCUMENT = "SHEBA_DOCUMENT"
    ACCOUNT_INFORMATION = "ACCOUNT_INFORMATION"
    PAYMENT_CONFIRMATION = "PAYMENT_CONFIRMATION"
    UNKNOWN = "UNKNOWN"


@dataclass
class BankingExtractionResult:
    document_type: str = DocumentType.UNKNOWN.value
    bank_name: str = ""
    card_number: str = ""
    sheba: str = ""
    account_number: str = ""
    owner_name: str = ""
    sender_name: str = ""
    receiver_name: str = ""
    amount: int | None = None
    date: str = ""
    time: str = ""
    tracking_number: str = ""
    transaction_id: str = ""
    status: str = ""
    confidence: float = 0.0
    fraud_score: float = 0.0
    raw_text: str = ""
    validation_errors: list[str] = field(default_factory=list)
    source: str = ""
    processing_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_telegram_dict(self) -> dict[str, Any]:
        return asdict(self)
