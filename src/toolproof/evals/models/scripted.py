"""Deterministic scripted "models" that replay trajectories written in the case files.

``mock:golden`` replays the reference solution (it should score 1.0 everywhere, which validates
the grader itself), and ``mock:<name>`` replays any other named trajectory, e.g. ``mock:flawed``,
which encodes realistic mistakes. They make the whole harness runnable offline and in CI.
"""

from __future__ import annotations

from typing import Any

from toolproof.evals.models.base import AssistantTurn, Message, ModelAdapter, ToolCall, ToolSpec


class ScriptedModel(ModelAdapter):
    provider = "mock"

    def __init__(
        self,
        model: str,
        trajectories: dict[str, dict[str, list[dict[str, Any]]]],
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(model)
        self._trajectories = trajectories
        self._steps = steps or []

    def for_case(self, case_id: str) -> ScriptedModel:
        by_name = self._trajectories.get(case_id, {})
        steps = by_name.get(self.model) or by_name.get("golden") or []
        return ScriptedModel(self.model, self._trajectories, steps)

    async def generate(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> AssistantTurn:
        index = sum(1 for m in messages if m.role == "assistant")
        if index >= len(self._steps):
            return AssistantTurn(text="(no further steps)")
        step = self._steps[index]
        if "answer" in step:
            return AssistantTurn(text=str(step["answer"]))
        raw_calls = step.get("calls") or [step["call"]]
        calls = [
            ToolCall(id=f"call_{index}_{j}", name=c["tool"], arguments=dict(c.get("args") or {}))
            for j, c in enumerate(raw_calls)
        ]
        return AssistantTurn(text=str(step.get("say", "")), tool_calls=calls)
