"""Data-transfer objects passed between pipeline stages.

WHY THIS FILE EXISTS
--------------------
The cleaning → anomaly → classification → summary stages operate on plain,
in-memory rows rather than ORM objects, so they stay pure and unit-testable
without a database. `PipelineRow` is the mutable working record threaded through
the stages; `SummaryData` and `PipelineResult` are the immutable outputs the
worker persists.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.constants import UNCATEGORISED
from app.models.enums import Currency, TransactionStatus


@dataclass
class PipelineRow:
    """One cleaned transaction, annotated in place by later stages."""

    # --- set by cleaning -----------------------------------------------------
    txn_id: str | None
    date: datetime.date | None
    merchant: str
    amount: Decimal
    currency: Currency
    status: TransactionStatus
    category: str
    account_id: str
    notes: str | None

    # --- set by anomaly detection -------------------------------------------
    is_anomaly: bool = False
    anomaly_reason: list[str] = field(default_factory=list)

    # --- set by LLM classification ------------------------------------------
    llm_category: str | None = None
    llm_failed: bool = False

    @property
    def effective_category(self) -> str:
        """Original category if present, else the LLM-assigned one, else the
        placeholder. Used for spend/category aggregation."""
        if self.category and self.category != UNCATEGORISED:
            return self.category
        return self.llm_category or UNCATEGORISED


@dataclass
class SummaryData:
    """Aggregate report for a job. Monetary JSON fields hold floats (JSON-safe);
    the per-currency totals stay Decimal for the NUMERIC columns."""

    total_spend_inr: Decimal
    total_spend_usd: Decimal
    top_merchants: list[dict]
    category_breakdown: dict[str, float]
    anomaly_count: int
    narrative: str | None
    risk_level: str | None


@dataclass
class PipelineResult:
    """Everything the worker needs to persist for a processed job."""

    rows: list[PipelineRow]
    row_count_raw: int
    row_count_clean: int
    summary: SummaryData
