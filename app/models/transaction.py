"""`Transaction` ORM model.

WHY THIS FILE EXISTS
--------------------
One row per cleaned, de-duplicated transaction belonging to a job. This is the
high-volume table, so it uses a compact `BIGSERIAL` surrogate key and carries
the anomaly/LLM annotation columns the pipeline will populate in later phases.
"""

import datetime
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import Currency, TransactionStatus

if TYPE_CHECKING:
    from app.models.job import Job


class Transaction(Base):
    __tablename__ = "transactions"

    # Surrogate key for the hot table — sequential and index-friendly.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Owning job. ON DELETE CASCADE + passive_deletes lets Postgres handle the
    # cascade efficiently for bulk deletes. Indexed: every results query filters
    # by job_id.
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Source transaction id — NULLABLE because the CSV genuinely has blanks;
    # we never invent ids.
    txn_id: Mapped[str | None] = mapped_column(String(64))

    # Date normalised to ISO 8601; NULL when the source value is unparseable.
    date: Mapped[datetime.date | None] = mapped_column(Date)

    merchant: Mapped[str] = mapped_column(String(255), nullable=False)

    # Money is NUMERIC, never float. (14,2) covers the large outliers with
    # headroom. CHECK keeps amounts non-negative.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, name="currency"), nullable=False
    )
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transaction_status"), nullable=False
    )

    # Blank categories are filled with 'Uncategorised' during cleaning.
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Uncategorised"
    )

    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    # --- Anomaly annotations (populated by the processing pipeline) ----------
    is_anomaly: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # A row can trip multiple rules, so reasons are an array of codes.
    anomaly_reason: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    # --- LLM annotations (populated by the processing pipeline) --------------
    # Category assigned by the LLM for rows that were originally blank.
    llm_category: Mapped[str | None] = mapped_column(String(64))
    # True if this row's LLM batch exhausted retries (degraded, not fatal).
    llm_failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # --- Relationships -------------------------------------------------------
    job: Mapped["Job"] = relationship(back_populates="transactions")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_transactions_amount_non_negative"),
        # Per-account grouping for the median-based anomaly rule.
        Index("ix_transactions_job_id_account_id", "job_id", "account_id"),
        # Partial index: fast retrieval of flagged rows for the results endpoint.
        Index(
            "ix_transactions_job_id_is_anomaly",
            "job_id",
            postgresql_where=text("is_anomaly"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} job_id={self.job_id} "
            f"merchant={self.merchant!r} amount={self.amount}>"
        )
