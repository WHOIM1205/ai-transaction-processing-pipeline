"""Celery application.

WHY THIS FILE EXISTS
--------------------
The worker container runs `celery -A app.workers.celery_app:celery_app worker`.
This module defines that Celery application object and its configuration. It is
also imported by the API to enqueue tasks (`process_job.delay(...)`). Tasks live
in `app/workers/tasks.py` and are registered via `include` below.
"""

from celery import Celery

from app.core.config import get_settings
from app.core.logging import setup_logging

# The worker is a separate process, so it must configure its own logging.
setup_logging()

settings = get_settings()

# Broker = where task messages are queued (Redis). Backend = where task
# state/results are stored (also Redis). Both come from central config.
celery_app = Celery(
    "transaction_pipeline",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    # Import the task module on worker startup so tasks are registered. (The API
    # registers them by importing the task in the upload service.)
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # --- Serialization: JSON only (safe, language-agnostic, no pickle). ------
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # --- Time: operate in UTC to avoid ambiguity across hosts. ---------------
    timezone="UTC",
    enable_utc=True,
    # --- Reliability defaults. -----------------------------------------------
    # Acknowledge a task only AFTER it finishes, so a crashed worker causes the
    # task to be redelivered rather than silently lost.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One task at a time per worker process — fair scheduling for the long,
    # CPU/IO-bound processing jobs this system will run.
    worker_prefetch_multiplier=1,
    # Don't let task results accumulate in Redis forever.
    result_expires=3600,
)

# Tasks live in app/workers/tasks.py and are registered via `include` above.
