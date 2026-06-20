"""Stage 4: LLM category classification (batched, retried, graceful).

WHY THIS FILE EXISTS
--------------------
Assigns categories to rows that were uncategorised after cleaning, using the
LLM. Batches calls (never one-per-row), retries with backoff, validates results
against the allowed category set, and degrades gracefully: a batch that
exhausts retries (or a missing client) marks its rows `llm_failed` and the job
continues.
"""

from collections.abc import Iterator

from app.core.constants import ALLOWED_CATEGORIES, UNCATEGORISED
from app.core.logging import get_logger
from app.llm.client import LLMClient
from app.llm.retry import with_retries
from app.services.dto import PipelineRow

logger = get_logger(__name__)

_ALLOWED = set(ALLOWED_CATEGORIES)


def _batches(rows: list[PipelineRow], size: int) -> Iterator[list[PipelineRow]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def classify_uncategorised(
    rows: list[PipelineRow],
    llm_client: LLMClient | None,
    *,
    batch_size: int,
    max_retries: int,
    base_delay: float,
) -> None:
    """Populate `llm_category` / `llm_failed` for uncategorised rows in place."""
    targets = [r for r in rows if r.category == UNCATEGORISED]
    if not targets:
        logger.info("classifier: no uncategorised rows; skipping LLM")
        return

    if llm_client is None:
        logger.warning(
            "classifier: LLM unavailable; marking %d rows llm_failed", len(targets)
        )
        for row in targets:
            row.llm_failed = True
        return

    classified = 0
    for batch in _batches(targets, batch_size):
        # batch-local string refs map results back without trusting order
        items = [
            {
                "ref": str(i),
                "merchant": row.merchant,
                "amount": str(row.amount),
                "notes": row.notes or "",
            }
            for i, row in enumerate(batch)
        ]
        try:
            result = with_retries(
                lambda items=items: llm_client.classify(items),
                max_attempts=max_retries,
                base_delay=base_delay,
                logger=logger,
            )
        except Exception:  # noqa: BLE001 - degrade this batch, keep the job alive
            logger.exception("classifier: batch failed after retries; marking llm_failed")
            for row in batch:
                row.llm_failed = True
            continue

        for i, row in enumerate(batch):
            category = result.get(str(i))
            # Coerce anything out-of-contract (or missing) to "Other".
            row.llm_category = category if category in _ALLOWED else "Other"
            classified += 1

    logger.info("classifier: classified %d/%d uncategorised rows", classified, len(targets))
