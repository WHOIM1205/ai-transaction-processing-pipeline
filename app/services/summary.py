"""Stage 5: summary aggregation + LLM narrative.

WHY THIS FILE EXISTS
--------------------
Computes the deterministic aggregates in-house (trustworthy, free) and uses the
LLM only for the prose narrative and the risk judgement. If the LLM is
unavailable or fails, the aggregates are still produced and narrative/risk_level
are left null (graceful degradation).

"Spend" = transactions with status SUCCESS. INR and USD are reported separately
(no FX conversion — mixing currencies would be wrong).
"""

from decimal import Decimal

from app.core.logging import get_logger
from app.llm.client import LLMClient
from app.llm.retry import with_retries
from app.models.enums import Currency, TransactionStatus
from app.services.dto import PipelineRow, SummaryData

logger = get_logger(__name__)

_RISK_LEVELS = {"low", "medium", "high"}
_ZERO = Decimal("0.00")


def _round2(value: Decimal) -> float:
    """JSON-safe float rounded to 2dp (for JSONB columns)."""
    return float(value.quantize(Decimal("0.01")))


def build_summary(
    rows: list[PipelineRow],
    llm_client: LLMClient | None,
    *,
    max_retries: int,
    base_delay: float,
) -> SummaryData:
    success = [r for r in rows if r.status == TransactionStatus.SUCCESS]

    total_inr = sum((r.amount for r in success if r.currency == Currency.INR), _ZERO)
    total_usd = sum((r.amount for r in success if r.currency == Currency.USD), _ZERO)

    # Merchant totals (by SUCCESS spend) and category breakdown (effective cat).
    merchant_totals: dict[str, list] = {}  # merchant -> [total: Decimal, count: int]
    category_totals: dict[str, Decimal] = {}
    for row in success:
        bucket = merchant_totals.setdefault(row.merchant, [_ZERO, 0])
        bucket[0] += row.amount
        bucket[1] += 1
        category_totals[row.effective_category] = (
            category_totals.get(row.effective_category, _ZERO) + row.amount
        )

    top_merchants = [
        {"merchant": merchant, "total": _round2(total), "count": count}
        for merchant, (total, count) in sorted(
            merchant_totals.items(), key=lambda kv: kv[1][0], reverse=True
        )[:3]
    ]
    category_breakdown = {cat: _round2(total) for cat, total in category_totals.items()}
    anomaly_count = sum(1 for r in rows if r.is_anomaly)

    narrative: str | None = None
    risk_level: str | None = None

    aggregates = {
        "total_spend_inr": _round2(total_inr),
        "total_spend_usd": _round2(total_usd),
        "top_merchants": top_merchants,
        "category_breakdown": category_breakdown,
        "anomaly_count": anomaly_count,
    }

    if llm_client is None:
        logger.warning("summary: LLM unavailable; narrative/risk_level left null")
    else:
        try:
            result = with_retries(
                lambda: llm_client.summarize(aggregates),
                max_attempts=max_retries,
                base_delay=base_delay,
                logger=logger,
            )
            narrative = result.get("narrative")
            candidate = result.get("risk_level")
            risk_level = candidate if candidate in _RISK_LEVELS else None
        except Exception:  # noqa: BLE001 - keep aggregates, drop narrative/risk
            logger.exception("summary: LLM narrative failed; leaving narrative/risk null")

    return SummaryData(
        total_spend_inr=total_inr,
        total_spend_usd=total_usd,
        top_merchants=top_merchants,
        category_breakdown=category_breakdown,
        anomaly_count=anomaly_count,
        narrative=narrative,
        risk_level=risk_level,
    )
