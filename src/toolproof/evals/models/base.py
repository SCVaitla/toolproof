"""Provider-neutral conversation types and the adapter interface.

Adapters are stateless: each ``generate`` call converts the whole neutral history into the
provider's wire format. Assistant turns keep the provider's *raw* payload and replay it verbatim,
which preserves things the neutral format can't represent (Anthropic thinking blocks, Gemini
thought signatures) and that providers reject if you drop them.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None when the model emitted unparseable arguments
    raw_arguments: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class AssistantTurn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
    usage: Usage = field(default_factory=Usage)


@dataclass
class ToolResultMsg:
    call_id: str
    name: str
    content: str
    is_error: bool


@dataclass
class Message:
    role: Literal["user", "assistant", "tool_results"]
    text: str = ""
    turn: AssistantTurn | None = None
    results: list[ToolResultMsg] = field(default_factory=list)


class ModelError(RuntimeError):
    """The model provider failed (auth, quota, outage). Recorded as an infra error, not a model miss."""


class ModelAdapter(ABC):
    provider: str = "base"

    def __init__(self, model: str) -> None:
        self.model = model

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    def for_case(self, case_id: str) -> ModelAdapter:
        """Return the adapter to use for one trial. Stateless adapters return ``self``; scripted
        models return a per-case copy so concurrent trials never share state."""
        return self

    @abstractmethod
    async def generate(
        self, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> AssistantTurn: ...

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass


class HttpModelAdapter(ModelAdapter):
    """Shared HTTP plumbing: API key from env, retries on 429/5xx/overloaded with backoff."""

    env_key: str = ""
    max_attempts = 6

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 120.0,
        max_tokens: int = 2048,
    ) -> None:
        super().__init__(model)
        self.api_key = api_key or os.environ.get(self.env_key, "")
        if not self.api_key:
            raise ModelError(f"{self.provider}: set {self.env_key} to evaluate {model}")
        self.max_tokens = max_tokens
        self._http = httpx.AsyncClient(
            base_url=base_url or self.default_base_url(), transport=transport, timeout=timeout
        )

    def default_base_url(self) -> str:
        raise NotImplementedError

    async def _post(self, path: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        delay = 2.0
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = await self._http.post(path, json=body, headers=headers)
            except httpx.TransportError as exc:
                if attempt == self.max_attempts:
                    raise ModelError(f"{self.label}: transport error {exc}") from exc
            else:
                if resp.status_code < 400:
                    return resp.json()
                retryable = resp.status_code in (408, 409, 429, 529) or resp.status_code >= 500
                if not retryable or attempt == self.max_attempts:
                    raise ModelError(f"{self.label}: HTTP {resp.status_code}: {resp.text[:500]}")
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.replace(".", "", 1).isdigit():
                    delay = max(delay, float(retry_after))
            await asyncio.sleep(min(delay, 60))
            delay *= 2
        raise ModelError(f"{self.label}: exhausted retries")  # pragma: no cover

    async def aclose(self) -> None:
        await self._http.aclose()
