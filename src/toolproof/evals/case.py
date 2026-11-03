"""Schema and loading helpers for YAML evaluation cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArgCritic(_Strict):
    critic: Literal["exact", "similarity", "channel", "contains", "regex", "numeric", "absent", "any"] = (
        "exact"
    )
    expected: Any = None
    weight: float = 1.0
    threshold: float = 0.75  # similarity
    tolerance: float = 0.0  # numeric
    case_sensitive: bool = False


class ExpectedCall(_Strict):
    tool: str
    args: dict[str, ArgCritic] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        # Plain values use the exact-match critic by default.
        if isinstance(data, dict) and isinstance(data.get("args"), dict):
            data = dict(data)
            data["args"] = {
                k: v if isinstance(v, dict) and ("critic" in v or "expected" in v) else {"expected": v}
                for k, v in data["args"].items()
            }
        return data


class PostExpectation(_Strict):
    channel: str
    thread_ts: str | None = None
    top_level: bool = False
    text_contains: list[str] = Field(default_factory=list)
    text_regex: str | None = None
    text_similar_to: str | None = None
    similarity: float = 0.6


class ReactionExpectation(_Strict):
    channel: str
    ts: str
    name_any: list[str]


class AnswerExpectation(_Strict):
    contains_all: list[str] = Field(default_factory=list)
    contains_any: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)


class Expectations(_Strict):
    posts: list[PostExpectation] = Field(default_factory=list)
    reactions: list[ReactionExpectation] = Field(default_factory=list)
    answer: AnswerExpectation = Field(default_factory=AnswerExpectation)
    max_unexpected: int = 0


class Case(_Strict):
    id: str
    title: str
    prompt: str
    fixture: str = "acme"
    tags: list[str] = Field(default_factory=list)
    expected_calls: list[ExpectedCall] = Field(default_factory=list)
    optimal_calls: int | None = None
    expect: Expectations = Field(default_factory=Expectations)
    max_turns: int = 12
    trajectories: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    @property
    def optimal(self) -> int:
        return self.optimal_calls or max(1, len(self.expected_calls))


def load_cases(path: str | Path, only: list[str] | None = None) -> list[Case]:
    path = Path(path)
    files = sorted(path.glob("*.y*ml")) if path.is_dir() else [path]
    cases: list[Case] = []
    for f in files:
        doc = yaml.safe_load(f.read_text())
        items = doc if isinstance(doc, list) else [doc]
        for item in items:
            case = Case.model_validate(item)
            if not only or case.id in only or any(t in only for t in case.tags):
                cases.append(case)
    ids = [c.id for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate case ids: {sorted(dupes)}")
    if not cases:
        raise ValueError(f"no cases found at {path}" + (f" matching {only}" if only else ""))
    return cases
