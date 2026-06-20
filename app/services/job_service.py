"""Job orchestration: upload validation + persistence.

WHY THIS FILE EXISTS
--------------------
Holds the business logic for accepting an upload, independent of HTTP. The route
hands raw bytes + filename here; this module validates them, persists a `Job`,
and writes the file to the uploads directory using the claim-check pattern (the
worker later reads the file by job id), then enqueues the processing task.
"""

import csv
import io
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.constants import ALLOWED_UPLOAD_EXTENSION, REQUIRED_CSV_COLUMNS
from app.core.logging import get_logger
from app.models.job import Job
from app.repositories import job_repo
from app.services.errors import (
    EmptyFileError,
    FileTooLargeError,
    InvalidCsvError,
    InvalidFileExtension,
)
from app.workers.tasks import process_job

settings = get_settings()
logger = get_logger(__name__)


def _validate_extension(filename: str | None) -> None:
    if not filename or not filename.lower().endswith(ALLOWED_UPLOAD_EXTENSION):
        raise InvalidFileExtension(
            f"Only {ALLOWED_UPLOAD_EXTENSION} files are accepted."
        )


def _validate_size(content: bytes) -> None:
    if len(content) == 0:
        raise EmptyFileError("Uploaded file is empty.")
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise FileTooLargeError(
            f"File exceeds the maximum allowed size of "
            f"{settings.MAX_UPLOAD_BYTES} bytes."
        )


def _validate_csv_header(content: bytes) -> None:
    """Ensure the file decodes as UTF-8 text and its header has every required
    column. Header comparison is case-insensitive and whitespace-tolerant."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidCsvError("File is not valid UTF-8 text.") from exc

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise InvalidCsvError("CSV has no header row.") from None

    present = {col.strip().lower() for col in header}
    missing = REQUIRED_CSV_COLUMNS - present
    if missing:
        raise InvalidCsvError(
            f"Missing required columns: {', '.join(sorted(missing))}."
        )


def create_job_from_upload(db: Session, filename: str | None, content: bytes) -> Job:
    """Validate an uploaded CSV, persist a pending job, and store the file.

    Validation order is cheapest-first (extension → size → header parse) so
    obviously-wrong requests are rejected before any parsing work. The job row
    and the on-disk file are committed together; if the file write fails the job
    is rolled back so no orphaned record is left behind.
    """
    _validate_extension(filename)
    _validate_size(content)
    _validate_csv_header(content)

    job = job_repo.create_job(db, filename=filename)  # flush → job.id available

    destination = Path(settings.UPLOAD_DIR) / f"{job.id}{ALLOWED_UPLOAD_EXTENSION}"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    except OSError as exc:
        db.rollback()
        logger.exception("Failed to write upload for job to %s", destination)
        raise InvalidCsvError("Could not store the uploaded file.") from exc

    db.commit()
    db.refresh(job)
    logger.info("Created job id=%s filename=%s", job.id, job.filename)

    # Enqueue asynchronous processing. The job is already durably persisted as
    # `pending`, so the worker can be picked up independently. Only the job id is
    # sent (claim-check) — the worker reloads the file and row from source.
    async_result = process_job.delay(str(job.id))
    logger.info("Enqueued process_job for job id=%s (task_id=%s)", job.id, async_result.id)

    return job
