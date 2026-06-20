"""Prompt templates for Gemini calls.

WHY THIS FILE EXISTS
--------------------
Keeps prompt text out of the client logic so prompts can be reviewed and tuned
in one place. Both prompts demand strict JSON output; the client requests JSON
mode and the callers validate the parsed result.
"""

import json
from collections.abc import Sequence


def classification_prompt(items: list[dict], allowed_categories: Sequence[str]) -> str:
    """Prompt to classify a batch of transactions into the allowed categories.

    `items` is a list of {"ref", "merchant", "amount", "notes"}. The model must
    return a JSON object mapping each `ref` to exactly one allowed category.
    """
    categories = ", ".join(allowed_categories)
    payload = json.dumps(items, ensure_ascii=False)
    return (
        "You are a financial transaction classifier. Assign each transaction to "
        "exactly ONE of these categories:\n"
        f"{categories}\n\n"
        "Rules:\n"
        "- Use the merchant name (and notes) to decide.\n"
        "- If unsure, use \"Other\".\n"
        "- Respond with ONLY a JSON object mapping each transaction's \"ref\" "
        "(as a string) to its category. No prose.\n\n"
        f"Transactions:\n{payload}\n\n"
        'Example response: {"0": "Food", "1": "Travel"}'
    )


def summary_prompt(aggregates: dict) -> str:
    """Prompt to turn pre-computed aggregates into a narrative + risk level.

    The model writes prose and judges risk; it does NOT compute the numbers
    (those are calculated deterministically and passed in).
    """
    payload = json.dumps(aggregates, ensure_ascii=False, default=str)
    return (
        "You are a financial analyst. Given these pre-computed spending "
        "aggregates for one batch of transactions, write a concise summary.\n\n"
        f"Aggregates:\n{payload}\n\n"
        "Respond with ONLY a JSON object of the form:\n"
        '{"narrative": "<2-3 sentence plain-English summary>", '
        '"risk_level": "low|medium|high"}\n'
        "Base risk_level on the number of anomalies and unusually large spend."
    )
