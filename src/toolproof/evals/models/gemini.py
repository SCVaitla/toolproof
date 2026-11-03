"""Google Gemini (Generative Language API) adapter.

Gemini accepts an OpenAPI-style *subset* of JSON Schema for function parameters, so MCP input
schemas (which use ``anyOf`` with null, ``title``, ``default``, ``additionalProperties``...) are
sanitized first. Model turns are replayed with their raw ``parts`` so thought signatures survive.
"""

from __future__ import annotations

import json
from typing import Any

from toolproof.evals.models.base import (
    AssistantTurn,
    HttpModelAdapter,
    Message,
    ToolCall,
    ToolSpec,
    Usage,
)

_KEEP = {
    "type",
    "description",
    "enum",
    "properties",
    "required",
    "items",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "nullable",
    "format",
    "minLength",
    "maxLength",
}
_FORMATS = {"enum", "date-time", "int32", "int64", "float", "double"}


def sanitize_schema(schema: Any) -> Any:
    """Convert a JSON Schema into the subset Gemini's function declarations accept."""
    if not isinstance(schema, dict):
        return schema
    s = dict(schema)
    any_of = s.pop("anyOf", None)
    if any_of:
        non_null = [o for o in any_of if o.get("type") != "null"]
        if len(non_null) == 1:
            merged = {**non_null[0], **{k: v for k, v in s.items() if k != "type"}}
            if len(non_null) < len(any_of):
                merged["nullable"] = True
            return sanitize_schema(merged)
        s.setdefault("type", (non_null[0] if non_null else {}).get("type", "string"))
    out: dict[str, Any] = {}
    for key, value in s.items():
        if key not in _KEEP:
            continue
        if key == "type":
            out["type"] = str(value).upper()
        elif key == "properties":
            out["properties"] = {k: sanitize_schema(v) for k, v in value.items()}
        elif key == "items":
            out["items"] = sanitize_schema(value)
        elif key == "format" and value not in _FORMATS:
            continue
        else:
            out[key] = value
    if out.get("type") == "OBJECT" and "properties" in out and out.get("required"):
        out["required"] = [r for r in out["required"] if r in out["properties"]]
    return out


class GeminiAdapter(HttpModelAdapter):
    provider = "gemini"
    env_key = "GEMINI_API_KEY"

    def default_base_url(self) -> str:
        return "https://generativelanguage.googleapis.com/v1beta/"

    def build_request(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "user":
                contents.append({"role": "user", "parts": [{"text": m.text}]})
            elif m.role == "assistant":
                assert m.turn is not None
                parts = m.turn.raw.get("parts") if isinstance(m.turn.raw, dict) else None
                if not parts:
                    parts = ([{"text": m.turn.text}] if m.turn.text else []) + [
                        {"functionCall": {"name": c.name, "args": c.arguments or {}}}
                        for c in m.turn.tool_calls
                    ]
                contents.append({"role": "model", "parts": parts})
            else:
                parts = []
                for r in m.results:
                    try:
                        payload = json.loads(r.content)
                        if not isinstance(payload, dict):
                            payload = {"result": payload}
                    except json.JSONDecodeError:
                        payload = {"result": r.content}
                    response = {"error": payload} if r.is_error else payload
                    fr: dict[str, Any] = {"name": r.name, "response": response}
                    if not r.call_id.startswith("gemini-"):
                        fr["id"] = r.call_id
                    parts.append({"functionResponse": fr})
                contents.append({"role": "user", "parts": parts})
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": sanitize_schema(t.input_schema),
                        }
                        for t in tools
                    ]
                }
            ],
            "generationConfig": {"maxOutputTokens": self.max_tokens},
        }

    @staticmethod
    def parse_response(data: dict[str, Any]) -> AssistantTurn:
        candidates = data.get("candidates") or []
        parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
        texts, calls = [], []
        for i, part in enumerate(parts):
            if "text" in part and not part.get("thought"):
                texts.append(part["text"])
            if "functionCall" in part:
                fc = part["functionCall"]
                args = fc.get("args")
                calls.append(
                    ToolCall(
                        id=fc.get("id") or f"gemini-{i}",
                        name=fc.get("name", ""),
                        arguments=args if isinstance(args, dict) else ({} if args is None else None),
                    )
                )
        usage = data.get("usageMetadata") or {}
        return AssistantTurn(
            text="".join(texts).strip(),
            tool_calls=calls,
            raw={"parts": parts},
            usage=Usage(usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)),
        )

    async def generate(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> AssistantTurn:
        data = await self._post(
            f"models/{self.model}:generateContent",
            self.build_request(system, messages, tools),
            {"x-goog-api-key": self.api_key},
        )
        return self.parse_response(data)
