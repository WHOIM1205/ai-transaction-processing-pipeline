"""Unit tests for CSV cleaning (pure function, no DB)."""

import datetime
from decimal import Decimal

from app.core.constants import UNCATEGORISED
from app.models.enums import Currency, TransactionStatus
from app.services.cleaning import clean_rows

HEADER = "txn_id,date,merchant,amount,currency,status,category,account_id,notes\n"


def _csv(*data_rows: str) -> bytes:
    return (HEADER + "\n".join(data_rows) + "\n").encode("utf-8")


def test_exact_duplicate_rows_removed():
    content = _csv(
        "TXN1,04-09-2024,Flipkart,100.00,INR,SUCCESS,Shopping,ACC1,",
        "TXN1,04-09-2024,Flipkart,100.00,INR,SUCCESS,Shopping,ACC1,",  # exact dup
        "TXN2,04-09-2024,Amazon,50.00,INR,SUCCESS,Shopping,ACC1,",
    )
    rows, raw, clean = clean_rows(content)
    assert raw == 3
    assert clean == 2
    assert len(rows) == 2


def test_date_normalisation_handles_all_formats():
    content = _csv(
        "TXN1,04-09-2024,Flipkart,100.00,INR,SUCCESS,Shopping,ACC1,",   # DD-MM-YYYY
        "TXN2,2024/02/05,Amazon,100.00,INR,SUCCESS,Shopping,ACC1,",     # YYYY/MM/DD
        "TXN3,2024-07-15,IRCTC,100.00,INR,SUCCESS,Travel,ACC1,",        # YYYY-MM-DD
        "TXN4,not-a-date,Ola,100.00,INR,SUCCESS,Transport,ACC1,",       # unparseable
    )
    rows, _, _ = clean_rows(content)
    by_id = {r.txn_id: r for r in rows}
    assert by_id["TXN1"].date == datetime.date(2024, 9, 4)
    assert by_id["TXN2"].date == datetime.date(2024, 2, 5)
    assert by_id["TXN3"].date == datetime.date(2024, 7, 15)
    assert by_id["TXN4"].date is None  # unparseable -> null, row still kept


def test_missing_category_filled_with_uncategorised():
    content = _csv("TXN1,04-09-2024,Flipkart,100.00,INR,SUCCESS,,ACC1,")
    rows, _, _ = clean_rows(content)
    assert rows[0].category == UNCATEGORISED


def test_amount_and_casing_normalisation():
    content = _csv("TXN1,04-09-2024,Swiggy,$1234.5,inr,success,Food,ACC1,")
    rows, _, _ = clean_rows(content)
    row = rows[0]
    assert row.amount == Decimal("1234.50")          # $ stripped, 2dp
    assert row.currency == Currency.INR              # 'inr' -> INR
    assert row.status == TransactionStatus.SUCCESS   # 'success' -> SUCCESS
