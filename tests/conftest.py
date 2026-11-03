from __future__ import annotations

import json

import pytest

from toolproof.clock import VirtualClock
from toolproof.server import build_twin_client
from toolproof.twin import ChaosConfig, SlackTwin, World


@pytest.fixture
def world() -> World:
    return World.from_fixture("acme")


@pytest.fixture
def clock() -> VirtualClock:
    return VirtualClock()


def make_client(world: World, chaos: ChaosConfig | None = None, clock: VirtualClock | None = None, **kw):
    twin = SlackTwin(world, chaos or ChaosConfig())
    return twin, build_twin_client(twin, clock=clock or VirtualClock(), **kw)


def tool_json(result) -> dict:
    """Parse a CallToolResult's text payload (success or error) into a dict."""
    text = "".join(getattr(c, "text", "") for c in result.content)
    start = text.find("{")
    return json.loads(text[start:]) if start >= 0 else {"raw": text}
