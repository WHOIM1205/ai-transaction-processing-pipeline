"""Models package.

WHY THIS FILE EXISTS
--------------------
Importing this package registers every ORM model on `Base.metadata`. Both
Alembic's `env.py` (for autogenerate) and any code that needs the models import
from here, guaranteeing all tables are known to the metadata in one import.

Import order is leaf-last is irrelevant here because relationships use string
forward references resolved via the shared declarative registry.
"""

from app.db.base import Base
from app.models.enums import Currency, JobStatus, RiskLevel, TransactionStatus
from app.models.job import Job
from app.models.summary import JobSummary
from app.models.transaction import Transaction

__all__ = [
    "Base",
    "Job",
    "Transaction",
    "JobSummary",
    "JobStatus",
    "TransactionStatus",
    "Currency",
    "RiskLevel",
]
