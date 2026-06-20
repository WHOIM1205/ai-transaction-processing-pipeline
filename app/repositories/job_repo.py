"""Data-access functions for the `Job` aggregate.

WHY THIS FILE EXISTS
--------------------
Centralises every query touching jobs so routes/services never embed SQL. These
functions operate on a provided `Session` and do NOT commit — transaction
boundaries are owned by the service layer, so multiple repo calls can be
composed into one unit of work.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import JobStatus
from app.models.job import Job


def create_job(db: Session, filename: str) -> Job:
    """Insert a new job in the `pending` state and return it with its id.

    Flushes (not commits) so the DB-generated `id`/`created_at` are populated
    while leaving the surrounding transaction open for the caller to commit.
    """
    job = Job(filename=filename, status=JobStatus.pending)
    db.add(job)
    db.flush()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: uuid.UUID) -> Job | None:
    """Fetch a single job by id, eagerly loading its summary.

    `selectinload` avoids a lazy load during response serialisation (which could
    fire after the request scope) and prevents an N+1 for the status endpoint.
    """
    stmt = select(Job).options(selectinload(Job.summary)).where(Job.id == job_id)
    return db.scalar(stmt)


def list_jobs(
    db: Session,
    status: JobStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[Job], int]:
    """Return a page of jobs (newest first) and the total matching count.

    The total is computed with the same filter so pagination metadata is
    accurate regardless of the page requested.
    """
    filters = []
    if status is not None:
        filters.append(Job.status == status)

    total = db.scalar(select(func.count()).select_from(Job).where(*filters)) or 0

    stmt = (
        select(Job)
        .where(*filters)
        .order_by(Job.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list(db.scalars(stmt).all())
    return items, total


def delete_job(db: Session, job: Job) -> None:
    """Remove a job (used to roll back an orphaned record if file write fails)."""
    db.delete(job)
