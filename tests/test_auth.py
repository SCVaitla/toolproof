from __future__ import annotations

import asyncio
import os
import stat
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from toolproof.clock import VirtualClock
from toolproof.slack import AuthError, OAuthClient, OAuthConfig, OAuthTokenProvider, TokenSet, TokenStore
from toolproof.slack.auth import start_callback_server
from toolproof.slack.ratelimit import TokenBucket
from toolproof.twin import SlackTwin


@pytest.fixture
def oauth_setup(world, tmp_path):
    clients = {"cid": {"secret": "csecret", "code": "good-code", "redirect_uri": "https://localhost:8765/cb"}}
    twin = SlackTwin(world, oauth_clients=clients)
    cfg = OAuthConfig(
        client_id="cid",
        client_secret="csecret",
        redirect_uri="https://localhost:8765/cb",
        scopes=["chat:write", "channels:read"],
        api_base="https://slack.twin/api/",
    )
    return twin, OAuthClient(cfg, transport=twin.transport()), TokenStore(tmp_path / "tokens.json")


def test_authorize_url(oauth_setup):
    _, oauth, _ = oauth_setup
    qs = parse_qs(urlparse(oauth.authorize_url("s123")).query)
    assert qs["client_id"] == ["cid"] and qs["state"] == ["s123"]
    assert qs["scope"] == ["chat:write,channels:read"]


async def test_exchange_and_store_with_0600(oauth_setup):
    _, oauth, store = oauth_setup
    tokens = await oauth.exchange_code("good-code")
    assert tokens.bot_user_id == "U0BOT1" and tokens.refresh_token and tokens.expires_at
    store.save(tokens)
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert store.load() == tokens


async def test_bad_code_raises(oauth_setup):
    _, oauth, _ = oauth_setup
    with pytest.raises(AuthError) as exc:
        await oauth.exchange_code("nope")
    assert exc.value.code == "invalid_code"


async def test_provider_refreshes_once_under_concurrency(oauth_setup):
    _, oauth, store = oauth_setup
    tokens = await oauth.exchange_code("good-code")
    tokens.expires_at = 1000.0  # already inside the refresh window
    store.save(tokens)
    provider = OAuthTokenProvider(store, oauth, clock=lambda: 999.0)
    results = await asyncio.gather(*(provider.get_token() for _ in range(10)))
    assert len(set(results)) == 1 and results[0] == "xoxe.xoxb-rotated-1"
    assert store.load().refresh_token == "xoxe-1-refresh-1"
    assert store.load().bot_user_id == "U0BOT1"  # metadata preserved across refresh


async def test_reactive_refresh_on_token_expired(oauth_setup):
    twin, oauth, store = oauth_setup
    store.save(TokenSet(access_token="stale", refresh_token="xoxe-1-refresh-0", expires_at=None))
    twin.oauth_clients["cid"]["refresh_token"] = "xoxe-1-refresh-0"
    provider = OAuthTokenProvider(store, oauth)
    assert await provider.on_auth_failure("token_expired") is True
    assert await provider.get_token() == "xoxe.xoxb-rotated-1"
    assert await provider.on_auth_failure("invalid_auth") is False


def test_missing_tokens_is_clear_error(tmp_path, oauth_setup):
    _, oauth, _ = oauth_setup
    with pytest.raises(AuthError, match="auth login"):
        OAuthTokenProvider(TokenStore(tmp_path / "none.json"), oauth)


@pytest.mark.parametrize(("state", "ok"), [("expected", True), ("forged", False)])
def test_callback_server_validates_state(state, ok):
    server, result = start_callback_server("http://127.0.0.1:0/cb", "expected")
    port = server.server_address[1]
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/cb", params={"code": "abc", "state": state}, timeout=5)
        assert result.event.wait(5)
        assert (resp.status_code == 200) is ok
        assert (result.code == "abc") is ok
        assert (result.error == "state_mismatch") is (not ok)
    finally:
        server.shutdown()
        server.server_close()


async def test_bucket_does_not_spin_on_float_drift():
    """Regression: at large clock values a tiny refill delay doesn't move a float clock, which
    used to spin acquire() forever."""
    clock = VirtualClock(start=1_730_000_000.0)
    bucket = TokenBucket(capacity=1, refill_per_sec=1 / 3, clock=clock)
    for _ in range(50):
        await asyncio.wait_for(bucket.acquire(), timeout=2)
