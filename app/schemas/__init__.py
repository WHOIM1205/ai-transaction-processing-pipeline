"""Pydantic schemas package — the API's response contracts.

WHY THIS FILE EXISTS
--------------------
Separates the wire format (what the API returns) from the ORM models (how data
is stored). Routes declare these as `response_model`s, keeping the wire contract
explicit and decoupled from the ORM.

All schemas set `from_attributes=True`, so they can be built directly from ORM
instances (`TransactionOut.model_validate(orm_obj)`).
"""

from app.schemas.job import (
    JobCreatedOut,
    JobListItem,
    JobListOut,
    JobOut,
    JobResultsOut,
    JobStatusOut,
    TransactionPage,
)
from app.schemas.summary import JobSummaryOut, TopMerchant
from app.schemas.transaction import TransactionOut

__all__ = [
    "JobCreatedOut",
    "JobOut",
    "JobListItem",
    "JobListOut",
    "JobStatusOut",
    "JobResultsOut",
    "TransactionPage",
    "JobSummaryOut",
    "TopMerchant",
    "TransactionOut",
]
