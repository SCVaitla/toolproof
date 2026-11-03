"""Clocks. Real code sleeps; evals and tests run on a virtual clock so chaos runs finish instantly
while still accounting for every second an agent would have waited in production."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


class VirtualClock:
    """Clock used by tests; sleep advances virtual time instead of blocking."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self.slept = 0.0

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += max(0.0, seconds)

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._now += seconds
            self.slept += seconds
        # Let other tasks run before advancing virtual time.
        await asyncio.sleep(0)
