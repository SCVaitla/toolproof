"""Slack OAuth helpers for login, token refresh, and local credential storage.

Token rotation: when enabled on a Slack app, ``oauth.v2.access`` returns short-lived access tokens
plus a ``refresh_token``. ``OAuthTokenProvider`` refreshes proactively before expiry and reactively
on ``token_expired``, behind a lock so N concurrent tool calls trigger exactly one refresh.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import tempfile
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from toolproof.slack.errors import AuthError

AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
DEFAULT_API_BASE = "https://slack.com/api/"
REFRESH_SKEW_SECONDS = 300


class TokenProvider(Protocol):
    async def get_token(self) -> str: ...

    async def on_auth_failure(self, code: str) -> bool:
        """Called when Slack rejects the token. Return True if a fresh token is now available."""
        ...


class StaticTokenProvider:
    def __init__(self, token: str) -> None:
        if not token:
            raise AuthError("not_authed", "no Slack token configured")
        self._token = token

    async def get_token(self) -> str:
        return self._token

    async def on_auth_failure(self, code: str) -> bool:
        return False


@dataclass
class TokenSet:
    access_token: str
    team_id: str | None = None
    team_name: str | None = None
    bot_user_id: str | None = None
    scope: str = ""
    refresh_token: str | None = None
    expires_at: float | None = None  # unix seconds; None = non-expiring token
    authed_user_id: str | None = None

    @classmethod
    def from_oauth_response(cls, data: dict[str, Any], now: float | None = None) -> TokenSet:
        now = time.time() if now is None else now
        team = data.get("team") or {}
        expires_in = data.get("expires_in")
        return cls(
            access_token=data["access_token"],
            team_id=team.get("id"),
            team_name=team.get("name"),
            bot_user_id=data.get("bot_user_id"),
            scope=data.get("scope", ""),
            refresh_token=data.get("refresh_token"),
            expires_at=(now + float(expires_in)) if expires_in else None,
            authed_user_id=(data.get("authed_user") or {}).get("id"),
        )

    def needs_refresh(self, now: float | None = None) -> bool:
        if self.expires_at is None or not self.refresh_token:
            return False
        now = time.time() if now is None else now
        return now >= self.expires_at - REFRESH_SKEW_SECONDS


class TokenStore:
    """Store OAuth credentials in a 0600 JSON file using atomic replacement."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> TokenSet | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text())
        return TokenSet(**data)

    def save(self, tokens: TokenSet) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tokens-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(asdict(tokens), fh, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


@dataclass
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: list[str] = field(default_factory=list)
    user_scopes: list[str] = field(default_factory=list)
    api_base: str = DEFAULT_API_BASE

    @classmethod
    def from_env(cls) -> OAuthConfig:
        missing = [k for k in ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET") if not os.environ.get(k)]
        if missing:
            raise AuthError("not_configured", f"missing environment variables: {', '.join(missing)}")
        return cls(
            client_id=os.environ["SLACK_CLIENT_ID"],
            client_secret=os.environ["SLACK_CLIENT_SECRET"],
            redirect_uri=os.environ.get("SLACK_REDIRECT_URI", "https://localhost:8765/slack/oauth/callback"),
            scopes=_split(os.environ.get("SLACK_SCOPES", ",".join(DEFAULT_BOT_SCOPES))),
            user_scopes=_split(os.environ.get("SLACK_USER_SCOPES", "")),
        )


DEFAULT_BOT_SCOPES = [
    "channels:read",
    "groups:read",
    "channels:history",
    "groups:history",
    "chat:write",
    "reactions:write",
    "users:read",
    "users:read.email",
]


def _split(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


class OAuthClient:
    def __init__(self, config: OAuthConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self._transport = transport

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self.config.client_id,
            "scope": ",".join(self.config.scopes),
            "redirect_uri": self.config.redirect_uri,
            "state": state,
        }
        if self.config.user_scopes:
            params["user_scope"] = ",".join(self.config.user_scopes)
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def _token_request(self, form: dict[str, str]) -> TokenSet:
        async with httpx.AsyncClient(
            base_url=self.config.api_base, transport=self._transport, timeout=30
        ) as http:
            resp = await http.post(
                "oauth.v2.access",
                data={**form, "client_id": self.config.client_id, "client_secret": self.config.client_secret},
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise AuthError(
                "oauth_failed", f"non-JSON response from oauth.v2.access (HTTP {resp.status_code})"
            ) from exc
        if not data.get("ok"):
            raise AuthError(data.get("error", "oauth_failed"), f"oauth.v2.access failed: {data.get('error')}")
        return TokenSet.from_oauth_response(data)

    async def exchange_code(self, code: str) -> TokenSet:
        return await self._token_request({"code": code, "redirect_uri": self.config.redirect_uri})

    async def refresh(self, refresh_token: str) -> TokenSet:
        return await self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})


class OAuthTokenProvider:
    def __init__(self, store: TokenStore, oauth: OAuthClient, clock=time.time) -> None:
        self.store = store
        self.oauth = oauth
        self._clock = clock
        self._lock = asyncio.Lock()
        self._tokens = store.load()
        if self._tokens is None:
            raise AuthError("not_authed", f"no tokens in {store.path}; run `toolproof auth login` first")

    async def _refresh_locked(self) -> None:
        assert self._tokens is not None
        if not self._tokens.refresh_token:
            raise AuthError("token_expired", "token expired and no refresh token is available")
        fresh = await self.oauth.refresh(self._tokens.refresh_token)
        # Refresh responses may omit team/bot metadata; keep what we already know.
        for attr in ("team_id", "team_name", "bot_user_id", "authed_user_id"):
            if getattr(fresh, attr) is None:
                setattr(fresh, attr, getattr(self._tokens, attr))
        self._tokens = fresh
        self.store.save(fresh)

    async def get_token(self) -> str:
        assert self._tokens is not None
        if self._tokens.needs_refresh(self._clock()):
            async with self._lock:
                if self._tokens.needs_refresh(self._clock()):  # double-checked: one refresh per expiry
                    await self._refresh_locked()
        return self._tokens.access_token

    async def on_auth_failure(self, code: str) -> bool:
        if code != "token_expired" or not self._tokens or not self._tokens.refresh_token:
            return False
        stale = self._tokens.access_token
        async with self._lock:
            if self._tokens.access_token == stale:  # nobody refreshed while we waited
                await self._refresh_locked()
        return True


# Local browser login flow


class _CallbackResult:
    def __init__(self) -> None:
        self.code: str | None = None
        self.error: str | None = None
        self.event = threading.Event()


def _make_handler(expected_state: str, path: str, result: _CallbackResult):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != path:
                self.send_error(404)
                return
            qs = parse_qs(parsed.query)
            state = (qs.get("state") or [""])[0]
            if not secrets.compare_digest(state, expected_state):
                result.error = "state_mismatch"
            elif "error" in qs:
                result.error = qs["error"][0]
            else:
                result.code = (qs.get("code") or [None])[0]
                if not result.code:
                    result.error = "missing_code"
            ok = result.error is None
            body = (
                "<h2>toolproof: Slack connected. You can close this tab.</h2>"
                if ok
                else f"<h2>toolproof: authorization failed ({result.error}).</h2>"
            ).encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            result.event.set()

        def log_message(self, *args: Any) -> None:  # keep the terminal clean
            return

    return Handler


def start_callback_server(redirect_uri: str, state: str) -> tuple[HTTPServer, _CallbackResult]:
    """Start a loopback server on the redirect URI's port. Caller must ``shutdown()`` it."""
    parsed = urlparse(redirect_uri)
    result = _CallbackResult()
    server = HTTPServer(
        (parsed.hostname or "127.0.0.1", parsed.port if parsed.port is not None else 8765),
        _make_handler(state, parsed.path or "/", result),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, result


async def run_login_flow(
    config: OAuthConfig, store: TokenStore, *, open_browser: bool = True, timeout_s: float = 300
) -> TokenSet:
    """Interactive login. Note: Slack requires HTTPS redirect URIs; for local dev, front this
    loopback server with a TLS tunnel (e.g. ngrok) and register that URL in your Slack app."""
    state = secrets.token_urlsafe(24)
    oauth = OAuthClient(config)
    server, result = start_callback_server(config.redirect_uri, state)
    try:
        url = oauth.authorize_url(state)
        print(f"Open this URL to authorize:\n  {url}\n")
        if open_browser:
            webbrowser.open(url)
        got = await asyncio.to_thread(result.event.wait, timeout_s)
        if not got:
            raise AuthError("oauth_timeout", "timed out waiting for the OAuth callback")
        if result.error or not result.code:
            raise AuthError(result.error or "oauth_failed", f"authorization failed: {result.error}")
        tokens = await oauth.exchange_code(result.code)
        store.save(tokens)
        return tokens
    finally:
        server.shutdown()
        server.server_close()
