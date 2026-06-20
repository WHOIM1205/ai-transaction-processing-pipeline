"""Job endpoints: upload, list, status.

WHY THIS FILE EXISTS
--------------------
The HTTP layer for jobs. Handlers are deliberately thin: they parse/validate the
request, delegate to the service/repository layers, translate domain errors into
HTTP responses, and shape the output via response models. No business logic or
SQL lives here.

Endpoints: POST /jobs/upload, GET /jobs, GET /jobs/{id}/status,
GET /jobs/{id}/results.
"""

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import PaginationParams, get_db
from app.core.logging import get_logger
from app.models.enums import JobStatus
from app.repositories import job_repo, transaction_repo
from app.schemas.job import (
    JobCreatedOut,
    JobListItem,
    JobListOut,
    JobResultsOut,
    JobStatusOut,
    TransactionPage,
)
from app.schemas.summary import JobSummaryOut
from app.schemas.transaction import TransactionOut
from app.services import job_service
from app.services.errors import UploadError

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = get_logger(__name__)


@router.post(
    "/upload",
    response_model=JobCreatedOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a transactions CSV and create a processing job",
    description=(
        "Accepts a CSV file, validates its extension and header, stores it, and "
        "creates a job in the `pending` state. Returns the `job_id` immediately; "
        "processing happens asynchronously on the Celery worker."
    ),
    responses={
        202: {"description": "Job accepted and created in `pending` state."},
        400: {"description": "Invalid file (extension, empty, or bad/missing CSV header)."},
        413: {"description": "Uploaded file exceeds the size limit."},
        422: {"description": "Malformed multipart request (e.g. missing file field)."},
    },
)
async def upload_job(
    file: UploadFile = File(..., description="The transactions CSV to process."),
    db: Session = Depends(get_db),
) -> JobCreatedOut:
    content = await file.read()
    try:
        job = job_service.create_job_from_upload(db, file.filename, content)
    except UploadError as exc:
        # Domain error → its declared HTTP status. One handler, no per-type branching.
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return JobCreatedOut.model_validate(job)


@router.get(
    "",
    response_model=JobListOut,
    summary="List jobs",
    description="Lists jobs newest-first, with optional `status` filter and pagination.",
    responses={
        200: {"description": "A page of jobs with pagination metadata."},
        422: {"description": "Invalid query parameter (e.g. unknown status value)."},
    },
)
def list_jobs(
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    status_filter: JobStatus | None = Query(
        None,
        alias="status",
        description="Filter by job status (pending/processing/completed/failed).",
    ),
) -> JobListOut:
    items, total = job_repo.list_jobs(
        db, status=status_filter, limit=pagination.limit, offset=pagination.offset
    )
    return JobListOut(
        items=[JobListItem.model_validate(job) for job in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/{job_id}/status",
    response_model=JobStatusOut,
    summary="Get job status",
    description=(
        "Returns the job's current status and row counts. Once the job is "
        "`completed`, the `summary` field is populated; otherwise it is null."
    ),
    responses={
        200: {"description": "Current job status."},
        404: {"description": "No job exists with the given id."},
        422: {"description": "`job_id` is not a valid UUID."},
    },
)
def get_job_status(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> JobStatusOut:
    job = job_repo.get_job(db, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found.",
        )
    return JobStatusOut.model_validate(job)


@router.get(
    "/{job_id}/results",
    response_model=JobResultsOut,
    summary="Get full job results",
    description=(
        "Returns the job details, processing status, the generated summary, and "
        "the cleaned transactions (paginated). Only available once the job has "
        "`completed`; otherwise returns 409 so callers keep polling status."
    ),
    responses={
        200: {"description": "Job details, summary, and a page of transactions."},
        404: {"description": "No job exists with the given id."},
        409: {"description": "Job is not completed yet; results are not ready."},
        422: {"description": "`job_id` is not a valid UUID, or bad pagination params."},
    },
)
def get_job_results(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
) -> JobResultsOut:
    job = job_repo.get_job(db, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found.",
        )
    if job.status != JobStatus.completed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job not completed; current status: {job.status.value}.",
        )

    items, total = transaction_repo.list_for_job(
        db, job_id, limit=pagination.limit, offset=pagination.offset
    )
    return JobResultsOut(
        job_id=job.id,
        filename=job.filename,
        status=job.status,
        row_count_raw=job.row_count_raw,
        row_count_clean=job.row_count_clean,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
        summary=JobSummaryOut.model_validate(job.summary) if job.summary else None,
        transactions=TransactionPage(
            items=[TransactionOut.model_validate(t) for t in items],
            total=total,
            limit=pagination.limit,
            offset=pagination.offset,
        ),
    )
