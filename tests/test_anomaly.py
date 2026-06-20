"""Unit tests for anomaly detection (pure function, no DB)."""

from decimal import Decimal

from app.core.constants import ANOMALY_AMOUNT_OUTLIER, ANOMALY_USD_DOMESTIC
from app.models.enums import Currency, TransactionStatus
from app.services.anomaly import detect_anomalies
from app.services.dto import PipelineRow


def _row(merchant, amount, account="ACC1", currency=Currency.INR):
    return PipelineRow(
        txn_id=None,
        date=None,
        merchant=merchant,
        amount=Decimal(str(amount)),
        currency=currency,
        status=TransactionStatus.SUCCESS,
        category="X",
        account_id=account,
        notes=None,
    )


def test_amount_outlier_rule():
    # Median of [100,100,100,100] = 100; 3x = 300. 1000 > 300 -> flagged; 100 -> not.
    rows = [_row("Amazon", 100) for _ in range(4)] + [_row("Amazon", 1000)]
    detect_anomalies(rows)
    outlier = rows[-1]
    assert outlier.is_anomaly is True
    assert ANOMALY_AMOUNT_OUTLIER in outlier.anomaly_reason
    assert all(not r.is_anomaly for r in rows[:4])


def test_usd_domestic_merchant_rule():
    rows = [
        _row("Swiggy", 100, currency=Currency.USD),    # domestic + USD -> flagged
        _row("Swiggy", 100, currency=Currency.INR),    # INR -> fine
        _row("MakeMyTrip", 100, currency=Currency.USD),  # not domestic -> fine
    ]
    detect_anomalies(rows)
    assert rows[0].is_anomaly is True
    assert ANOMALY_USD_DOMESTIC in rows[0].anomaly_reason
    assert rows[1].is_anomaly is False
    assert rows[2].is_anomaly is False
