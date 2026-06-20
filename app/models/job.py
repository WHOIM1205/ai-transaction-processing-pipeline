"""`Job` ORM model.

WHY THIS FILE EXISTS
--------------------
The `jobs` table is the system of record for an upload's lifecycle. Every
uploaded CSV becomes one `Job` row whose `status` the polling API reports, and
which owns the resulting transactions and summary.

SQLAlchemy 2.x typed style (`Mapped` / `mapped_column`) is used throughout so
the Python types and the database schema are declared in one place.
"""

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import JobStatus

if TYPE_CHECKING:  # import only for type checkers; avoids runtime import cycles
    from app.models.summary import JobSummary
    from app.models.transaction import Transaction


class Job(Base):
    __tablename__ = "jobs"

    # UUID primary key: safe to expose in URLs and non-enumerable. Generated
    # application-side so the value is known immediately on insert.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Original upload filename — echoed back in the job listing for traceability.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)

    # Lifecycle state. Indexed because `GET /jobs?status=` filters on it.
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"),
        nullable=False,
        default=JobStatus.pending,
        server_default=JobStatus.pending.value,
        index=True,
    )

    # Rows read from the CSV (pre-dedup) and rows kept after cleaning. Nullable
    # because they are unknown until the worker has parsed the file.
    row_count_raw: Mapped[int | None] = mapped_column(Integer)
    row_count_clean: Mapped[int | None] = mapped_column(Integer)

    # Populated only when status == failed; surfaced for debugging.
    error_message: Mapped[str | None] = mapped_column(Text)

    # Timezone-aware timestamps. `created_at` is DB-defaulted and indexed for
    # the default "newest first" listing order.
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # --- Relationships -------------------------------------------------------
    # 1:N — a job owns its cleaned transactions. Cascade delete keeps cleanup
    # trivial (deleting a job removes its children).
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # 1:1 — a job has at most one summary report.
    summary: Mapped["JobSummary | None"] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    def __repr__(self) -> str:  # helpful in logs / shell, no behavioural impact
        return f"<Job id={self.id} status={self.status} filename={self.filename!r}>"
