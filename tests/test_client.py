from __future__ import annotations

import httpx
import pytest

from toolproof.clock import VirtualClock
from toolproof.slack import (
    AuthError,
    MissingScopeError,
    OutcomeUnknownError,
    RateLimitedError,
    RetryPolicy,
    SlackApiError,
    SlackClient,
    StaticTokenProvider,
)
from toolproof.slack.ratelimit import RateLimiter, TokenBucket
from toolproof.twin import ChaosConfig

from .conftest import make_client


async def test_token_bucket_paces_requests():
    clock = VirtualClock()
    bucket = TokenBucket(capacity=2, refill_per_sec=1.0, clock=clock)
    assert await bucket.acquire() == 0
    assert await bucket.acquire() == 0
    waited = await bucket.acquire()
    assert waited == pytest.approx(1.0)
    bucket.penalize(10)
    assert await bucket.acquire() == pytest.approx(11.0)  # 10s block + 1s to refill one token


async def test_rate_limiter_uses_method_tiers():
    limiter = RateLimiter(clock=VirtualClock())
    assert limiter.bucket("conversations.list").refill_per_sec == pytest.approx(20 / 60)
    assert limiter.bucket("users.info").refill_per_sec == pytest.approx(100 / 60)


def test_retry_delay_never_undercuts_retry_after():
    policy = RetryPolicy(base_delay=0.5, max_delay=8, jitter=False)
    assert policy.delay(1) == 0.5
    assert policy.delay(10) == 8
    assert policy.delay(1, retry_after=20) == 20


async def test_pagination_collects_everything_and_resumes(world):
    _, client = make_client(world, ChaosConfig(max_page_size=7))
    users, cursor = await client.collect("users.list", "members", max_items=10)
    assert len(users) == 10 and cursor is not None
    rest, done = await client.collect("users.list", "members", max_items=1000, cursor=cursor)
    assert done is None
    ids = [u["id"] for u in users + rest]
    assert len(ids) == len(set(ids)) == len(world.users)  # nothing skipped, nothing duplicated


async def test_reads_survive_rate_limits_and_html_5xx(world):
    clock = VirtualClock()
    chaos = ChaosConfig(seed=11, rate_limit_prob=0.3, server_error_prob=0.3, max_page_size=5)
    twin, client = make_client(world, chaos, clock)
    client.retry = RetryPolicy(max_attempts=12)
    items, _ = await client.collect("conversations.list", "channels", max_items=500, types="public_channel")
    assert len(items) == sum(1 for c in world.channels.values() if not c.is_private)
    assert twin.stats.faults_rate_limit > 0 and twin.stats.faults_server_error > 0
    assert client.stats.retries >= twin.stats.faults_rate_limit
    assert clock.slept > 0  # honoured Retry-After on a virtual clock


async def test_rate_limit_exhaustion_surfaces_retry_after(world):
    _, client = make_client(world, ChaosConfig(rate_limit_prob=1.0, retry_after=(7, 7)))
    client.retry = RetryPolicy(max_attempts=2)
    with pytest.raises(RateLimitedError) as exc:
        await client.call("users.list")
    assert exc.value.retry_after == 7


async def test_ambiguous_write_is_not_blindly_retried(world):
    _, client = make_client(world, ChaosConfig(ambiguous_write_prob=1.0))
    with pytest.raises(OutcomeUnknownError):
        await client.call("chat.postMessage", channel="C0RELEASES", text="hello")
    posted = [m for m in world.mutations if m.kind == "message_posted"]
    assert len(posted) == 1  # applied exactly once, and the client did not retry


async def test_unsafe_retries_cause_duplicate_posts(world):
    _, client = make_client(world, ChaosConfig(ambiguous_write_prob=1.0), unsafe_retry_writes=True)
    client.retry = RetryPolicy(max_attempts=3, jitter=False)
    with pytest.raises(Exception):  # noqa: B017 - all attempts fail ambiguously
        await client.call("chat.postMessage", channel="C0RELEASES", text="hello")
    assert len([m for m in world.mutations if m.kind == "message_posted"]) == 3


async def test_api_errors_are_typed(world):
    _, client = make_client(world)
    with pytest.raises(SlackApiError) as exc:
        await client.call("conversations.history", channel="C0NOPE000")
    assert exc.value.code == "channel_not_found" and not exc.value.retryable
    world.scopes.discard("users:read")
    with pytest.raises(MissingScopeError) as exc2:
        await client.call("users.list")
    assert exc2.value.needed == "users:read"


async def test_invalid_token_is_auth_error(world):
    from toolproof.twin import SlackTwin

    twin = SlackTwin(world)
    client = SlackClient(
        StaticTokenProvider("xoxb-wrong"), base_url="https://x/api/", transport=twin.transport()
    )
    with pytest.raises(AuthError) as exc:
        await client.call("auth.test")
    assert exc.value.code == "invalid_auth"


async def test_token_expired_triggers_single_refresh():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.headers["Authorization"] == "Bearer old":
            return httpx.Response(200, json={"ok": False, "error": "token_expired"})
        return httpx.Response(200, json={"ok": True, "user_id": "U1"})

    class Provider:
        token = "old"

        async def get_token(self):
            return self.token

        async def on_auth_failure(self, code):
            self.token = "new"
            return True

    client = SlackClient(Provider(), base_url="https://x/api/", transport=httpx.MockTransport(handler))
    assert (await client.call("auth.test"))["user_id"] == "U1"
    assert calls["n"] == 2 and client.stats.token_refreshes == 1


async def test_connect_errors_on_writes_are_retried():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"ok": True, "ts": "1.1", "channel": "C1"})

    client = SlackClient(
        StaticTokenProvider("t"),
        base_url="https://x/api/",
        transport=httpx.MockTransport(handler),
        clock=VirtualClock(),
    )
    assert (await client.call("chat.postMessage", channel="C1", text="x"))["ts"] == "1.1"


async def test_timeouts_on_writes_are_outcome_unknown():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = SlackClient(
        StaticTokenProvider("t"),
        base_url="https://x/api/",
        transport=httpx.MockTransport(handler),
        clock=VirtualClock(),
    )
    with pytest.raises(OutcomeUnknownError):
        await client.call("chat.postMessage", channel="C1", text="x")
