"""Stage 6: persist pipeline results.

WHY THIS FILE EXISTS
--------------------
Maps the in-memory `PipelineResult` to ORM rows and writes everything for a job
in ONE unit of work: clear any prior results (idempotent re-run), insert the
transactions and the summary, and update the job's counts. The caller owns the
commit so the whole set is atomic — partial results can never be observed.
"""

from app.core.logging import get_logger
from app.models.enums import RiskLevel
from app.models.job import Job
from app.models.summary import JobSummary
from app.models.transaction import Transaction
from app.repositories import transaction_repo
from app.services.dto import PipelineResult

logger = get_logger(__name__)


def _to_orm_transaction(job_id, row) -> Transaction:
    return Transaction(
        job_id=job_id,
        txn_id=row.txn_id,
        date=row.date,
        merchant=row.merchant,
        amount=row.amount,
        currency=row.currency,
        status=row.status,
        category=row.category,
        account_id=row.account_id,
        notes=row.notes,
        is_anomaly=row.is_anomaly,
        # store NULL rather than an empty array when there are no reasons
        anomaly_reason=row.anomaly_reason or None,
        llm_category=row.llm_category,
        llm_failed=row.llm_failed,
    )


def persist_results(db, job: Job, result: PipelineResult) -> None:
    """Write transactions + summary and update the job. Does NOT commit."""
    transaction_repo.delete_results_for_job(db, job.id)

    transactions = [_to_orm_transaction(job.id, row) for row in result.rows]
    transaction_repo.bulk_insert_transactions(db, transactions)

    s = result.summary
    db.add(
        JobSummary(
            job_id=job.id,
            total_spend_inr=s.total_spend_inr,
            total_spend_usd=s.total_spend_usd,
            top_merchants=s.top_merchants,
            category_breakdown=s.category_breakdown,
            anomaly_count=s.anomaly_count,
            narrative=s.narrative,
            risk_level=RiskLevel(s.risk_level) if s.risk_level else None,
        )
    )

    job.row_count_raw = result.row_count_raw
    job.row_count_clean = result.row_count_clean

    logger.info(
        "persistence: job %s -> %d transactions, summary written",
        job.id,
        len(transactions),
    )
