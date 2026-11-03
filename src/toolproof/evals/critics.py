"""Argument critics: score one tool-call argument in [0, 1].

Inspired by Arcade's evals (BinaryCritic, SimilarityCritic, NumericCritic), plus domain critics
such as ``channel``, which treats ``#releases``, ``releases`` and ``C0RELEASES`` as the same value
by resolving them against the twin, so models aren't penalised for valid alternative spellings.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from toolproof.evals.case import ArgCritic
from toolproof.twin.world import World

_MISSING = object()


def _norm(value: Any, case_sensitive: bool) -> str:
    s = str(value).strip()
    return s if case_sensitive else s.lower()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def score_arg(critic: ArgCritic, actual: Any, world: World) -> float:
    present = actual is not _MISSING and actual is not None
    kind = critic.critic
    if kind == "absent":
        return 0.0 if present else 1.0
    if not present:
        return 0.0
    if kind == "any":
        return 1.0
    if kind == "exact":
        return float(_norm(actual, critic.case_sensitive) == _norm(critic.expected, critic.case_sensitive))
    if kind == "similarity":
        return 1.0 if similarity(str(actual), str(critic.expected)) >= critic.threshold else 0.0
    if kind == "contains":
        needles = critic.expected if isinstance(critic.expected, list) else [critic.expected]
        hay = _norm(actual, critic.case_sensitive)
        return sum(_norm(n, critic.case_sensitive) in hay for n in needles) / max(1, len(needles))
    if kind == "regex":
        flags = 0 if critic.case_sensitive else re.IGNORECASE
        return float(re.search(str(critic.expected), str(actual), flags) is not None)
    if kind == "numeric":
        try:
            diff = abs(float(actual) - float(critic.expected))
        except (TypeError, ValueError):
            return 0.0
        if critic.tolerance <= 0:
            return float(diff == 0)
        return max(0.0, 1.0 - diff / critic.tolerance)
    if kind == "channel":
        want = world.resolve_channel(str(critic.expected).lstrip("#").lower()) or world.resolve_channel(
            str(critic.expected)
        )
        got_ref = str(actual).strip()
        got = world.resolve_channel(got_ref) or world.resolve_channel(got_ref.lstrip("#").lower())
        return float(want is not None and got is not None and want.id == got.id)
    raise ValueError(f"unknown critic {kind!r}")


def score_call(expected_args: dict[str, ArgCritic], actual_args: dict[str, Any], world: World) -> float:
    if not expected_args:
        return 1.0
    total = sum(c.weight for c in expected_args.values()) or 1.0
    got = sum(
        c.weight * score_arg(c, actual_args.get(name, _MISSING), world) for name, c in expected_args.items()
    )
    return got / total
