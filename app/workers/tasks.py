"""Celery tasks.

WHY THIS FILE EXISTS
--------------------
Defines the asynchronous work the worker executes. `process_job` is the single
entry point enqueued by the upload endpoint; it owns the job's state machine
(pending → processing → completed/failed).

The task claims the job, runs the processing pipeline (clean → anomaly →
classify → summarise), persists the transactions and summary, and marks the job
completed (or failed). It is idempotent and safe against duplicate deliveries.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.constants import ALLOWED_UPLOAD_EXTENSION
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.llm.client import get_llm_client
from app.models.enums import JobStatus
from app.models.job import Job
from app.services import persistence, pipeline
from app.workers.celery_app import celery_app

settings = get_settings()
logger = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.process_job", bind=True)
def process_job(self, job_id: str) -> dict:
    """Process an uploaded job.

    State machine, with idempotency and transaction safety:
      * Phase A (claim): lock the job row `FOR UPDATE`; if it is already
        processing/completed, skip (safe against duplicate deliveries); else
        flip pending/failed → processing and commit so the state is visible.
      * Phase B (work): read the file and compute row_count_raw — no DB lock
        held during the (future, potentially slow) work.
      * Phase C (finalize): write the result and flip → completed in one commit.
      * On any error: a fresh transaction records status=failed + error_message.
    """
    logger.info("process_job: received job_id=%s (task_id=%s)", job_id, self.request.id)
    job_uuid = uuid.UUID(job_id)

    # --- Phase A: claim the job (idempotency guard) --------------------------
    with SessionLocal() as db:
        job = db.execute(
            select(Job).where(Job.id == job_uuid).with_for_update()
        ).scalar_one_or_none()

        if job is None:
            logger.error("process_job: job %s not found; nothing to do", job_id)
            return {"job_id": job_id, "status": "not_found"}

        if job.status in (JobStatus.processing, JobStatus.completed):
            logger.warning(
                "process_job: job %s already %s; skipping (idempotent)",
                job_id,
                job.status.value,
            )
            return {"job_id": job_id, "status": job.status.value, "skipped": True}

        job.status = JobStatus.processing
        job.error_message = None  # clear any prior failure if reprocessing
        db.commit()
        logger.info("process_job: job %s -> processing", job_id)

    # --- Phase B: run the processing pipeline (no DB lock held) --------------
    try:
        upload_path = Path(settings.UPLOAD_DIR) / f"{job_id}{ALLOWED_UPLOAD_EXTENSION}"
        if not upload_path.is_file():
            raise FileNotFoundError(f"Uploaded file not found: {upload_path}")

        content = upload_path.read_bytes()
        llm_client = get_llm_client()  # None when Gemini is not configured
        result = pipeline.run_pipeline(content, llm_client)

        # --- Phase C: persist results + finalize, atomically ----------------
        with SessionLocal() as db:
            job = db.get(Job, job_uuid)
            persistence.persist_results(db, job, result)
            job.status = JobStatus.completed
            job.completed_at = datetime.now(timezone.utc)
            db.commit()

        llm_failed = sum(1 for r in result.rows if r.llm_failed)
        logger.info(
            "process_job: job %s -> completed (raw=%d clean=%d anomalies=%d llm_failed=%d)",
            job_id,
            result.row_count_raw,
            result.row_count_clean,
            result.summary.anomaly_count,
            llm_failed,
        )
        return {
            "job_id": job_id,
            "status": "completed",
            "row_count_raw": result.row_count_raw,
            "row_count_clean": result.row_count_clean,
            "anomaly_count": result.summary.anomaly_count,
            "llm_failed": llm_failed,
        }

    except Exception as exc:  # noqa: BLE001 - record failure, then surface it
        logger.exception("process_job: job %s failed during processing", job_id)
        with SessionLocal() as db:
            job = db.get(Job, job_uuid)
            if job is not None:
                job.status = JobStatus.failed
                job.error_message = str(exc)[:1000]
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
        raise  # let Celery mark the task as failed
