from __future__ import annotations

import json

import httpx
import pytest

from toolproof.evals.models import (
    AnthropicAdapter,
    AssistantTurn,
    GeminiAdapter,
    Message,
    ModelError,
    OpenAIAdapter,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
    make_model,
    sanitize_schema,
)

TOOLS = [
    ToolSpec(
        "slack_send_message",
        "Post a message",
        {
            "type": "object",
            "title": "Args",
            "additionalProperties": False,
            "properties": {
                "channel": {"type": "string", "title": "Channel", "description": "Channel"},
                "thread_ts": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "description": "ts",
                },
            },
            "required": ["channel"],
        },
    )
]


def _history(turn: AssistantTurn) -> list[Message]:
    return [
        Message(role="user", text="post hi"),
        Message(role="assistant", turn=turn),
        Message(
            role="tool_results",
            results=[ToolResultMsg(turn.tool_calls[0].id, "slack_send_message", '{"ok": true}', False)],
        ),
    ]


def _capture(response: dict):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=response)

    return seen, httpx.MockTransport(handler)


async def test_anthropic_roundtrip():
    resp = {
        "content": [
            {"type": "text", "text": "Posting."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "slack_send_message",
                "input": {"channel": "general"},
            },
        ],
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }
    seen, transport = _capture(resp)
    m = AnthropicAdapter("claude-test", api_key="k", transport=transport)
    turn = await m.generate("sys", [Message(role="user", text="post hi")], TOOLS)
    assert turn.tool_calls[0].arguments == {"channel": "general"} and turn.usage.output_tokens == 7
    assert seen["url"].endswith("/v1/messages") and seen["headers"]["x-api-key"] == "k"
    assert seen["body"]["tools"][0]["input_schema"]["required"] == ["channel"]

    body = m.build_request("sys", _history(turn), TOOLS)
    assert body["messages"][1]["content"][1]["type"] == "tool_use"  # raw blocks replayed
    assert body["messages"][2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": '{"ok": true}',
        "is_error": False,
    }
    await m.aclose()


async def test_openai_roundtrip_and_malformed_arguments():
    resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "slack_send_message", "arguments": '{"channel": "gen'},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    seen, transport = _capture(resp)
    m = OpenAIAdapter("gpt-test", api_key="k", base_url="https://api.test/v1/", transport=transport)
    turn = await m.generate("sys", [Message(role="user", text="hi")], TOOLS)
    assert turn.tool_calls[0].arguments is None and turn.tool_calls[0].raw_arguments == '{"channel": "gen'
    assert seen["headers"]["authorization"] == "Bearer k"
    assert seen["body"]["messages"][0]["role"] == "system"
    assert seen["body"]["tools"][0]["function"]["parameters"]["required"] == ["channel"]

    body = m.build_request("sys", _history(turn), TOOLS)
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"channel": "gen'
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}
    await m.aclose()


async def test_gemini_roundtrip_preserves_thought_signatures():
    part = {
        "functionCall": {"name": "slack_send_message", "args": {"channel": "general"}},
        "thoughtSignature": "sig==",
    }
    resp = {
        "candidates": [{"content": {"role": "model", "parts": [part]}}],
        "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4},
    }
    seen, transport = _capture(resp)
    m = GeminiAdapter("gemini-test", api_key="k", transport=transport)
    turn = await m.generate("sys", [Message(role="user", text="hi")], TOOLS)
    assert turn.tool_calls[0].name == "slack_send_message" and turn.tool_calls[0].id == "gemini-0"
    assert seen["url"].endswith("models/gemini-test:generateContent")
    assert seen["headers"]["x-goog-api-key"] == "k"

    body = m.build_request("sys", _history(turn), TOOLS)
    assert body["contents"][1]["parts"][0]["thoughtSignature"] == "sig=="
    fr = body["contents"][2]["parts"][0]["functionResponse"]
    assert fr == {"name": "slack_send_message", "response": {"ok": True}}
    await m.aclose()


def test_gemini_schema_sanitizer():
    out = sanitize_schema(TOOLS[0].input_schema)
    assert out["type"] == "OBJECT" and "title" not in out and "additionalProperties" not in out
    assert out["properties"]["thread_ts"] == {"type": "STRING", "description": "ts", "nullable": True}


async def test_provider_errors_raise_model_error():
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "bad key"}))
    m = AnthropicAdapter("claude-test", api_key="k", transport=transport)
    with pytest.raises(ModelError, match="401"):
        await m.generate("sys", [Message(role="user", text="hi")], TOOLS)
    await m.aclose()


def test_registry(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ModelError, match="OPENAI_API_KEY"):
        make_model("openai:gpt-test")
    with pytest.raises(ValueError):
        make_model("nonsense")
    assert make_model("mock:golden").label == "mock:golden"


async def test_scripted_model_is_per_case():
    m = make_model(
        "mock:golden", {"a": {"golden": [{"call": {"tool": "t", "args": {"x": 1}}}, {"answer": "done"}]}}
    )
    a = m.for_case("a")
    first = await a.generate("", [Message(role="user", text="go")], [])
    assert first.tool_calls == [ToolCall(id="call_0_0", name="t", arguments={"x": 1})]
    msgs = [Message(role="user", text="go"), Message(role="assistant", turn=first)]
    assert (await a.generate("", msgs, [])).text == "done"
    assert (await m.for_case("missing").generate("", [], [])).tool_calls == []
