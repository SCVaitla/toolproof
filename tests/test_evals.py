from __future__ import annotations

from pathlib import Path

import pytest

from toolproof.cli import main
from toolproof.evals.agent import CallRecord, Transcript
from toolproof.evals.case import ArgCritic, Case, load_cases
from toolproof.evals.critics import score_arg
from toolproof.evals.grading import grade
from toolproof.evals.report import to_html, to_markdown
from toolproof.evals.runner import RunSettings, run_experiment

CASES_DIR = Path(__file__).resolve().parent.parent / "evals" / "cases"


@pytest.fixture(scope="module")
def cases():
    return load_cases(CASES_DIR)


def test_critics(world):
    assert score_arg(ArgCritic(critic="channel", expected="releases"), "#releases", world) == 1
    assert score_arg(ArgCritic(critic="channel", expected="releases"), "C0RELEASES", world) == 1
    assert score_arg(ArgCritic(critic="channel", expected="releases"), "general", world) == 0
    assert (
        score_arg(ArgCritic(critic="similarity", expected="all green", threshold=0.8), "All green", world)
        == 1
    )
    assert score_arg(ArgCritic(critic="numeric", expected=10, tolerance=10), 15, world) == 0.5
    assert score_arg(ArgCritic(critic="contains", expected=["a", "z"]), "abc", world) == 0.5
    from toolproof.evals.critics import _MISSING

    assert score_arg(ArgCritic(critic="absent"), _MISSING, world) == 1


def test_case_shorthand_args():
    case = Case.model_validate(
        {
            "id": "x",
            "title": "x",
            "prompt": "p",
            "expected_calls": [{"tool": "t", "args": {"channel": "general"}}],
        }
    )
    assert case.expected_calls[0].args["channel"].expected == "general"


def test_grade_counts_blast_radius_and_duplicates(world, cases):
    case = next(c for c in cases if c.id == "oncall-handoff")
    world.post_message("C0ONCALL", world.bot_user_id, "Handoff: INC-482 still open, owner Lena.", None)
    world.post_message("C0ONCALL", world.bot_user_id, "Handoff: INC-482 still open, owner Lena.", None)
    t = Transcript(
        calls=[
            CallRecord(
                1,
                "slack_send_message",
                {"channel": "oncall", "text": "Handoff: INC-482 still open, owner Lena."},
                False,
                None,
                "{}",
            )
        ]
        * 2,
        final_answer="done",
    )
    g = grade(case, world, t)
    assert g.outcome == 1.0 and not g.passed
    assert g.unexpected_mutations == 1 and g.duplicate_posts == 1 and g.safety == 0.5
    assert g.efficiency == 0.5


def test_hallucinated_tool_hurts_selection(world, cases):
    case = next(c for c in cases if c.id == "release-latest-from-priya")
    t = Transcript(
        calls=[CallRecord(1, "slack_search", {}, True, "unknown_tool", "{}")],
        final_answer="4.2 at 1730008200.000300",
    )
    g = grade(case, world, t)
    assert g.passed and g.selection == 0.0 and g.invalid_calls == 1


async def test_golden_is_perfect_and_flawed_is_caught(cases):
    res = await run_experiment(["mock:golden", "mock:flawed"], cases, RunSettings(trials=2))
    by = {m["model"]: m for m in res["models"]}
    assert by["mock:golden"]["clean_pass_rate"] == 1.0
    assert by["mock:golden"]["stress_pass_rate"] == 1.0
    assert by["mock:golden"]["blast_radius"] == 0
    assert by["mock:flawed"]["clean_pass_rate"] < 0.5
    assert by["mock:flawed"]["blast_radius"] > 0
    flawed_alt = [
        t for t in res["trials"] if t["model"] == "mock:flawed" and t["case_id"] == "incident-summary-reply"
    ]
    assert all(t["passed"] for t in flawed_alt)  # valid alternative path accepted...
    assert all(t["scores"]["args"] < 1 for t in flawed_alt)  # ...but its odd thread_ts is noted
    assert "Leaderboard" in to_markdown(res) and "<table>" in to_html(res)


async def test_unsafe_server_retries_are_caught_as_duplicates(cases):
    writes = [c for c in cases if "write" in c.tags]
    res = await run_experiment(["mock:golden"], writes, RunSettings(trials=3, unsafe_retry_writes=True))
    golden = res["models"][0]
    assert golden["clean_pass_rate"] == 1.0
    assert golden["duplicate_posts"] > 0 and golden["resilience_index"] < 1.0


def test_cli_eval_end_to_end(tmp_path, capsys):
    code = main(
        [
            "eval",
            "--cases",
            str(CASES_DIR),
            "--only",
            "read",
            "--models",
            "mock:golden",
            "--out",
            str(tmp_path),
            "--quiet",
            "--fail-under",
            "0.99",
        ]
    )
    assert code == 0
    assert (tmp_path / "report.html").exists() and (tmp_path / "results.json").exists()
    assert main(["report", str(tmp_path / "results.json")]) == 0


def test_cli_fail_under(tmp_path):
    code = main(
        [
            "eval",
            "--cases",
            str(CASES_DIR),
            "--models",
            "mock:flawed",
            "--conditions",
            "clean",
            "--out",
            str(tmp_path),
            "--quiet",
            "--fail-under",
            "0.9",
        ]
    )
    assert code == 1


def test_cli_export_tools(capsys):
    assert main(["export-tools"]) == 0
    assert '"slack_send_message"' in capsys.readouterr().out
