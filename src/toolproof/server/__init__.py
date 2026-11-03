"""Factories for the MCP server and Slack client backends."""

from __future__ import annotations

import os
import random

from toolproof.clock import Clock
from toolproof.server.tools import create_server
from toolproof.slack.auth import (
    OAuthClient,
    OAuthConfig,
    OAuthTokenProvider,
    StaticTokenProvider,
    TokenStore,
)
from toolproof.slack.client import SlackClient
from toolproof.slack.errors import AuthError
from toolproof.slack.ratelimit import RateLimiter
from toolproof.twin.api import SlackTwin

DEFAULT_TOKEN_PATH = "~/.config/toolproof/slack-tokens.json"


def build_live_client(token_path: str | None = None) -> SlackClient:
    """Build a Slack client from a bot token or stored OAuth credentials."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token:
        return SlackClient(StaticTokenProvider(token))
    store = TokenStore(token_path or os.environ.get("TOOLPROOF_TOKEN_PATH", DEFAULT_TOKEN_PATH))
    try:
        oauth = OAuthClient(OAuthConfig.from_env())
    except AuthError as exc:
        raise AuthError(
            "not_configured",
            "no Slack credentials: set SLACK_BOT_TOKEN, or set SLACK_CLIENT_ID/SLACK_CLIENT_SECRET and run "
            "`toolproof auth login`. To try it offline, run `toolproof serve --twin`.",
        ) from exc
    provider = OAuthTokenProvider(store, oauth)
    return SlackClient(provider)


def build_twin_client(
    twin: SlackTwin, *, clock: Clock | None = None, seed: int = 0, unsafe_retry_writes: bool = False
) -> SlackClient:
    limiter = RateLimiter(clock=clock) if clock else None
    return SlackClient(
        StaticTokenProvider(twin.world.token),
        base_url="https://slack.twin/api/",
        transport=twin.transport(),
        clock=clock,
        limiter=limiter,
        rng=random.Random(seed),
        unsafe_retry_writes=unsafe_retry_writes,
    )


__all__ = ["build_live_client", "build_twin_client", "create_server"]
