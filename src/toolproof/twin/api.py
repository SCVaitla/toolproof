"""Slack Web API test double backed by ``httpx.MockTransport``.

The production ``SlackClient`` talks to it unchanged, so evals exercise the real retry, rate-limit,
pagination and write-safety code paths. ``ChaosConfig`` injects the failure modes that real Slack
produces in the wild, deterministically from a seed.
"""

from __future__ import annotations

import base64
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

from toolproof.twin.world import Channel, World

READ_SCOPES = {
    "conversations.list": "channels:read",
    "conversations.info": "channels:read",
    "conversations.history": "channels:history",
    "conversations.replies": "channels:history",
    "users.list": "users:read",
    "users.info": "users:read",
    "users.lookupByEmail": "users:read.email",
    "chat.postMessage": "chat:write",
    "reactions.add": "reactions:write",
}
WRITE_METHODS = {"chat.postMessage", "reactions.add"}


@dataclass
class ChaosConfig:
    """Per-request fault injection settings."""

    seed: int = 0
    rate_limit_prob: float = 0.0
    retry_after: tuple[int, int] = (1, 20)
    server_error_prob: float = 0.0
    # For writes, mutate state before returning a 500 to simulate an ambiguous outcome.
    ambiguous_write_prob: float = 0.0
    # Cap page size so pagination paths are exercised in tests.
    max_page_size: int | None = None
    # Methods excluded from fault injection.
    exempt_methods: set[str] = field(default_factory=lambda: {"auth.test", "oauth.v2.access"})

    @classmethod
    def named(cls, name: str, seed: int = 0) -> ChaosConfig:
        presets = {
            "clean": cls(seed=seed, max_page_size=50),
            "chaos": cls(
                seed=seed,
                rate_limit_prob=0.15,
                server_error_prob=0.08,
                ambiguous_write_prob=0.35,
                max_page_size=15,
            ),
            "storm": cls(
                seed=seed,
                rate_limit_prob=0.3,
                server_error_prob=0.15,
                ambiguous_write_prob=0.6,
                max_page_size=7,
            ),
        }
        if name not in presets:
            raise ValueError(f"unknown condition {name!r}; choose from {sorted(presets)}")
        return presets[name]


@dataclass
class TwinStats:
    requests: int = 0
    faults_rate_limit: int = 0
    faults_server_error: int = 0
    faults_ambiguous_write: int = 0
    by_method: dict[str, int] = field(default_factory=dict)


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode()).decode()


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        tag, value = base64.urlsafe_b64decode(cursor.encode()).decode().split(":", 1)
        if tag != "offset":
            raise ValueError
        return max(0, int(value))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _ApiError("invalid_cursor") from exc


