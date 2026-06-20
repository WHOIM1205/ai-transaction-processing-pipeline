"""Transaction response schema.

WHY THIS FILE EXISTS
--------------------
Defines exactly which transaction fields are exposed over the API and their
JSON types. Built from the `Transaction` ORM model via `from_attributes`.
"""

import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import Currency, TransactionStatus


class TransactionOut(BaseModel):
    """A single cleaned transaction as returned in the results payload."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    txn_id: str | None
    date: datetime.date | None
    merchant: str
    amount: Decimal
    currency: Currency
    status: TransactionStatus
    category: str
    account_id: str
    notes: str | None
    is_anomaly: bool
    anomaly_reason: list[str] | None
    llm_category: str | None
    llm_failed: bool
