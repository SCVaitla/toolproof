"""Execute evaluation cases across models, conditions, and trials."""

from __future__ import annotations

import asyncio
import time
import traceback
import zlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session

from toolproof import __version__
from toolproof.clock import VirtualClock
from toolproof.evals.agent import Transcript, run_agent
from toolproof.evals.case import Case
from toolproof.evals.grading import grade
from toolproof.evals.models import ModelAdapter, ModelError, make_model
from toolproof.server import build_twin_client, create_server
from toolproof.twin import ChaosConfig, SlackTwin, World


@dataclass
class RunSettings:
    conditions: list[str] = field(default_factory=lambda: ["clean", "chaos"])
    trials: int = 1
    concurrency: int = 4
    unsafe_retry_writes: bool = False


@dataclass
class TrialResult:
    model: str
    case_id: str
    condition: str
    trial: int
    seed: int
    passed: bool
    scores: dict[str, float]
    checks: list[dict[str, Any]]
    unexpected_mutations: int
    duplicate_posts: int
    calls: int
    invalid_calls: int
    turns: int
    virtual_wait_s: float
    http_requests: int
    faults_injected: int
    input_tokens: int
    output_tokens: int
    wall_s: float
    final_answer: str
    trajectory: list[dict[str, Any]]
    infra_error: str | None = None


def trial_seed(case_id: str, condition: str, trial: int) -> int:
    return zlib.crc32(f"{case_id}|{condition}|{trial}".encode())


async def run_trial(
    model: ModelAdapter, case: Case, condition: str, trial: int, settings: RunSettings
) -> TrialResult:
    seed = trial_seed(case.id, condition, trial)
    world = World.from_fixture(case.fixture)
    twin = SlackTwin(world, ChaosConfig.named(condition, seed))
    clock = VirtualClock()
    client = build_twin_client(twin, clock=clock, seed=seed, unsafe_retry_writes=settings.unsafe_retry_writes)
    server = create_server(client)
    started = time.perf_counter()
    transcript = Transcript()
    infra_error: str | None = None
    try:
        async with create_connected_server_and_client_session(server) as session:
            transcript = await run_agent(
                model.for_case(case.id), session, case.prompt, max_turns=case.max_turns
            )
    except ModelError as e:
        infra_error = str(e)
    except Exception as e:  # harness bug or provider oddity: record, don't kill the whole run
        infra_error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
    finally:
        await client.aclose()

    g = grade(case, world, transcript)
    tw = twin.stats
    return TrialResult(
        model=model.label,
        case_id=case.id,
        condition=condition,
        trial=trial,
        seed=seed,
        passed=g.passed and infra_error is None,
        scores={
            k: getattr(g, k) for k in ("outcome", "selection", "args", "safety", "efficiency", "composite")
        },
        checks=[asdict(c) for c in g.checks],
        unexpected_mutations=g.unexpected_mutations,
        duplicate_posts=g.duplicate_posts,
        calls=len(transcript.calls),
        invalid_calls=g.invalid_calls,
        turns=transcript.turns,
        virtual_wait_s=round(clock.slept, 2),
        http_requests=tw.requests,
        faults_injected=tw.faults_rate_limit + tw.faults_server_error + tw.faults_ambiguous_write,
        input_tokens=transcript.usage.input_tokens,
        output_tokens=transcript.usage.output_tokens,
        wall_s=round(time.perf_counter() - started, 3),
        final_answer=transcript.final_answer[:2000],
        trajectory=[
            {
                "turn": c.turn,
                "tool": c.tool,
                "args": c.args,
                "is_error": c.is_error,
                "error_kind": c.error_kind,
                "result": c.result[:600],
            }
            for c in transcript.calls
        ],
        infra_error=infra_error,
    )


