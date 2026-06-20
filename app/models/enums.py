"""Enumerations shared by the ORM models and the Pydantic schemas.

WHY THIS FILE EXISTS
--------------------
The same closed value-sets (job status, transaction status, currency, risk
level) are needed in two places: the database column types (SQLAlchemy `Enum`)
and the API contracts (Pydantic). Defining them ONCE here guarantees the
database and the API can never drift apart.

Each enum subclasses `str` so values serialise cleanly to JSON and compare to
plain strings. Member NAME == member VALUE on purpose, which keeps the native
PostgreSQL enum labels identical to the Python values (no name/value ambiguity
in SQLAlchemy's `Enum` handling).
"""

import enum


class JobStatus(str, enum.Enum):
    """Lifecycle of a processing job (drives the polling API)."""

    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class TransactionStatus(str, enum.Enum):
    """Normalised (upper-cased) transaction status from the source CSV."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PENDING = "PENDING"


class Currency(str, enum.Enum):
    """Normalised (upper-cased) currency codes present in the data."""

    INR = "INR"
    USD = "USD"


class RiskLevel(str, enum.Enum):
    """Overall risk classification assigned in the job summary."""

    low = "low"
    medium = "medium"
    high = "high"
