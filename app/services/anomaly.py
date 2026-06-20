"""Stage 3: deterministic anomaly detection (no LLM).

WHY THIS FILE EXISTS
--------------------
Flags suspicious rows using two cheap, explainable rules, annotating each
`PipelineRow` in place. Runs before any LLM cost.

  Rule 1 — statistical outlier: amount > 3 × the account's median amount.
  Rule 2 — currency mismatch: USD charged on a domestic-only merchant.

A row may trip both rules, so reasons accumulate in `anomaly_reason`.
"""

import statistics
from decimal import Decimal

from app.core.constants import (
    ANOMALY_AMOUNT_OUTLIER,
    ANOMALY_USD_DOMESTIC,
    DOMESTIC_ONLY_MERCHANTS,
)
from app.core.logging import get_logger
from app.models.enums import Currency
from app.services.dto import PipelineRow

logger = get_logger(__name__)


def detect_anomalies(rows: list[PipelineRow]) -> int:
    """Annotate rows with anomaly flags/reasons. Returns the flagged count."""
    # Per-account median amount (computed over all of the account's rows).
    amounts_by_account: dict[str, list[Decimal]] = {}
    for row in rows:
        amounts_by_account.setdefault(row.account_id, []).append(row.amount)
    medians = {
        account: statistics.median(amounts)
        for account, amounts in amounts_by_account.items()
    }

    flagged = 0
    for row in rows:
        reasons: list[str] = []

        median = medians.get(row.account_id)
        if median is not None and median > 0 and row.amount > 3 * median:
            reasons.append(ANOMALY_AMOUNT_OUTLIER)

        if (
            row.currency == Currency.USD
            and row.merchant.strip().lower() in DOMESTIC_ONLY_MERCHANTS
        ):
            reasons.append(ANOMALY_USD_DOMESTIC)

        if reasons:
            row.is_anomaly = True
            row.anomaly_reason = reasons
            flagged += 1

    logger.info("anomaly: flagged %d/%d rows", flagged, len(rows))
    return flagged