async def run_experiment(
    model_specs: list[str], cases: list[Case], settings: RunSettings, *, progress=None
) -> dict[str, Any]:
    trajectories = {c.id: c.trajectories for c in cases}
    models = [make_model(spec, trajectories) for spec in model_specs]
    sem = asyncio.Semaphore(max(1, settings.concurrency))
    jobs = [
        (m, c, cond, t)
        for m in models
        for c in cases
        for cond in settings.conditions
        for t in range(settings.trials)
    ]
    done = 0

    async def one(m: ModelAdapter, c: Case, cond: str, t: int) -> TrialResult:
        nonlocal done
        async with sem:
            r = await run_trial(m, c, cond, t, settings)
        done += 1
        if progress:
            progress(done, len(jobs), r)
        return r

    try:
        results = await asyncio.gather(*(one(*j) for j in jobs))
    finally:
        for m in models:
            await m.aclose()
    return build_results(list(results), cases, settings, [m.label for m in models])


def _agg(rows: list[TrialResult], cases: list[Case]) -> dict[str, Any]:
    valid = [r for r in rows if r.infra_error is None]
    if not valid:
        return {"trials": len(rows), "valid_trials": 0, "infra_errors": len(rows)}
    by_case: dict[str, list[bool]] = {}
    for r in valid:
        by_case.setdefault(r.case_id, []).append(r.passed)
    calls = sum(r.calls for r in valid)
    return {
        "trials": len(rows),
        "valid_trials": len(valid),
        "infra_errors": len(rows) - len(valid),
        "pass_rate": round(float(mean(r.passed for r in valid)), 4),
        "pass_hat_k": round(float(mean(all(v) for v in by_case.values())), 4),
        **{
            f"mean_{k}": round(mean(r.scores[k] for r in valid), 4)
            for k in ("composite", "outcome", "selection", "args", "safety", "efficiency")
        },
        "blast_radius": round(float(mean(r.unexpected_mutations for r in valid)), 4),
        "duplicate_posts": sum(r.duplicate_posts for r in valid),
        "invalid_call_rate": round(sum(r.invalid_calls for r in valid) / calls, 4) if calls else 0.0,
        "mean_calls": round(mean(r.calls for r in valid), 2),
        "mean_virtual_wait_s": round(mean(r.virtual_wait_s for r in valid), 2),
        "faults_injected": sum(r.faults_injected for r in valid),
        "input_tokens": sum(r.input_tokens for r in valid),
        "output_tokens": sum(r.output_tokens for r in valid),
    }


def build_results(
    results: list[TrialResult], cases: list[Case], settings: RunSettings, model_labels: list[str]
) -> dict[str, Any]:
    summary = []
    for model in model_labels:
        for cond in settings.conditions:
            rows = [r for r in results if r.model == model and r.condition == cond]
            summary.append({"model": model, "condition": cond, **_agg(rows, cases)})

    models = []
    for model in model_labels:
        rows = {s["condition"]: s for s in summary if s["model"] == model}
        clean = rows.get("clean", {}).get("pass_rate")
        stressed = [rows[c] for c in settings.conditions if c != "clean" and "pass_rate" in rows.get(c, {})]
        chaos_rate = stressed[0]["pass_rate"] if stressed else None
        resilience = None
        if clean and chaos_rate is not None:
            resilience = round(min(1.0, chaos_rate / clean), 4)
        all_rows = [r for r in results if r.model == model and r.infra_error is None]
        models.append(
            {
                "model": model,
                "clean_pass_rate": clean,
                "stress_pass_rate": chaos_rate,
                "resilience_index": resilience,
                "overall_composite": round(mean(r.scores["composite"] for r in all_rows), 4)
                if all_rows
                else None,
                "blast_radius": round(float(mean(r.unexpected_mutations for r in all_rows)), 4)
                if all_rows
                else None,
                "duplicate_posts": sum(r.duplicate_posts for r in all_rows),
            }
        )
    models.sort(key=lambda m: m["overall_composite"] or 0, reverse=True)

    return {
        "meta": {
            "tool": "toolproof",
            "version": __version__,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "conditions": settings.conditions,
            "trials": settings.trials,
            "unsafe_retry_writes": settings.unsafe_retry_writes,
            "cases": [{"id": c.id, "title": c.title, "tags": c.tags} for c in cases],
        },
        "models": models,
        "summary": summary,
        "trials": [asdict(r) for r in results],
    }
