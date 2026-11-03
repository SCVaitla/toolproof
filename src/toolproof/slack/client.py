"""Async client for the Slack Web API.

What makes it production-grade rather than a thin wrapper:

* Per-method, tier-aware rate limiting with shared ``Retry-After`` penalties.
* Retries with capped, jittered exponential backoff that never undercuts ``Retry-After``.
* **Write safety.** Non-idempotent writes (``chat.postMessage``) are never retried after an
  ambiguous failure (5xx / timeout after send). They raise ``OutcomeUnknownError`` so the caller
  can verify state first. Retrying them blindly is the classic way agents double-post.
* Reactive token refresh on ``token_expired`` (exactly once per call).
* Cursor pagination that is resumable: page sizes shrink to the remaining budget so the returned
  cursor never skips items.
* Tolerates Slack's messier responses: non-JSON 5xx bodies, ``ok:false`` with HTTP 200, and
  pages that are shorter than requested.
"""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from toolproof.clock import Clock, RealClock
from toolproof.slack.auth import DEFAULT_API_BASE, TokenProvider
from toolproof.slack.errors import (
    AUTH_ERROR_CODES,
    RETRYABLE_ERROR_CODES,
    AuthError,
    MissingScopeError,
    OutcomeUnknownError,
    RateLimitedError,
    SlackApiError,
    SlackError,
    TransientError,
)
from toolproof.slack.ratelimit import RateLimiter
from toolproof.slack.retry import RetryPolicy

# Replaying these methods can create duplicate side effects.
NON_IDEMPOTENT_METHODS = frozenset({"chat.postMessage", "conversations.create", "chat.scheduleMessage"})


@dataclass
class ClientStats:
    requests: int = 0
    retries: int = 0
    rate_limited: int = 0
    server_errors: int = 0
    outcome_unknown: int = 0
    token_refreshes: int = 0
    waited_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _encode(params: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, (list, dict)):
            out[key] = json.dumps(value)
        else:
            out[key] = str(value)
    return out


class SlackClient:
    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        base_url: str = DEFAULT_API_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
        retry: RetryPolicy | None = None,
        limiter: RateLimiter | None = None,
        clock: Clock | None = None,
        rng: random.Random | None = None,
        unsafe_retry_writes: bool = False,
    ) -> None:
        self.clock = clock or RealClock()
        self.tokens = token_provider
        self.retry = retry or RetryPolicy()
        self.limiter = limiter or RateLimiter(clock=self.clock)
        self.rng = rng or random.Random()
        self.unsafe_retry_writes = unsafe_retry_writes
        self.stats = ClientStats()
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": "toolproof-slack-mcp/0.1"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> SlackClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # Request handling

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        non_idempotent = method in NON_IDEMPOTENT_METHODS
        form = _encode(params)
        attempt = 0
        refreshed = False
        while True:
            attempt += 1
            self.stats.waited_seconds += await self.limiter.acquire(method)
            token = await self.tokens.get_token()
            self.stats.requests += 1
            try:
                err = await self._send(method, form, token, non_idempotent)
            except _Success as ok:
                return ok.data

            if isinstance(err, AuthError) and err.code == "token_expired" and not refreshed:
                if await self.tokens.on_auth_failure(err.code):
                    refreshed = True
                    self.stats.token_refreshes += 1
                    attempt -= 1  # a refresh is not a failed attempt
                    continue
            if not err.retryable or attempt >= self.retry.max_attempts:
                raise err
            retry_after = err.retry_after if isinstance(err, RateLimitedError) else None
            delay = self.retry.delay(attempt, retry_after, self.rng)
            self.stats.retries += 1
            self.stats.waited_seconds += delay
            await self.clock.sleep(delay)

    async def _send(self, method: str, form: dict[str, str], token: str, non_idempotent: bool) -> SlackError:
        """Perform one HTTP request and return the Slack error when it is not successful."""
        try:
            resp = await self._http.post(method, data=form, headers={"Authorization": f"Bearer {token}"})
        except httpx.ConnectError as exc:  # never reached Slack: safe to retry, even for writes
            return TransientError("connect_error", f"{method}: {exc}")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return self._ambiguous(method, f"{type(exc).__name__}", non_idempotent)

        if resp.status_code == 429:
            self.stats.rate_limited += 1
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            self.limiter.penalize(method, retry_after)
            return RateLimitedError(retry_after, method)
        if resp.status_code >= 500:
            self.stats.server_errors += 1
            return self._ambiguous(method, f"HTTP {resp.status_code}", non_idempotent)

        try:
            data = resp.json()
        except ValueError:
            return self._ambiguous(method, f"non-JSON body (HTTP {resp.status_code})", non_idempotent)
        if not isinstance(data, dict):
            return TransientError("bad_response", f"{method}: unexpected payload type")
        if data.get("ok"):
            raise _Success(data)

        code = str(data.get("error", "unknown_error"))
        if code == "ratelimited":
            self.stats.rate_limited += 1
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            self.limiter.penalize(method, retry_after)
            return RateLimitedError(retry_after, method)
        if code == "missing_scope":
            return MissingScopeError(data.get("needed"), data.get("provided"), data)
        if code in AUTH_ERROR_CODES:
            return AuthError(code, f"{method}: {code}", data=data)
        if code in RETRYABLE_ERROR_CODES:
            return self._ambiguous(method, code, non_idempotent)
        return SlackApiError(code, f"{method}: {code}", data=data)

    def _ambiguous(self, method: str, cause: str, non_idempotent: bool) -> SlackError:
        if non_idempotent and not self.unsafe_retry_writes:
            self.stats.outcome_unknown += 1
            return OutcomeUnknownError(method, cause)
        return TransientError("server_error", f"{method}: {cause}")

    # Pagination

    async def paginate(
        self,
        method: str,
        items_key: str,
        *,
        page_size: int = 200,
        max_items: int | None = None,
        cursor: str | None = None,
        **params: Any,
    ) -> AsyncIterator[tuple[dict[str, Any], str | None]]:
        """Yield items with the cursor for the page they came from."""
        fetched = 0
        while True:
            limit = page_size if max_items is None else min(page_size, max_items - fetched)
            if limit <= 0:
                return
            data = await self.call(method, limit=limit, cursor=cursor, **params)
            items = data.get(items_key) or []
            next_cursor = (data.get("response_metadata") or {}).get("next_cursor") or None
            for item in items:
                fetched += 1
                yield item, next_cursor
            if not next_cursor or not items:
                return
            cursor = next_cursor

    async def collect(
        self,
        method: str,
        items_key: str,
        *,
        max_items: int,
        page_size: int = 200,
        cursor: str | None = None,
        **params: Any,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Collect up to ``max_items`` and return the cursor for the next page."""
        items: list[dict[str, Any]] = []
        resume: str | None = None
        async for item, page_cursor in self.paginate(
            method, items_key, page_size=page_size, max_items=max_items, cursor=cursor, **params
        ):
            items.append(item)
            resume = page_cursor
        return items, resume if len(items) >= max_items else None


class _Success(Exception):
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


def _parse_retry_after(value: str | None) -> float:
    try:
        return max(0.0, float(value)) if value is not None else 1.0
    except ValueError:
        return 1.0
