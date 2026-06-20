"""Job response schemas.

WHY THIS FILE EXISTS
--------------------
Defines the job-shaped responses the API returns. Public payloads expose the
job's identifier as `job_id` (mapped from the ORM attribute `id` via
`validation_alias`), so the wire contract is stable and independent of the
column name.

  * JobCreatedOut — POST /jobs/upload (the accepted job)
  * JobListItem   — one row in GET /jobs
  * JobListOut    — paginated envelope for GET /jobs
  * JobStatusOut  — GET /jobs/{id}/status (+ summary once completed)
  * JobOut / JobResultsOut — job detail + full results payload

All build from ORM instances via `from_attributes=True`.
"""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import JobStatus
from app.schemas.summary import JobSummaryOut
from app.schemas.transaction import TransactionOut


class JobCreatedOut(BaseModel):
    """Response for a successful upload — returns the new job id immediately."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    job_id: uuid.UUID = Field(validation_alias="id")
    status: JobStatus
    filename: str


class JobListItem(BaseModel):
    """Compact representation of a job in the listing endpoint."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    job_id: uuid.UUID = Field(validation_alias="id")
    filename: str
    status: JobStatus
    row_count_raw: int | None
    created_at: datetime.datetime


class JobListOut(BaseModel):
    """Paginated envelope for GET /jobs."""

    items: list[JobListItem]
    total: int
    limit: int
    offset: int


class JobStatusOut(BaseModel):
    """Status response. `summary` is populated only once the job is completed."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    job_id: uuid.UUID = Field(validation_alias="id")
    status: JobStatus
    row_count_raw: int | None
    row_count_clean: int | None
    error_message: str | None
    summary: JobSummaryOut | None = None


# --- Job detail + results payload -------------------------------------------
class JobBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    job_id: uuid.UUID = Field(validation_alias="id")
    filename: str
    status: JobStatus
    row_count_raw: int | None
    row_count_clean: int | None
    error_message: str | None
    created_at: datetime.datetime
    completed_at: datetime.datetime | None


class JobOut(JobBase):
    """A job's core fields (no nested relationships)."""


class TransactionPage(BaseModel):
    """A paginated slice of a job's transactions."""

    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


class JobResultsOut(JobBase):
    """Full results payload — job details + status + summary + (paged) transactions."""

    summary: JobSummaryOut | None = None
    transactions: TransactionPage
