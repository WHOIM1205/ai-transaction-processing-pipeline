"""Health check endpoints.

WHY THIS FILE EXISTS
--------------------
Docker Compose, load balancers, and humans all need a cheap way to ask "is the
service alive, and are its dependencies reachable?".

Two distinct checks, following the standard liveness/readiness split:
  * GET /health        — LIVENESS: the process is up and serving. Cheap, no I/O.
                         Used by the container healthcheck.
  * GET /health/ready  — READINESS: Postgres and Redis are reachable. Returns
                         503 if a dependency is down, so orchestration can hold
                         traffic until the stack is fully wired.
"""

import redis
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import engine

router = APIRouter(tags=["health"])
logger = get_logger(__name__)
settings = get_settings()


@router.get("/health", summary="Liveness probe")
def health() -> dict:
    """Return 200 if the API process is running. No external calls — this must
    stay fast and dependency-free so it never reports the app as down just
    because a downstream is briefly unavailable."""
    return {"status": "ok", "service": settings.APP_NAME}


@router.get("/health/ready", summary="Readiness probe")
def readiness(response: Response) -> dict:
    """Check that Postgres and Redis are reachable.

    Returns 200 + per-component status when all dependencies are healthy,
    otherwise 503 so callers can distinguish "alive" from "ready to serve".
    """
    components: dict[str, str] = {}
    healthy = True

    # --- PostgreSQL ----------------------------------------------------------
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        components["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report any failure, don't crash
        logger.warning("Readiness: database unavailable: %s", exc)
        components["database"] = "unavailable"
        healthy = False

    # --- Redis ---------------------------------------------------------------
    try:
        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        client.ping()
        components["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Readiness: redis unavailable: %s", exc)
        components["redis"] = "unavailable"
        healthy = False

    response.status_code = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if healthy else "degraded", "components": components}
