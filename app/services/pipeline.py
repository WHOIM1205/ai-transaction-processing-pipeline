"""Pipeline orchestrator.

WHY THIS FILE EXISTS
--------------------
Runs the in-memory stages in order and returns a `PipelineResult` for the worker
to persist. Keeps the Celery task thin and makes the whole transform testable in
isolation: `run_pipeline(bytes, llm_client) -> PipelineResult`, no DB required.
"""

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.client import LLMClient
from app.services import anomaly, classifier, cleaning, summary
from app.services.dto import PipelineResult

logger = get_logger(__name__)


def run_pipeline(content: bytes, llm_client: LLMClient | None) -> PipelineResult:
    """Clean → detect anomalies → classify (LLM) → summarise."""
    settings = get_settings()

    rows, row_count_raw, row_count_clean = cleaning.clean_rows(content)
    anomaly.detect_anomalies(rows)
    classifier.classify_uncategorised(
        rows,
        llm_client,
        batch_size=settings.LLM_BATCH_SIZE,
        max_retries=settings.LLM_MAX_RETRIES,
        base_delay=settings.LLM_RETRY_BASE_DELAY,
    )
    summary_data = summary.build_summary(
        rows,
        llm_client,
        max_retries=settings.LLM_MAX_RETRIES,
        base_delay=settings.LLM_RETRY_BASE_DELAY,
    )

    logger.info(
        "pipeline: complete (clean=%d, anomalies=%d, llm_failed=%d)",
        row_count_clean,
        sum(1 for r in rows if r.is_anomaly),
        sum(1 for r in rows if r.llm_failed),
    )
    return PipelineResult(
        rows=rows,
        row_count_raw=row_count_raw,
        row_count_clean=row_count_clean,
        summary=summary_data,
    )
