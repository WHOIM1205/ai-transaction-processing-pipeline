"""Domain constants.

WHY THIS FILE EXISTS
--------------------
Fixed, non-configurable facts about the input contract live here (as opposed to
`config.py`, which holds environment-tunable settings). Keeping them in one
place means the upload validator and any future parser agree on the same column
contract.
"""

# The exact column set the uploaded CSV must contain (matches transactions.csv).
# Compared case-insensitively against the file's header row.
REQUIRED_CSV_COLUMNS: frozenset[str] = frozenset(
    {
        "txn_id",
        "date",
        "merchant",
        "amount",
        "currency",
        "status",
        "category",
        "account_id",
        "notes",
    }
)

# Only this file extension is accepted by the upload endpoint.
ALLOWED_UPLOAD_EXTENSION: str = ".csv"

# Value used to fill blank/missing categories during cleaning.
UNCATEGORISED: str = "Uncategorised"

# The closed set of categories the LLM may assign. Anything outside this set is
# coerced to "Other" so an out-of-contract model response can never reach the DB.
ALLOWED_CATEGORIES: tuple[str, ...] = (
    "Food",
    "Shopping",
    "Travel",
    "Transport",
    "Utilities",
    "Cash Withdrawal",
    "Entertainment",
    "Other",
)

# Domestic-only (India-only) brands. A USD charge on any of these is suspicious.
# The brief names Swiggy/Ola/IRCTC as examples ("such as"); Zomato is the same
# class of domestic-only food brand and is the merchant that actually appears in
# USD in the dataset, so it is included to make Rule 2 meaningful. Matched
# case-insensitively against the cleaned merchant name.
DOMESTIC_ONLY_MERCHANTS: frozenset[str] = frozenset(
    {"swiggy", "ola", "irctc", "zomato"}
)

# Anomaly reason codes (stable, machine-readable; stored in anomaly_reason[]).
ANOMALY_AMOUNT_OUTLIER: str = "amount_gt_3x_account_median"
ANOMALY_USD_DOMESTIC: str = "usd_on_domestic_merchant"
