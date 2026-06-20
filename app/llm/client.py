"""Gemini client + factory.

WHY THIS FILE EXISTS
--------------------
Wraps Gemini 1.5 Flash behind a tiny, mockable interface (`classify`,
`summarize`) so the pipeline depends on behaviour, not the SDK. The factory
`get_llm_client()` returns `None` when no API key is configured, which the
pipeline treats as graceful degradation (no crashes, no credentials required to
run the system).

The `google-generativeai` SDK is imported lazily inside `GeminiClient` so the
module imports cleanly even when the SDK is absent and no key is set.
"""

import json
from typing import Protocol, runtime_checkable

from app.core.config import get_settings
from app.core.constants import ALLOWED_CATEGORIES
from app.core.logging import get_logger
from app.llm.prompts import classification_prompt, summary_prompt

logger = get_logger(__name__)


@runtime_checkable
class LLMClient(Protocol):
    """The interface the pipeline depends on (real client or a test fake)."""

    def classify(self, items: list[dict]) -> dict[str, str]:
        """Return a mapping of each item's ref -> category string."""
        ...

    def summarize(self, aggregates: dict) -> dict:
        """Return {"narrative": str, "risk_level": "low|medium|high"}."""
        ...


class GeminiClient:
    """Concrete Gemini-backed implementation of `LLMClient`."""

    def __init__(self, api_key: str, model: str) -> None:
        import google.generativeai as genai  # lazy: only needed when key is set

        genai.configure(api_key=api_key)
        self._genai = genai
        self._model_name = model

    def _generate_json(self, prompt: str) -> dict:
        model = self._genai.GenerativeModel(
            self._model_name,
            generation_config={"response_mime_type": "application/json"},
        )
        response = model.generate_content(prompt)
        return json.loads(response.text)

    def classify(self, items: list[dict]) -> dict[str, str]:
        data = self._generate_json(classification_prompt(items, ALLOWED_CATEGORIES))
        # Normalise keys to strings; caller validates the category values.
        return {str(k): str(v) for k, v in data.items()}

    def summarize(self, aggregates: dict) -> dict:
        return self._generate_json(summary_prompt(aggregates))


def get_llm_client() -> LLMClient | None:
    """Build the LLM client, or return None if Gemini is not configured."""
    settings = get_settings()
    if not settings.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — LLM features will be skipped.")
        return None
    return GeminiClient(api_key=settings.GEMINI_API_KEY, model=settings.GEMINI_MODEL)
