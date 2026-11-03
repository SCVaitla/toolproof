"""MCP tools for the Slack integration.

The public tool surface accepts either Slack IDs or human-friendly names. Resolution, pagination,
and retry handling live here so callers do not need to duplicate Slack-specific bookkeeping.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from toolproof.slack.client import SlackClient
from toolproof.slack.errors import (
    AuthError,
    MissingScopeError,
    OutcomeUnknownError,
    RateLimitedError,
    SlackApiError,
    SlackError,
)

CHANNEL_ID_RE = re.compile(r"^[CGD][A-Z0-9]{6,}$")
USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{5,}$")
TS_RE = re.compile(r"^\d{9,11}\.\d{1,6}$")
MAX_TEXT = 4000
SEND_ATTEMPTS = 3

SERVER_INSTRUCTIONS = """\
Tools for reading and writing a Slack workspace as a bot user.
- Channels can be referenced by name ("#releases" or "releases") or ID ("C0123ABC").
- To mention someone so they are notified, use the format <@USER_ID> (find IDs with slack_find_users).
- Message timestamps ("ts") identify messages; pass them as thread_ts to reply in a thread.
- Tool errors are JSON. If "retryable" is true, wait "retry_after_seconds" and retry. Never re-send a
  message after an error without checking whether it was already posted.
