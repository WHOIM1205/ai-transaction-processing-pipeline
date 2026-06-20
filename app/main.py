"""FastAPI application entrypoint.

WHY THIS FILE EXISTS
--------------------
This is the object uvicorn loads (`app.main:app`). It wires the application
together: configure logging, build the FastAPI instance, and register the health
and jobs routers.

Kept deliberately thin — an application factory (`create_app`) plus a module
-level `app`. The factory pattern makes the app reconstructable in tests with
overridden settings.
"""

from pathlib import Path

from fastapi import FastAPI

from app.api.routes_health import router as health_router
from app.api.routes_jobs import router as jobs_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

# Configure logging before anything else logs, so startup lines are formatted.
setup_logging()
logger = get_logger(__name__)
settings = get_settings()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application instance."""
    application = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="AI-Powered Transaction Processing Pipeline.",
    )

    # Best-effort: pre-create the uploads directory. Failure here is non-fatal —
    # the upload service also ensures the directory at write time and will surface
    # a real error then. This keeps startup robust if the path isn't yet writable.
    try:
        Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not pre-create UPLOAD_DIR %s: %s", settings.UPLOAD_DIR, exc)

    # Health probes + the jobs API (upload / list / status / results).
    application.include_router(health_router)
    application.include_router(jobs_router)

    logger.info(
        "Application initialised: name=%s env=%s", settings.APP_NAME, settings.APP_ENV
    )
    return application


# Module-level instance referenced by uvicorn and the Docker CMD.
app = create_app()
