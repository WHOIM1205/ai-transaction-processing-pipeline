"""Retry helper with exponential backoff.

WHY THIS FILE EXISTS
--------------------
LLM calls fail transiently (rate limits, timeouts, malformed JSON). This small
helper retries a callable up to N attempts with exponential backoff, isolated
here so it is easy to unit-test (the `sleep` is injectable) and reuse for both
classification and summary calls.
"""

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def with_retries(
    func: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
    logger=None,
) -> T:
    """Call `func`, retrying on `retry_on`. Delay doubles each attempt
    (base_delay, 2×, 4×, …). Re-raises the last error if all attempts fail."""
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except retry_on as exc:
            last_error = exc
            if logger is not None:
                logger.warning(
                    "LLM call attempt %d/%d failed: %s", attempt, max_attempts, exc
                )
            if attempt == max_attempts:
                break
            sleep(base_delay * (2 ** (attempt - 1)))
    assert last_error is not None  # loop always sets it before breaking
    raise last_error
