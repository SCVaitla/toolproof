"""In-memory Slack workspace used by the evaluation harness.

Every mutation is journaled, so after an agent runs we can diff "the world it left behind" against
what the task required, and count every unintended side effect (the agent's *blast radius*).
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


@dataclass
class User:
    id: str
    name: str
    real_name: str
    email: str | None = None
    display_name: str = ""
    title: str = ""
    tz: str = "America/Los_Angeles"
    is_bot: bool = False
    deleted: bool = False

    def to_api(self) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "real_name": self.real_name,
            "display_name": self.display_name or self.name,
            "title": self.title,
        }
        if self.email:
            profile["email"] = self.email
        return {
            "id": self.id,
            "name": self.name,
            "real_name": self.real_name,
            "deleted": self.deleted,
            "is_bot": self.is_bot,
            "tz": self.tz,
            "profile": profile,
        }


@dataclass
class Channel:
    id: str
    name: str
    is_private: bool = False
    is_archived: bool = False
    topic: str = ""
    purpose: str = ""
    members: list[str] = field(default_factory=list)

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "is_channel": not self.is_private,
            "is_group": self.is_private,
            "is_private": self.is_private,
            "is_archived": self.is_archived,
            "num_members": len(self.members),
            "topic": {"value": self.topic},
            "purpose": {"value": self.purpose},
        }


@dataclass
class Message:
    ts: str
    user: str
    text: str
    thread_ts: str | None = None
    reactions: dict[str, list[str]] = field(default_factory=dict)

    def to_api(self, replies: list[Message] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "message", "ts": self.ts, "user": self.user, "text": self.text}
        if self.thread_ts:
            out["thread_ts"] = self.thread_ts
        if replies:
            out["thread_ts"] = self.ts
            out["reply_count"] = len(replies)
            out["latest_reply"] = replies[-1].ts
        if self.reactions:
            out["reactions"] = [
                {"name": name, "users": list(users), "count": len(users)}
                for name, users in self.reactions.items()
            ]
        return out


@dataclass(frozen=True)
class Mutation:
    kind: str  # "message_posted" | "reaction_added"
    channel_id: str
    ts: str
    data: dict[str, Any]


class World:
    def __init__(
        self,
        *,
        team: dict[str, str],
        bot_user_id: str,
        token: str,
        scopes: list[str],
        users: list[User],
        channels: list[Channel],
        messages: dict[str, list[Message]],
        clock_start: float = 1_730_000_000.0,
    ) -> None:
        self.team = team
        self.bot_user_id = bot_user_id
        self.token = token
        self.scopes = set(scopes)
        self.users = {u.id: u for u in users}
        self.channels = {c.id: c for c in channels}
        self.messages = messages  # channel_id -> messages sorted by ts ascending (incl. replies)
        self.mutations: list[Mutation] = []
        latest = max((float(m.ts) for ms in messages.values() for m in ms), default=clock_start)
        self._next_ts = max(latest, clock_start) + 60

    # Queries

    def channel_by_name(self, name: str) -> Channel | None:
        name = name.lstrip("#").lower()
        return next((c for c in self.channels.values() if c.name == name), None)

    def resolve_channel(self, ref: str) -> Channel | None:
        return self.channels.get(ref) or self.channel_by_name(ref)

    def user_by_name(self, name: str) -> User | None:
        name = name.lstrip("@").lower()
        return next((u for u in self.users.values() if u.name == name), None)

    def top_level(self, channel_id: str) -> list[Message]:
        return [m for m in self.messages.get(channel_id, []) if not m.thread_ts or m.thread_ts == m.ts]

    def replies(self, channel_id: str, thread_ts: str) -> list[Message]:
        return [
            m for m in self.messages.get(channel_id, []) if m.thread_ts == thread_ts and m.ts != thread_ts
        ]

    def find_message(self, channel_id: str, ts: str) -> Message | None:
        return next((m for m in self.messages.get(channel_id, []) if m.ts == ts), None)

    # Mutations

    def _new_ts(self) -> str:
        self._next_ts += 1.0
        return f"{self._next_ts:.6f}"

    def post_message(self, channel_id: str, user: str, text: str, thread_ts: str | None) -> Message:
        msg = Message(ts=self._new_ts(), user=user, text=text, thread_ts=thread_ts)
        self.messages.setdefault(channel_id, []).append(msg)
        self.mutations.append(
            Mutation(
                "message_posted", channel_id, msg.ts, {"text": text, "thread_ts": thread_ts, "user": user}
            )
        )
        return msg

    def add_reaction(self, channel_id: str, ts: str, name: str, user: str) -> bool:
        msg = self.find_message(channel_id, ts)
        assert msg is not None
        users = msg.reactions.setdefault(name, [])
        if user in users:
            return False
        users.append(user)
        self.mutations.append(Mutation("reaction_added", channel_id, ts, {"name": name, "user": user}))
        return True

    def clone(self) -> World:
        return copy.deepcopy(self)

    # Fixture loading

    @classmethod
    def from_fixture(cls, ref: str | Path) -> World:
        return cls.from_dict(load_fixture(ref))

    @classmethod
    def from_dict(cls, spec: dict[str, Any]) -> World:
        rng = random.Random(spec.get("seed", 7))
        users = [User(**u) for u in spec.get("users", [])]
        channels = [Channel(**c) for c in spec.get("channels", [])]
        by_name = {c.name: c for c in channels}

        gen = spec.get("generate", {}) or {}
        first_names = [
            "alex",
            "jordan",
            "sam",
            "taylor",
            "morgan",
            "casey",
            "riley",
            "jamie",
            "devon",
            "quinn",
        ]
        for i in range(int(gen.get("users", 0))):
            first = rng.choice(first_names)
            users.append(
                User(
                    id=f"U{1000000 + i:07d}",
                    name=f"{first}{i:02d}",
                    real_name=f"{first.title()} Gen{i:02d}",
                    email=f"{first}{i:02d}@acme.dev",
                )
            )
        for i in range(int(gen.get("channels", 0))):
            prefix = rng.choice(["proj", "team", "ops", "help", "social", "eng", "sales"])
            ch = Channel(id=f"C{9000 + i:07d}", name=f"{prefix}-{i:03d}", topic=f"Generated {prefix} channel")
            channels.append(ch)
            by_name[ch.name] = ch

        all_user_ids = [u.id for u in users if not u.is_bot]
        for ch in channels:
            if not ch.members:
                ch.members = [*all_user_ids, spec["bot"]["user_id"]]

        messages: dict[str, list[Message]] = {}
        for raw in spec.get("messages", []):
            raw = dict(raw)
            ch = by_name[raw.pop("channel")]
            reactions = {k: list(v) for k, v in (raw.pop("reactions", None) or {}).items()}
            messages.setdefault(ch.id, []).append(Message(reactions=reactions, **raw))

        for filler in gen.get("filler", []) or []:
            ch = by_name[filler["channel"]]
            base = float(filler.get("start_ts", 1_729_000_000))
            for i in range(int(filler["count"])):
                user = rng.choice(all_user_ids)
                text = rng.choice(_FILLER_LINES).format(n=i)
                messages.setdefault(ch.id, []).append(
                    Message(ts=f"{base + i * 37:.6f}", user=user, text=text)
                )

        for msgs in messages.values():
            msgs.sort(key=lambda m: float(m.ts))
        # Put generated channels first so channel lookup has to cross a page boundary.
        channels.sort(key=lambda c: c.id)
        bot = spec["bot"]
        return cls(
            team=spec.get("team", {"id": "T0TWIN", "name": "Twin"}),
            bot_user_id=bot["user_id"],
            token=bot["token"],
            scopes=bot.get("scopes", []),
            users=users,
            channels=channels,
            messages=messages,
        )


_FILLER_LINES = [
    "sync notes #{n}: nothing blocking",
    "lgtm, merging #{n}",
    "anyone seen the flaky test in build {n}?",
    "reminder: retro at 3pm (item {n})",
    "dashboards look green {n}",
    "pushed a fix for issue {n}",
]

_FIXTURE_CACHE: dict[str, dict[str, Any]] = {}


def load_fixture(ref: str | Path) -> dict[str, Any]:
    """Load a built-in or file-based fixture and return an isolated copy."""
    key = str(ref)
    if key not in _FIXTURE_CACHE:
        path = Path(ref)
        if path.suffix in {".yaml", ".yml"} and path.exists():
            text = path.read_text()
        else:
            text = resources.files("toolproof.twin.fixtures").joinpath(f"{key}.yaml").read_text()
        _FIXTURE_CACHE[key] = yaml.safe_load(text)
    return copy.deepcopy(_FIXTURE_CACHE[key])
