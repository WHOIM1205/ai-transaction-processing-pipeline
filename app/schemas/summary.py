"""Job summary response schemas.

WHY THIS FILE EXISTS
--------------------
Gives the JSONB blobs stored on `JobSummary` a typed, validated shape on the
way out. `TopMerchant` documents the structure of each `top_merchants` entry
instead of leaking a raw, untyped dict to clients.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import RiskLevel


class TopMerchant(BaseModel):
    """One entry in the summary's `top_merchants` list."""

    merchant: str
    total: Decimal
    count: int


class JobSummaryOut(BaseModel):
    """Aggregate report for a completed job."""

    model_config = ConfigDict(from_attributes=True)

    total_spend_inr: Decimal
    total_spend_usd: Decimal
    top_merchants: list[TopMerchant] = Field(default_factory=list)
    category_breakdown: dict[str, Decimal] = Field(default_factory=dict)
    anomaly_count: int
    narrative: str | None
    risk_level: RiskLevel | None
