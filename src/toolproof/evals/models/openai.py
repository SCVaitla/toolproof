"""OpenAI Chat Completions adapter. Works with any OpenAI-compatible endpoint via OPENAI_BASE_URL."""

from __future__ import annotations

import json
import os
from typing import Any

from toolproof.evals.models.base import (
    AssistantTurn,
    HttpModelAdapter,
    Message,
    ToolCall,
    ToolSpec,
    Usage,
)


class OpenAIAdapter(HttpModelAdapter):
    provider = "openai"
    env_key = "OPENAI_API_KEY"

    def default_base_url(self) -> str:
        return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1/").rstrip("/") + "/"

    def build_request(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> dict[str, Any]:
        wire: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "user":
                wire.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                assert m.turn is not None
                msg: dict[str, Any] = {"role": "assistant", "content": m.turn.text or None}
                if m.turn.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                # Replay exactly what the model produced, even if it was malformed.
                                "arguments": c.raw_arguments
                                if c.raw_arguments is not None
                                else json.dumps(c.arguments or {}),
                            },
                        }
                        for c in m.turn.tool_calls
                    ]
                wire.append(msg)
            else:
                wire.extend(
                    {"role": "tool", "tool_call_id": r.call_id, "content": r.content} for r in m.results
                )
        return {
            "model": self.model,
            "messages": wire,
            "tools": [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in tools
            ],
            "tool_choice": "auto",
            "max_completion_tokens": self.max_tokens,
        }

    @staticmethod
    def parse_response(data: dict[str, Any]) -> AssistantTurn:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
                if not isinstance(args, dict):
                    args = None
            except json.JSONDecodeError:
                args = None
            calls.append(
                ToolCall(id=tc["id"], name=fn.get("name", ""), arguments=args, raw_arguments=raw_args)
            )
        usage = data.get("usage") or {}
        return AssistantTurn(
            text=(msg.get("content") or "").strip(),
            tool_calls=calls,
            raw=msg,
            usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
        )

    async def generate(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> AssistantTurn:
        data = await self._post(
            "chat/completions",
            self.build_request(system, messages, tools),
            {"Authorization": f"Bearer {self.api_key}"},
        )
        return self.parse_response(data)