class _ApiError(Exception):
    def __init__(self, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.extra = extra


class SlackTwin:
    def __init__(self, world: World, chaos: ChaosConfig | None = None, *, oauth_clients: dict | None = None):
        self.world = world
        self.chaos = chaos or ChaosConfig()
        self.rng = random.Random(self.chaos.seed)
        self.stats = TwinStats()
        self.oauth_clients = oauth_clients or {}
        self.handlers: dict[str, Callable[[dict[str, str]], dict[str, Any]]] = {
            "auth.test": self._auth_test,
            "conversations.list": self._conversations_list,
            "conversations.info": self._conversations_info,
            "conversations.history": self._conversations_history,
            "conversations.replies": self._conversations_replies,
            "users.list": self._users_list,
            "users.info": self._users_info,
            "users.lookupByEmail": self._users_lookup_by_email,
            "chat.postMessage": self._chat_post_message,
            "reactions.add": self._reactions_add,
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # Request dispatch

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        self.stats.requests += 1
        self.stats.by_method[method] = self.stats.by_method.get(method, 0) + 1
        params = {k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}

        if method == "oauth.v2.access":
            return self._json(self._oauth_access(params))
        handler = self.handlers.get(method)
        if handler is None:
            return self._json({"ok": False, "error": "unknown_method"}, status=404)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return self._json({"ok": False, "error": "not_authed"})
        if auth.removeprefix("Bearer ") != self.world.token:
            return self._json({"ok": False, "error": "invalid_auth"})
        needed = READ_SCOPES.get(method)
        if needed and needed not in self.world.scopes:
            return self._json(
                {
                    "ok": False,
                    "error": "missing_scope",
                    "needed": needed,
                    "provided": ",".join(sorted(self.world.scopes)),
                }
            )

        faulted = method not in self.chaos.exempt_methods
        if faulted and self.rng.random() < self.chaos.rate_limit_prob:
            self.stats.faults_rate_limit += 1
            retry_after = self.rng.randint(*self.chaos.retry_after)
            return self._json(
                {"ok": False, "error": "ratelimited"}, status=429, headers={"Retry-After": str(retry_after)}
            )
        if faulted and self.rng.random() < self.chaos.server_error_prob:
            self.stats.faults_server_error += 1
            # Real gateways return HTML, not JSON, on 5xx.
            return httpx.Response(503, text="<html><body>upstream connect error</body></html>")

        try:
            body = handler(params)
        except _ApiError as err:
            return self._json({"ok": False, "error": err.code, **err.extra})

        if faulted and method in WRITE_METHODS and self.rng.random() < self.chaos.ambiguous_write_prob:
            self.stats.faults_ambiguous_write += 1
            return httpx.Response(500, text="Internal Server Error")  # the write already happened
        return self._json({"ok": True, **body})

    @staticmethod
    def _json(
        data: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return httpx.Response(status, json=data, headers=headers)

    # Helpers

    def _page(self, items: list[Any], params: dict[str, str], default: int = 100) -> tuple[list[Any], dict]:
        try:
            limit = int(params.get("limit") or default)
        except ValueError as exc:
            raise _ApiError("invalid_limit") from exc
        limit = max(1, min(limit, 1000))
        if self.chaos.max_page_size:
            limit = min(limit, self.chaos.max_page_size)
        start = _offset(params.get("cursor"))
        page = items[start : start + limit]
        nxt = start + limit
        meta = {"next_cursor": _cursor(nxt) if nxt < len(items) else ""}
        return page, meta

    def _channel(self, params: dict[str, str]) -> Channel:
        ref = params.get("channel", "")
        ch = self.world.channels.get(ref)
        if ch is None and ref.startswith("#"):
            ch = self.world.channel_by_name(ref)
        if ch is None:
            raise _ApiError("channel_not_found")
        return ch

    def _readable(self, ch: Channel) -> None:
        if ch.is_private and self.world.bot_user_id not in ch.members:
            raise _ApiError("channel_not_found")

    # Endpoint implementations

    def _auth_test(self, _: dict[str, str]) -> dict[str, Any]:
        return {
            "team": self.world.team["name"],
            "team_id": self.world.team["id"],
            "user_id": self.world.bot_user_id,
            "user": "toolproof-bot",
            "bot_id": "B0TWIN",
        }

    def _conversations_list(self, p: dict[str, str]) -> dict[str, Any]:
        types = set((p.get("types") or "public_channel").split(","))
        exclude_archived = p.get("exclude_archived") == "true"
        chans = []
        for ch in self.world.channels.values():
            kind = "private_channel" if ch.is_private else "public_channel"
            if kind not in types:
                continue
            if ch.is_private and self.world.bot_user_id not in ch.members:
                continue
            if exclude_archived and ch.is_archived:
                continue
            chans.append(ch)
        page, meta = self._page(chans, p)
        return {"channels": [c.to_api() for c in page], "response_metadata": meta}

    def _conversations_info(self, p: dict[str, str]) -> dict[str, Any]:
        ch = self._channel(p)
        self._readable(ch)
        return {"channel": ch.to_api()}

    def _conversations_history(self, p: dict[str, str]) -> dict[str, Any]:
        ch = self._channel(p)
        self._readable(ch)
        if self.world.bot_user_id not in ch.members:
            raise _ApiError("not_in_channel")
        msgs = list(reversed(self.world.top_level(ch.id)))  # newest first, like Slack
        oldest = float(p.get("oldest") or 0)
        latest = float(p["latest"]) if p.get("latest") else float("inf")
        msgs = (
            [m for m in msgs if oldest < float(m.ts) < latest]
            if (p.get("oldest") or p.get("latest"))
            else msgs
        )
        page, meta = self._page(msgs, p)
        out = [m.to_api(self.world.replies(ch.id, m.ts)) for m in page]
        return {"messages": out, "has_more": bool(meta["next_cursor"]), "response_metadata": meta}

    def _conversations_replies(self, p: dict[str, str]) -> dict[str, Any]:
        ch = self._channel(p)
        self._readable(ch)
        parent = self.world.find_message(ch.id, p.get("ts", ""))
        if parent is None:
            raise _ApiError("thread_not_found")
        thread = [parent, *self.world.replies(ch.id, parent.ts)]  # oldest first, parent included
        page, meta = self._page(thread, p)
        return {
            "messages": [m.to_api() for m in page],
            "has_more": bool(meta["next_cursor"]),
            "response_metadata": meta,
        }

    def _users_list(self, p: dict[str, str]) -> dict[str, Any]:
        page, meta = self._page(list(self.world.users.values()), p)
        return {"members": [u.to_api() for u in page], "response_metadata": meta}

    def _users_info(self, p: dict[str, str]) -> dict[str, Any]:
        user = self.world.users.get(p.get("user", ""))
        if user is None:
            raise _ApiError("user_not_found")
        return {"user": user.to_api()}

    def _users_lookup_by_email(self, p: dict[str, str]) -> dict[str, Any]:
        email = p.get("email", "").lower()
        user = next((u for u in self.world.users.values() if (u.email or "").lower() == email), None)
        if user is None:
            raise _ApiError("users_not_found")
        return {"user": user.to_api()}

    def _chat_post_message(self, p: dict[str, str]) -> dict[str, Any]:
        ch = self._channel(p)
        if ch.is_archived:
            raise _ApiError("is_archived")
        if self.world.bot_user_id not in ch.members:
            raise _ApiError("not_in_channel")
        text = p.get("text", "")
        if not text.strip():
            raise _ApiError("no_text")
        if len(text) > 40_000:
            raise _ApiError("msg_too_long")
        thread_ts = p.get("thread_ts") or None
        if thread_ts:
            parent = self.world.find_message(ch.id, thread_ts)
            if parent is None:
                raise _ApiError("thread_not_found")
            if parent.thread_ts and parent.thread_ts != parent.ts:
                thread_ts = parent.thread_ts  # replying to a reply lands in the parent thread
        msg = self.world.post_message(ch.id, self.world.bot_user_id, text, thread_ts)
        return {"channel": ch.id, "ts": msg.ts, "message": msg.to_api()}

    def _reactions_add(self, p: dict[str, str]) -> dict[str, Any]:
        ch = self._channel(p)
        name = p.get("name", "").strip(":")
        if not name or not all(c.isalnum() or c in "_-+'" for c in name):
            raise _ApiError("invalid_name")
        if self.world.find_message(ch.id, p.get("timestamp", "")) is None:
            raise _ApiError("message_not_found")
        if not self.world.add_reaction(ch.id, p["timestamp"], name, self.world.bot_user_id):
            raise _ApiError("already_reacted")
        return {}

    def _oauth_access(self, p: dict[str, str]) -> dict[str, Any]:
        client = self.oauth_clients.get(p.get("client_id", ""))
        if client is None or client.get("secret") != p.get("client_secret"):
            return {"ok": False, "error": "invalid_client_id"}
        if p.get("grant_type") == "refresh_token":
            if p.get("refresh_token") != client.get("refresh_token"):
                return {"ok": False, "error": "invalid_refresh_token"}
            client["generation"] = client.get("generation", 0) + 1
            client["refresh_token"] = f"xoxe-1-refresh-{client['generation']}"
            self.world.token = f"xoxe.xoxb-rotated-{client['generation']}"
            return {
                "ok": True,
                "access_token": self.world.token,
                "token_type": "bot",
                "scope": ",".join(sorted(self.world.scopes)),
                "refresh_token": client["refresh_token"],
                "expires_in": 43200,
            }
        if p.get("code") != client.get("code"):
            return {"ok": False, "error": "invalid_code"}
        if p.get("redirect_uri") != client.get("redirect_uri"):
            return {"ok": False, "error": "bad_redirect_uri"}
        client["refresh_token"] = "xoxe-1-refresh-0"
        return {
            "ok": True,
            "access_token": self.world.token,
            "token_type": "bot",
            "scope": ",".join(sorted(self.world.scopes)),
            "bot_user_id": self.world.bot_user_id,
            "app_id": "A0TWIN",
            "team": dict(self.world.team),
            "authed_user": {"id": "U01PRIYA"},
            "refresh_token": client["refresh_token"],
            "expires_in": 43200,
        }
