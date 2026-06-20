"""Stage 1–2: CSV parsing + cleaning/normalisation.

WHY THIS FILE EXISTS
--------------------
Turns the raw, dirty CSV bytes into a canonical, typed, de-duplicated list of
`PipelineRow`. Pure function over bytes → rows, so it is fully unit-testable
without a DB or worker.

Cleaning rules (from the brief, grounded in transactions.csv):
  * dates → ISO `date` (handles DD-MM-YYYY, YYYY/MM/DD, and YYYY-MM-DD)
  * amounts → Decimal with `$`, commas, spaces stripped
  * currency / status → upper-cased and validated against the enums
  * blank category → "Uncategorised"
  * exact duplicate rows removed (on the fully-normalised tuple)
"""

import csv
import io
from decimal import Decimal, InvalidOperation

from app.core.constants import UNCATEGORISED
from app.core.logging import get_logger
from app.models.enums import Currency, TransactionStatus
from app.services.dto import PipelineRow

logger = get_logger(__name__)

# Order matters only for readability; each row is tried against all formats.
_DATE_FORMATS = ("%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d")
_CENTS = Decimal("0.01")


def _clean_str(value: str | None) -> str:
    return value.strip() if value else ""


def _none_if_blank(value: str | None) -> str | None:
    cleaned = _clean_str(value)
    return cleaned or None


def _parse_date(raw: str | None):
    text = _clean_str(raw)
    if not text:
        return None
    import datetime  # local import keeps the module's top-level imports lean

    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    logger.warning("cleaning: unparseable date %r -> null", raw)
    return None


def _parse_amount(raw: str | None) -> Decimal | None:
    text = _clean_str(raw).replace("$", "").replace(",", "").replace(" ", "")
    if not text:
        return None
    try:
        return Decimal(text).quantize(_CENTS)
    except (InvalidOperation, ValueError):
        logger.warning("cleaning: unparseable amount %r -> row dropped", raw)
        return None


def _normalise_currency(raw: str | None) -> Currency | None:
    text = _clean_str(raw).upper()
    try:
        return Currency(text)
    except ValueError:
        logger.warning("cleaning: unknown currency %r -> row dropped", raw)
        return None


def _normalise_status(raw: str | None) -> TransactionStatus | None:
    text = _clean_str(raw).upper()
    try:
        return TransactionStatus(text)
    except ValueError:
        logger.warning("cleaning: unknown status %r -> row dropped", raw)
        return None


def clean_rows(content: bytes) -> tuple[list[PipelineRow], int, int]:
    """Parse and clean the CSV.

    Returns (rows, row_count_raw, row_count_clean), where raw is the number of
    non-empty data rows read and clean is the number kept after dropping
    invalid rows and exact duplicates.
    """
    text = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))

    rows: list[PipelineRow] = []
    seen: set[tuple] = set()
    raw_count = 0

    for record in reader:
        # Skip completely blank lines.
        if not any((v or "").strip() for v in record.values()):
            continue
        raw_count += 1

        amount = _parse_amount(record.get("amount"))
        currency = _normalise_currency(record.get("currency"))
        status = _normalise_status(record.get("status"))
        # A row missing any of these core typed fields can't be stored safely.
        if amount is None or currency is None or status is None:
            continue

        category = _clean_str(record.get("category")) or UNCATEGORISED
        row = PipelineRow(
            txn_id=_none_if_blank(record.get("txn_id")),
            date=_parse_date(record.get("date")),
            merchant=_clean_str(record.get("merchant")),
            amount=amount,
            currency=currency,
            status=status,
            category=category,
            account_id=_clean_str(record.get("account_id")),
            notes=_none_if_blank(record.get("notes")),
        )

        # Dedup on the fully-normalised identity (catches exact duplicate rows
        # even when they differ only in casing/formatting before cleaning).
        key = (
            row.txn_id,
            row.date,
            row.merchant,
            row.amount,
            row.currency,
            row.status,
            row.category,
            row.account_id,
            row.notes,
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    logger.info(
        "cleaning: raw=%d clean=%d (dropped/duplicate=%d)",
        raw_count,
        len(rows),
        raw_count - len(rows),
    )
    return rows, raw_count, len(rows)
