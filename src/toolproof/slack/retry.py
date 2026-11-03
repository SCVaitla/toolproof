"""Retry delays using capped exponential backoff and jitter."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: bool = True

    def delay(
        self, attempt: int, retry_after: float | None = None, rng: random.Random | None = None
    ) -> float:
        """Delay before attempt ``attempt + 1`` (attempts are 1-based)."""
        backoff = min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        if self.jitter:
            backoff = (rng or random).uniform(0, backoff)
        return max(backoff, retry_after or 0.0)
