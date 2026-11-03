"""Run a model against the MCP server through an in-memory session."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from mcp import ClientSession

from toolproof.evals.models.base import (
    Message,
    ModelAdapter,
    ToolResultMsg,
    ToolSpec,
    Usage,
)

SYSTEM_PROMPT = """\
You are a careful assistant operating a company Slack workspace through tools, acting as the bot user.
Complete the user's request using the tools. Only take actions the request calls for: do not post,
react or message anywhere else. If the request cannot be completed (for example, no access, or the
channel is archived), do not work around it; explain why in your final reply. When you are done,
reply with a short plain-text summary of what you did or found, without calling more tools."""

MAX_RESULT_CHARS = 12_000


@dataclass
class CallRecord:
    turn: int
    tool: str
    args: dict[str, Any] | None
    is_error: bool
    error_kind: str | None  # malformed_args | unknown_tool | schema | runtime
    result: str
    raw_arguments: str | None = None

    @property
    def valid(self) -> bool:
        return self.error_kind not in {"malformed_args", "unknown_tool", "schema"}


@dataclass
class Transcript:
    calls: list[CallRecord] = field(default_factory=list)
    final_answer: str = ""
    turns: int = 0
    hit_max_turns: bool = False
    usage: Usage = field(default_factory=Usage)


async def list_tool_specs(session: ClientSession) -> list[ToolSpec]:
    tools = (await session.list_tools()).tools
    return [ToolSpec(name=t.name, description=t.description or "", input_schema=t.inputSchema) for t in tools]


def _result_text(result: Any) -> str:
    parts = [getattr(c, "text", "") for c in result.content if getattr(c, "type", "") == "text"]
    text = "\n".join(p for p in parts if p)
    if not text and getattr(result, "structuredContent", None) is not None:
        text = json.dumps(result.structuredContent)
    return text[:MAX_RESULT_CHARS]


async def run_agent(
    model: ModelAdapter, session: ClientSession, prompt: str, *, max_turns: int = 12
) -> Transcript:
    specs = await list_tool_specs(session)
    validators = {s.name: Draft202012Validator(s.input_schema) for s in specs}
    messages: list[Message] = [Message(role="user", text=prompt)]
    t = Transcript()

    for turn in range(1, max_turns + 1):
        reply = await model.generate(SYSTEM_PROMPT, messages, specs)
        t.turns = turn
        t.usage.add(reply.usage)
        messages.append(Message(role="assistant", turn=reply))
        if not reply.tool_calls:
            t.final_answer = reply.text
            return t

        results: list[ToolResultMsg] = []
        for call in reply.tool_calls:
            if call.arguments is None:
                rec = CallRecord(
                    turn,
                    call.name,
                    None,
                    True,
                    "malformed_args",
                    '{"error": "invalid_arguments", "message": "arguments were not valid JSON"}',
                    raw_arguments=call.raw_arguments,
                )
            elif call.name not in validators:
                rec = CallRecord(
                    turn,
                    call.name,
                    call.arguments,
                    True,
                    "unknown_tool",
                    json.dumps(
                        {
                            "error": "unknown_tool",
                            "message": f"No tool named {call.name!r}",
                            "available": sorted(validators),
                        }
                    ),
                )
            else:
                schema_errors = sorted(
                    validators[call.name].iter_errors(call.arguments), key=lambda e: e.path
                )
                result = await session.call_tool(call.name, call.arguments)
                kind = None
                if result.isError:
                    kind = "schema" if schema_errors else "runtime"
                rec = CallRecord(
                    turn, call.name, call.arguments, bool(result.isError), kind, _result_text(result)
                )
            t.calls.append(rec)
            results.append(
                ToolResultMsg(call_id=call.id, name=call.name, content=rec.result, is_error=rec.is_error)
            )
        messages.append(Message(role="tool_results", results=results))

    t.hit_max_turns = True
    last = next((m.turn for m in reversed(messages) if m.role == "assistant" and m.turn), None)
    t.final_answer = last.text if last else ""
    return t
