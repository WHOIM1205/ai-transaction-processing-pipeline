"""`JobSummary` ORM model.

WHY THIS FILE EXISTS
--------------------
Holds the one aggregate report produced per job (1:1 with `Job`): per-currency
totals, top merchants, category breakdown, anomaly count, and the LLM-generated
narrative + risk level. Kept in its own table (rather than columns on `jobs`)
so the report can be (re)written atomically by the pipeline without touching
the job lifecycle row.
"""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import RiskLevel

if TYPE_CHECKING:
    from app.models.job import Job


class JobSummary(Base):
    __tablename__ = "job_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # UNIQUE enforces the 1:1 relationship with a job at the database level.
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Per-currency totals kept separate — mixing INR and USD would be wrong, so
    # there is deliberately no single "total spend" column and no FX conversion.
    total_spend_inr: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, server_default=text("0")
    )
    total_spend_usd: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, server_default=text("0")
    )

    # Variable-shape aggregates → JSONB.
    #   top_merchants: [{"merchant": str, "total": number, "count": int}, ...]
    top_merchants: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    #   category_breakdown: {"Shopping": number, "Food": number, ...}
    category_breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    anomaly_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # NULL when the LLM summary call failed (job still completes — degraded).
    narrative: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[RiskLevel | None] = mapped_column(
        Enum(RiskLevel, name="risk_level")
    )

    # --- Relationships -------------------------------------------------------
    job: Mapped["Job"] = relationship(back_populates="summary")

    def __repr__(self) -> str:
        return (
            f"<JobSummary job_id={self.job_id} risk_level={self.risk_level} "
            f"anomaly_count={self.anomaly_count}>"
        )
