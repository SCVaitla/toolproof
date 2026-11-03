"""Anthropic Messages API adapter (tool use)."""

from __future__ import annotations

from typing import Any

from toolproof.evals.models.base import (
    AssistantTurn,
    HttpModelAdapter,
    Message,
    ToolCall,
    ToolSpec,
    Usage,
)

API_VERSION = "2023-06-01"


class AnthropicAdapter(HttpModelAdapter):
    provider = "anthropic"
    env_key = "ANTHROPIC_API_KEY"

    def default_base_url(self) -> str:
        return "https://api.anthropic.com/v1/"

    def build_request(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> dict[str, Any]:
        wire: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "user":
                wire.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                assert m.turn is not None
                content = (m.turn.raw or {}).get("content") if isinstance(m.turn.raw, dict) else None
                if content is None:
                    content = []
                    if m.turn.text:
                        content.append({"type": "text", "text": m.turn.text})
                    content += [
                        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments or {}}
                        for c in m.turn.tool_calls
                    ]
                wire.append({"role": "assistant", "content": content})
            else:
                wire.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r.call_id,
                                "content": r.content,
                                "is_error": r.is_error,
                            }
                            for r in m.results
                        ],
                    }
                )
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": wire,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
            ],
        }

    @staticmethod
    def parse_response(data: dict[str, Any]) -> AssistantTurn:
        text_parts, calls = [], []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(id=block["id"], name=block["name"], arguments=block.get("input") or {}))
        usage = data.get("usage") or {}
        return AssistantTurn(
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            raw={"content": data.get("content", [])},
            usage=Usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0)),
        )

    async def generate(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> AssistantTurn:
        data = await self._post(
            "messages",
            self.build_request(system, messages, tools),
            {"x-api-key": self.api_key, "anthropic-version": API_VERSION},
        )
        return self.parse_response(data)
