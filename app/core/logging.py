"""Logging configuration.

WHY THIS FILE EXISTS
--------------------
The API and the worker run as separate processes/containers. Configuring
logging in one shared helper guarantees both emit logs in the same format, at
the same level, to stdout — which is what Docker captures (`docker compose
logs`). Centralising it now means later phases can add a `job_id` to every log
line in exactly one place.
"""

import logging
import sys

from app.core.config import get_settings

# A flat, greppable line format. `name` carries the module logger so it is easy
# to see whether a line came from the API, the worker, or a library.
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging() -> None:
    """Configure root logging once, at process startup.

    Called by both the FastAPI entrypoint and the Celery app so the two
    processes log identically. `force=True` resets any handlers installed by
    uvicorn/celery so our format wins.
    """
    settings = get_settings()
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        stream=sys.stdout,
        force=True,
    )

    # Keep uvicorn's access/error logs aligned with our level so output is
    # consistent rather than mixing two configurations.
    for noisy in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(noisy).setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Thin wrapper so call sites depend on this module
    (a stable seam) rather than the stdlib directly."""
    return logging.getLogger(name)
