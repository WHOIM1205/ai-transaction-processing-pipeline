"""Data-access for transactions and job summaries.

WHY THIS FILE EXISTS
--------------------
Keeps the bulk write/delete SQL for a job's results out of the service/worker.
Operates on a provided session and does not commit — the persistence service
owns the transaction boundary so the whole result set lands atomically.
"""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.summary import JobSummary
from app.models.transaction import Transaction


def delete_results_for_job(db: Session, job_id: uuid.UUID) -> None:
    """Remove any existing transactions + summary for a job.

    Makes re-processing idempotent: a re-run replaces prior results instead of
    duplicating them.
    """
    db.execute(delete(Transaction).where(Transaction.job_id == job_id))
    db.execute(delete(JobSummary).where(JobSummary.job_id == job_id))


def bulk_insert_transactions(db: Session, transactions: list[Transaction]) -> None:
    """Insert all transaction rows for a job."""
    db.add_all(transactions)


def list_for_job(
    db: Session, job_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[Transaction], int]:
    """Return a page of a job's transactions (stable order) and the total count."""
    total = (
        db.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.job_id == job_id)
        )
        or 0
    )
    stmt = (
        select(Transaction)
        .where(Transaction.job_id == job_id)
        .order_by(Transaction.id)
        .limit(limit)
        .offset(offset)
    )
    items = list(db.scalars(stmt).all())
    return items, total
