"""Database engine and session management.

WHY THIS FILE EXISTS
--------------------
Creating the SQLAlchemy `Engine` is expensive and must happen exactly once per
process (it owns the connection pool). This module owns that single engine and
a session factory, and exposes a `get_db` dependency that hands out a scoped
session per request and guarantees it is closed afterwards.

Used by:
  * FastAPI routes — via `Depends(get_db)`,
  * the health check — to ping the database,
  * Celery tasks — to open sessions inside the worker.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# One engine per process. `pool_pre_ping` transparently checks a connection is
# alive before use, which prevents "server closed the connection" errors after
# Postgres restarts or idle timeouts — important in a containerised setup.
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

# Session factory. `expire_on_commit=False` keeps ORM objects usable after a
# commit (convenient for returning data); `autoflush=False` makes flush timing
# explicit rather than implicit.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and always close it.

    Designed as a FastAPI dependency: FastAPI runs the generator, injects the
    session into the route, then resumes it after the response to run the
    `finally` block. The same factory is reused directly by the worker.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
