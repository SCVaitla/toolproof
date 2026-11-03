from __future__ import annotations

from mcp.shared.memory import create_connected_server_and_client_session

from toolproof.server import create_server
from toolproof.twin import ChaosConfig

from .conftest import make_client, tool_json


async def _session(world, chaos=None, **kw):
    twin, client = make_client(world, chaos, **kw)
    return twin, create_server(client)


async def test_tools_have_honest_annotations(world):
    _, server = await _session(world)
    async with create_connected_server_and_client_session(server) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
    assert set(tools) == {
        "slack_list_channels",
        "slack_get_messages",
        "slack_get_thread_replies",
        "slack_find_users",
        "slack_get_user",
        "slack_send_message",
        "slack_add_reaction",
    }
    assert tools["slack_get_messages"].annotations.readOnlyHint is True
    assert tools["slack_send_message"].annotations.idempotentHint is False
    assert tools["slack_add_reaction"].annotations.idempotentHint is True
    for t in tools.values():
        for prop in t.inputSchema["properties"].values():
            assert prop.get("description"), f"{t.name} has an undocumented parameter"


async def test_channel_by_name_paginates_and_enriches_names(world):
    twin, server = await _session(world, ChaosConfig(max_page_size=10))
    async with create_connected_server_and_client_session(server) as session:
        res = await session.call_tool("slack_get_messages", {"channel": "#releases", "limit": 2})
    data = tool_json(res)
    assert not res.isError
    assert data["channel"]["id"] == "C0RELEASES"
    assert data["messages"][0]["ts"] == "1730008200.000300"
    assert data["messages"][0]["user_name"] == "Priya Shah"
    assert twin.stats.by_method["conversations.list"] > 3  # the noise channels forced paging


async def test_unknown_channel_suggests_alternatives(world):
    _, server = await _session(world)
    async with create_connected_server_and_client_session(server) as session:
        res = await session.call_tool("slack_get_messages", {"channel": "release"})
    data = tool_json(res)
    assert res.isError and data["error"] == "channel_not_found"
    assert "#releases" in data["did_you_mean"]


async def test_private_channel_invisible_without_membership(world):
    _, server = await _session(world)
    async with create_connected_server_and_client_session(server) as session:
        res = await session.call_tool("slack_get_messages", {"channel": "leadership"})
    assert res.isError and tool_json(res)["error"] == "channel_not_found"


async def test_send_message_recovers_from_ambiguous_failure_without_duplicates(world):
    _, server = await _session(world, ChaosConfig(ambiguous_write_prob=1.0))
    async with create_connected_server_and_client_session(server) as session:
        res = await session.call_tool(
            "slack_send_message",
            {"channel": "releases", "text": "Verified on prod", "thread_ts": "1730008200.000300"},
        )
    data = tool_json(res)
    assert not res.isError and data["recovered"] is True
    posted = [m for m in world.mutations if m.kind == "message_posted"]
    assert len(posted) == 1 and posted[0].data["thread_ts"] == "1730008200.000300"


async def test_recovery_when_thread_ts_points_at_a_reply(world):
    """Regression: verify-then-retry used to look in the wrong thread when given a reply's ts,
    missed the landed message, and re-posted it."""
    _, server = await _session(world, ChaosConfig(ambiguous_write_prob=1.0))
    async with create_connected_server_and_client_session(server) as session:
        res = await session.call_tool(
            "slack_send_message",
            {"channel": "incidents", "text": "summary", "thread_ts": "1730002400.000300"},
        )
    assert not res.isError and tool_json(res)["recovered"] is True
    posted = [m for m in world.mutations if m.kind == "message_posted"]
    assert len(posted) == 1 and posted[0].data["thread_ts"] == "1730002000.000100"


async def test_archived_channel_is_refused_with_hint(world):
    _, server = await _session(world)
    async with create_connected_server_and_client_session(server) as session:
        res = await session.call_tool("slack_send_message", {"channel": "#design", "text": "hi"})
    data = tool_json(res)
    assert res.isError and data["error"] == "is_archived" and "hint" in data
    assert world.mutations == []


async def test_find_users_handles_ambiguity_and_email(world):
    _, server = await _session(world, ChaosConfig(max_page_size=9))
    async with create_connected_server_and_client_session(server) as session:
        both = tool_json(await session.call_tool("slack_find_users", {"query": "priya"}))
        one = tool_json(await session.call_tool("slack_find_users", {"query": "priya.kumar@acme.dev"}))
    assert {m["id"] for m in both["matches"]} == {"U01PRIYA", "U06PRIYAK"}
    assert one["matches"][0]["mention"] == "<@U06PRIYAK>"


async def test_reaction_is_idempotent(world):
    _, server = await _session(world)
    args = {"channel": "general", "timestamp": "1730010000.000100", "name": ":+1:"}
    async with create_connected_server_and_client_session(server) as session:
        first = tool_json(await session.call_tool("slack_add_reaction", args))
        second = tool_json(await session.call_tool("slack_add_reaction", args))
    assert first["ok"] and second["already_reacted"]
    assert len(world.mutations) == 1


async def test_schema_validation_errors_are_reported(world):
    _, server = await _session(world)
    async with create_connected_server_and_client_session(server) as session:
        res = await session.call_tool("slack_get_messages", {"channel": "general", "limit": 9999})
    assert res.isError


async def test_thread_replies(world):
    _, server = await _session(world, ChaosConfig(max_page_size=2))
    async with create_connected_server_and_client_session(server) as session:
        data = tool_json(
            await session.call_tool(
                "slack_get_thread_replies", {"channel": "incidents", "thread_ts": "1730002000.000100"}
            )
        )
    texts = [m["text"] for m in data["messages"]]
    assert len(texts) == 4 and "200" in texts[2]
