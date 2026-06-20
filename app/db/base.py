"""SQLAlchemy declarative base.

WHY THIS FILE EXISTS
--------------------
All ORM models (Job, Transaction, JobSummary) inherit from this single
declarative base so they share one metadata registry. That registry is what
Alembic introspects to autogenerate migrations.

Keeping `Base` in its own tiny module (rather than in `session.py`) avoids an
import cycle: models import `Base`, while `session.py` imports the engine —
the two concerns stay independent.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base class shared by every ORM model in the project."""

    pass
