"""Per-method rate limiting based on Slack API tiers.

Slack publishes limits per method tier rather than globally, so a client that shares one bucket
across methods either wastes capacity or gets 429s. Buckets also honour server-issued
``Retry-After`` penalties so that concurrent callers back off together instead of stampeding.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field

from toolproof.clock import Clock, RealClock

_EPS = 1e-9
_MIN_SLEEP = 1e-3

# Approximate requests per minute for Slack rate-limit tiers.
TIER_RPM: dict[int, float] = {1: 1, 2: 20, 3: 50, 4: 100}

# Defaults for the API methods used here. Callers can override them if Slack changes a tier.
DEFAULT_METHOD_TIERS: dict[str, int] = {
    "auth.test": 4,
    "conversations.list": 2,
    "conversations.info": 3,
    "conversations.history": 3,
    "conversations.replies": 3,
    "users.list": 2,
    "users.info": 4,
    "users.lookupByEmail": 3,
    "reactions.add": 3,
    "chat.postMessage": 4,  # Special-cased by Slack (~1 msg/sec/channel); treated as tier 4 here.
    "chat.getPermalink": 4,
    "oauth.v2.access": 4,
}


@dataclass
class TokenBucket:
    capacity: float
    refill_per_sec: float
    clock: Clock
    tokens: float = field(init=False)
    updated: float = field(init=False)
    blocked_until: float = field(init=False, default=0.0)
    _lock: asyncio.Lock = field(init=False, repr=False, default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.updated = self.clock.now()

    def _refill(self) -> None:
        now = self.clock.now()
        elapsed = max(0.0, now - self.updated)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        self.updated = now

    async def acquire(self) -> float:
        """Take one token, sleeping as needed. Returns seconds waited."""
        waited = 0.0
        async with self._lock:
            while True:
                now = self.clock.now()
                if now < self.blocked_until:
                    delay = self.blocked_until - now
                else:
                    self._refill()
                    if self.tokens >= 1 - _EPS:  # tolerate float drift, or we can spin forever
                        self.tokens = max(0.0, self.tokens - 1)
                        return waited
                    delay = max((1 - self.tokens) / self.refill_per_sec, _MIN_SLEEP)
                await self.clock.sleep(delay)
                waited += delay

    def penalize(self, retry_after: float) -> None:
        """Server said slow down: block everyone on this bucket and drain the burst."""
        self.blocked_until = max(self.blocked_until, self.clock.now() + retry_after)
        self.tokens = 0.0
        self.updated = self.blocked_until


class RateLimiter:
    def __init__(
        self,
        clock: Clock | None = None,
        method_tiers: dict[str, int] | None = None,
        default_tier: int = 3,
        burst_seconds: float = 10.0,
    ) -> None:
        self.clock = clock or RealClock()
        self.method_tiers = {**DEFAULT_METHOD_TIERS, **(method_tiers or {})}
        self.default_tier = default_tier
        self.burst_seconds = burst_seconds
        self._buckets: dict[str, TokenBucket] = {}

    def bucket(self, method: str) -> TokenBucket:
        if method not in self._buckets:
            rpm = TIER_RPM[self.method_tiers.get(method, self.default_tier)]
            per_sec = rpm / 60.0
            capacity = max(1.0, math.floor(per_sec * self.burst_seconds))
            self._buckets[method] = TokenBucket(capacity=capacity, refill_per_sec=per_sec, clock=self.clock)
        return self._buckets[method]

    async def acquire(self, method: str) -> float:
        return await self.bucket(method).acquire()

    def penalize(self, method: str, retry_after: float) -> None:
        self.bucket(method).penalize(retry_after)