"""

_HINTS = {
    "channel_not_found": "The channel doesn't exist or the bot can't see it (private channels need an invite).",
    "not_in_channel": "The bot is not a member of this channel. Ask a human to invite it; do not post elsewhere.",
    "is_archived": "The channel is archived and read-only. Tell the user instead of posting somewhere else.",
    "thread_not_found": "No message with that thread_ts exists in this channel. Re-read history to get the ts.",
    "message_not_found": "No message with that timestamp exists in this channel. Re-read history to get the ts.",
    "user_not_found": "Unknown user ID. Use slack_find_users to look the person up by name or email.",
    "invalid_name": "Emoji names are like 'thumbsup' or '+1', without colons or unicode emoji.",
    "msg_too_long": f"Shorten the message to under {MAX_TEXT} characters.",
    "ratelimited": "Slack is rate limiting this method. Wait retry_after_seconds before retrying.",
    "outcome_unknown": "The write may or may not have applied. Check history before retrying.",
}


def _error(code: str, message: str, *, retryable: bool = False, **extra: Any) -> ToolError:
    payload: dict[str, Any] = {"error": code, "message": message, "retryable": retryable}
    if code in _HINTS and "hint" not in extra:
        payload["hint"] = _HINTS[code]
    payload.update({k: v for k, v in extra.items() if v is not None})
    return ToolError(json.dumps(payload, ensure_ascii=False))


@asynccontextmanager
async def _boundary():
    """Convert Slack client failures to the JSON error format exposed by the tools."""
    try:
        yield
    except ToolError:
        raise
    except RateLimitedError as e:
        raise _error(e.code, str(e), retryable=True, retry_after_seconds=e.retry_after) from e
    except MissingScopeError as e:
        raise _error(
            e.code, str(e), needed_scope=e.needed, hint="A workspace admin must grant this scope."
        ) from e
    except AuthError as e:
        raise _error(e.code, str(e), hint="Authentication failed; the user must re-authorize the app.") from e
    except OutcomeUnknownError as e:
        raise _error(e.code, str(e)) from e
    except SlackApiError as e:
        raise _error(e.code, str(e)) from e
    except SlackError as e:
        raise _error(e.code, str(e), retryable=e.retryable) from e


class SlackTools:
    """Shared Slack state for a single MCP server instance."""

    def __init__(self, client: SlackClient) -> None:
        self.client = client
        self._channel_cache: dict[str, dict[str, Any]] = {}
        self._user_cache: dict[str, dict[str, Any]] = {}
        self._bot_user_id: str | None = None

    async def bot_user_id(self) -> str:
        if self._bot_user_id is None:
            self._bot_user_id = (await self.client.call("auth.test"))["user_id"]
        return self._bot_user_id

    async def resolve_channel(self, ref: str) -> dict[str, Any]:
        raw = (ref or "").strip()
        name = raw.lstrip("#").strip()
        if not name:
            raise _error("invalid_arguments", "channel is required")
        if CHANNEL_ID_RE.match(name):
            return {"id": name, "name": None}
        name = name.lower()
        if name in self._channel_cache:
            return self._channel_cache[name]
        seen: list[str] = []
        async for ch, _ in self.client.paginate(
            "conversations.list", "channels", page_size=200, types="public_channel,private_channel"
        ):
            entry = {"id": ch["id"], "name": ch["name"], "is_archived": ch.get("is_archived", False)}
            self._channel_cache[ch["name"]] = entry
            seen.append(ch["name"])
            if ch["name"] == name:
                return entry
        suggestions = difflib.get_close_matches(name, seen, n=3, cutoff=0.6)
        raise _error(
            "channel_not_found",
            f"No channel named '#{name}' is visible to the bot.",
            did_you_mean=[f"#{s}" for s in suggestions] or None,
        )

    async def user_name(self, user_id: str | None) -> str | None:
        if not user_id:
            return None
        if user_id not in self._user_cache:
            try:
                self._user_cache[user_id] = (await self.client.call("users.info", user=user_id))["user"]
            except SlackError:
                return None
        user = self._user_cache[user_id]
        return user.get("real_name") or user.get("name")

    async def format_message(self, m: dict[str, Any]) -> dict[str, Any]:
        message = m
        formatted: dict[str, Any] = {
            "ts": message.get("ts"),
            "user": message.get("user"),
            "user_name": await self.user_name(message.get("user")),
            "text": message.get("text", ""),
        }
        if message.get("thread_ts") and message.get("thread_ts") != message.get("ts"):
            formatted["thread_ts"] = message["thread_ts"]
        if message.get("reply_count"):
            formatted["reply_count"] = message["reply_count"]
        if message.get("reactions"):
            formatted["reactions"] = [
                {"name": reaction["name"], "count": reaction.get("count", 0)}
                for reaction in message["reactions"]
            ]
        return formatted


def _user_summary(user: dict[str, Any]) -> dict[str, Any]:
    profile = user.get("profile") or {}
    return {
        "id": user["id"],
        "handle": user.get("name"),
        "real_name": user.get("real_name") or profile.get("real_name"),
        "display_name": profile.get("display_name"),
        "title": profile.get("title") or None,
        "email": profile.get("email"),
        "is_bot": user.get("is_bot", False),
        "mention": f"<@{user['id']}>",
    }


def create_server(
    client: SlackClient, *, name: str = "toolproof-slack", log_level: str = "WARNING"
) -> FastMCP:
    mcp = FastMCP(name, instructions=SERVER_INSTRUCTIONS, log_level=log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    tools = SlackTools(client)
    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

    @mcp.tool(
        name="slack_list_channels",
        description="List channels the bot can see, with topic and member count. Paginates automatically.",
        annotations=read_only,
    )
    async def list_channels(
        include_private: Annotated[
            bool, Field(description="Include private channels the bot is a member of.")
        ] = True,
        include_archived: Annotated[
            bool, Field(description="Include archived (read-only) channels.")
        ] = False,
        limit: Annotated[int, Field(ge=1, le=500, description="Maximum channels to return.")] = 100,
        cursor: Annotated[
            str | None, Field(description="next_cursor from a previous call, to continue.")
        ] = None,
    ) -> dict[str, Any]:
        async with _boundary():
            types = "public_channel,private_channel" if include_private else "public_channel"
            items, nxt = await tools.client.collect(
                "conversations.list",
                "channels",
                max_items=limit,
                cursor=cursor,
                types=types,
                exclude_archived=not include_archived,
            )
            return {
                "channels": [
                    {
                        "id": c["id"],
                        "name": c["name"],
                        "is_private": c.get("is_private", False),
                        "is_archived": c.get("is_archived", False),
                        "topic": (c.get("topic") or {}).get("value", ""),
                        "num_members": c.get("num_members"),
                    }
                    for c in items
                ],
                "next_cursor": nxt,
            }

    @mcp.tool(
        name="slack_get_messages",
        description=(
            "Get top-level messages from a channel, newest first, with author names, reply counts and "
            "reactions. Use slack_get_thread_replies to read a thread."
        ),
        annotations=read_only,
    )
    async def get_messages(
        channel: Annotated[str, Field(description="Channel name ('#releases' or 'releases') or ID.")],
        limit: Annotated[int, Field(ge=1, le=200, description="Maximum messages to return.")] = 20,
        oldest: Annotated[str | None, Field(description="Only messages after this Slack timestamp.")] = None,
        latest: Annotated[str | None, Field(description="Only messages before this Slack timestamp.")] = None,
        cursor: Annotated[
            str | None, Field(description="next_cursor from a previous call, to continue.")
        ] = None,
    ) -> dict[str, Any]:
        async with _boundary():
            ch = await tools.resolve_channel(channel)
            items, nxt = await tools.client.collect(
                "conversations.history",
                "messages",
                max_items=limit,
                page_size=100,
                cursor=cursor,
                channel=ch["id"],
                oldest=oldest,
                latest=latest,
            )
            return {
                "channel": {"id": ch["id"], "name": ch["name"]},
                "messages": [await tools.format_message(m) for m in items],
                "next_cursor": nxt,
            }

    @mcp.tool(
        name="slack_get_thread_replies",
        description="Get a thread: the parent message followed by its replies, oldest first.",
        annotations=read_only,
    )
    async def get_thread_replies(
        channel: Annotated[str, Field(description="Channel name or ID containing the thread.")],
        thread_ts: Annotated[str, Field(description="The 'ts' of the thread's parent message.")],
        limit: Annotated[int, Field(ge=1, le=500, description="Maximum messages to return.")] = 100,
    ) -> dict[str, Any]:
        async with _boundary():
            if not TS_RE.match(thread_ts.strip()):
                raise _error(
                    "invalid_arguments", f"'{thread_ts}' is not a Slack timestamp like '1730000000.000100'"
                )
            ch = await tools.resolve_channel(channel)
            items, nxt = await tools.client.collect(
                "conversations.replies",
                "messages",
                max_items=limit,
                page_size=200,
                channel=ch["id"],
                ts=thread_ts.strip(),
            )
            return {
                "channel": {"id": ch["id"], "name": ch["name"]},
                "messages": [await tools.format_message(m) for m in items],
                "truncated": nxt is not None,
            }

    @mcp.tool(
        name="slack_find_users",
        description=(
            "Find people by name, handle or email. Returns IDs plus a ready-to-use mention string "
            "(e.g. '<@U123>'). If several people match, all are returned; ask the user to disambiguate."
        ),
        annotations=read_only,
    )
    async def find_users(
        query: Annotated[
            str, Field(min_length=1, description="Full or partial name, @handle, or email address.")
        ],
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum matches to return.")] = 10,
    ) -> dict[str, Any]:
        async with _boundary():
            q = query.strip().lstrip("@").lower()
            if "@" in q and "." in q.split("@")[-1]:
                try:
                    user = (await tools.client.call("users.lookupByEmail", email=q))["user"]
                    return {"matches": [_user_summary(user)]}
                except SlackApiError as e:
                    if e.code != "users_not_found":
                        raise
                    return {"matches": []}
            terms = q.split()
            matches = []
            async for user, _ in tools.client.paginate("users.list", "members", page_size=200):
                if user.get("deleted"):
                    continue
                profile = user.get("profile") or {}
                hay = " ".join(
                    str(x or "") for x in (user.get("name"), user.get("real_name"), profile.get("display_name"))
                ).lower()
                if all(t in hay for t in terms):
                    matches.append(_user_summary(user))
                    if len(matches) >= limit:
                        break
            return {"matches": matches}

    @mcp.tool(
        name="slack_get_user",
        description="Get a user's profile by user ID.",
        annotations=read_only,
    )
    async def get_user(
        user_id: Annotated[str, Field(description="Slack user ID, e.g. 'U0123ABC'.")],
    ) -> dict[str, Any]:
        async with _boundary():
            if not USER_ID_RE.match(user_id.strip()):
                raise _error(
                    "invalid_arguments",
                    f"'{user_id}' is not a user ID.",
                    hint="Use slack_find_users to look people up by name or email.",
                )
            return _user_summary((await tools.client.call("users.info", user=user_id.strip()))["user"])

    @mcp.tool(
        name="slack_send_message",
        description=(
            "Post a message to a channel, or reply in a thread by passing thread_ts. Mention people with "
            "<@USER_ID>. Safe to call once per intended message: the tool itself handles retries and "
            "detects duplicates, so never call it again for the same message after an error."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
        ),
    )
    async def send_message(
        channel: Annotated[str, Field(description="Channel name ('#releases' or 'releases') or ID.")],
        text: Annotated[
            str, Field(min_length=1, max_length=MAX_TEXT, description="Message text (Slack mrkdwn).")
        ],
        thread_ts: Annotated[
            str | None,
            Field(description="ts of the parent message to reply in its thread. Omit for a new message."),
        ] = None,
    ) -> dict[str, Any]:
        async with _boundary():
            if thread_ts is not None and not TS_RE.match(thread_ts.strip()):
                raise _error("invalid_arguments", f"thread_ts '{thread_ts}' is not a Slack timestamp")
            ch = await tools.resolve_channel(channel)
            if ch.get("is_archived"):
                raise _error("is_archived", f"#{ch['name']} is archived; messages cannot be posted there.")
            ts = thread_ts.strip() if thread_ts else None
            # A failed write may still have reached Slack. Check before trying the post again.
            for attempt in range(1, SEND_ATTEMPTS + 1):
                try:
                    resp = await tools.client.call("chat.postMessage", channel=ch["id"], text=text, thread_ts=ts)
                    out = {"ok": True, "channel": resp["channel"], "ts": resp["ts"], "thread_ts": ts}
                    return {**out, "attempts": attempt} if attempt > 1 else out
                except OutcomeUnknownError:
                    found = await _find_own_message(tools, ch["id"], text, ts)
                    if found:
                        return {
                            "ok": True,
                            "channel": ch["id"],
                            "ts": found,
                            "thread_ts": ts,
                            "recovered": True,
                        }
                    if attempt == SEND_ATTEMPTS:
                        raise
            raise AssertionError("unreachable")  # pragma: no cover

    @mcp.tool(
        name="slack_add_reaction",
        description="Add an emoji reaction to a message. Idempotent: reacting twice is a no-op.",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )
    async def add_reaction(
        channel: Annotated[str, Field(description="Channel name or ID containing the message.")],
        timestamp: Annotated[str, Field(description="The 'ts' of the message to react to.")],
        name: Annotated[str, Field(description="Emoji name without colons, e.g. 'thumbsup', '+1', 'eyes'.")],
    ) -> dict[str, Any]:
        async with _boundary():
            ch = await tools.resolve_channel(channel)
            emoji = name.strip().strip(":")
            try:
                await tools.client.call(
                    "reactions.add", channel=ch["id"], timestamp=timestamp.strip(), name=emoji
                )
                return {"ok": True, "channel": ch["id"], "timestamp": timestamp.strip(), "name": emoji}
            except SlackApiError as e:
                if e.code == "already_reacted":
                    return {"ok": True, "already_reacted": True, "name": emoji}
                raise

    return mcp


async def _find_own_message(
    tools: SlackTools, channel_id: str, text: str, thread_ts: str | None
) -> str | None:
    """Return the timestamp of a matching recent bot message, if one exists."""
    bot_user_id = await tools.bot_user_id()
    if thread_ts:
        recent, _ = await tools.client.collect(
            "conversations.replies", "messages", max_items=200, channel=channel_id, ts=thread_ts
        )
        # If the supplied timestamp points at a reply, verify against the thread root instead.
        first = recent[0] if recent else {}
        root = first.get("thread_ts")
        if root and root != first.get("ts"):
            recent, _ = await tools.client.collect(
                "conversations.replies", "messages", max_items=200, channel=channel_id, ts=root
            )
        recent = list(reversed(recent))
    else:
        recent, _ = await tools.client.collect(
            "conversations.history", "messages", max_items=20, channel=channel_id
        )
    for message in recent:
        if message.get("user") == bot_user_id and message.get("text") == text:
            return message.get("ts")
    return None
