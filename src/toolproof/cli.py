"""toolproof command line.

toolproof serve                       # MCP server (stdio) against real Slack
toolproof serve --twin                # ...against the offline digital twin (great for demos)
toolproof auth login                  # Slack OAuth v2 with token rotation
toolproof eval --models mock:golden,anthropic:<model>,openai:<model>,gemini:<model>
toolproof export-tools --out tools.json
toolproof report results.json         # re-render Markdown/HTML from saved results
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from toolproof import __version__


def _cmd_serve(args: argparse.Namespace) -> int:
    from toolproof.server import build_live_client, build_twin_client, create_server
    from toolproof.twin import ChaosConfig, SlackTwin, World

    if args.twin:
        twin = SlackTwin(World.from_fixture(args.fixture), ChaosConfig.named(args.condition))
        client = build_twin_client(twin)
    else:
        client = build_live_client(args.token_path)
    server = create_server(client, log_level=args.log_level)
    if args.transport == "streamable-http":
        server.settings.host = args.host
        server.settings.port = args.port
    server.run(transport=args.transport)
    return 0


def _cmd_auth(args: argparse.Namespace) -> int:
    from toolproof.server import DEFAULT_TOKEN_PATH
    from toolproof.slack import OAuthConfig, TokenStore
    from toolproof.slack.auth import run_login_flow

    store = TokenStore(args.token_path or DEFAULT_TOKEN_PATH)
    tokens = asyncio.run(run_login_flow(OAuthConfig.from_env(), store, open_browser=not args.no_browser))
    rotation = "with token rotation" if tokens.refresh_token else "non-rotating token"
    print(f"Connected to {tokens.team_name or tokens.team_id} as {tokens.bot_user_id} ({rotation}).")
    print(f"Tokens saved to {store.path} (mode 0600).")
    return 0


def _write_reports(results: dict, out: Path) -> None:
    from toolproof.evals.report import to_html, to_markdown

    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    (out / "report.md").write_text(to_markdown(results))
    (out / "report.html").write_text(to_html(results))


def _cmd_eval(args: argparse.Namespace) -> int:
    from toolproof.evals.case import load_cases
    from toolproof.evals.runner import RunSettings, run_experiment

    cases = load_cases(args.cases, only=args.only.split(",") if args.only else None)
    settings = RunSettings(
        conditions=[c.strip() for c in args.conditions.split(",") if c.strip()],
        trials=args.trials,
        concurrency=args.concurrency,
        unsafe_retry_writes=args.unsafe_retry_writes,
    )
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    total = len(models) * len(cases) * len(settings.conditions) * settings.trials
    print(
        f"toolproof {__version__}: {len(models)} model(s) x {len(cases)} case(s) x "
        f"{len(settings.conditions)} condition(s) x {settings.trials} trial(s) = {total} trials",
        file=sys.stderr,
    )

    def progress(done: int, n: int, r) -> None:
        mark = "PASS" if r.passed else ("ERR " if r.infra_error else "FAIL")
        print(f"  [{done:>4}/{n}] {mark} {r.model:<28} {r.condition:<6} {r.case_id}", file=sys.stderr)

    results = asyncio.run(run_experiment(models, cases, settings, progress=None if args.quiet else progress))
    out = Path(args.out)
    _write_reports(results, out)
    from toolproof.evals.report import to_markdown

    print(to_markdown(results).split("## By condition")[0].strip())
    print(f"\nReports: {out / 'report.html'}, {out / 'report.md'}, {out / 'results.json'}")

    if args.fail_under is not None:
        worst = min((m["overall_composite"] or 0.0) for m in results["models"])
        if worst < args.fail_under:
            print(f"FAIL: composite {worst:.3f} < --fail-under {args.fail_under}", file=sys.stderr)
            return 1
    return 0


def _cmd_export_tools(args: argparse.Namespace) -> int:
    from mcp.shared.memory import create_connected_server_and_client_session

    from toolproof.server import build_twin_client, create_server
    from toolproof.twin import SlackTwin, World

    async def run() -> list[dict]:
        client = build_twin_client(SlackTwin(World.from_fixture("acme")))
        async with create_connected_server_and_client_session(create_server(client)) as session:
            tools = (await session.list_tools()).tools
        await client.aclose()
        return [t.model_dump(mode="json", exclude_none=True) for t in tools]

    data = {"tools": asyncio.run(run())}
    text = json.dumps(data, indent=2)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text)
        print(f"Wrote {len(data['tools'])} tool definitions to {args.out}", file=sys.stderr)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    results = json.loads(Path(args.results).read_text())
    out = Path(args.out or Path(args.results).parent)
    _write_reports(results, out)
    print(f"Wrote {out / 'report.html'} and {out / 'report.md'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="toolproof", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", action="version", version=f"toolproof {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="Run the Slack MCP server")
    s.add_argument("--twin", action="store_true", help="Serve the offline digital twin instead of real Slack")
    s.add_argument("--fixture", default="acme", help="Twin fixture name or YAML path")
    s.add_argument("--condition", default="clean", help="Twin fault preset: clean | chaos | storm")
    s.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "sse"])
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--token-path", default=None)
    s.add_argument("--log-level", default="WARNING")
    s.set_defaults(func=_cmd_serve)

    a = sub.add_parser("auth", help="Slack OAuth")
    a_sub = a.add_subparsers(dest="auth_command", required=True)
    login = a_sub.add_parser(
        "login", help="Authorize via browser (needs SLACK_CLIENT_ID / SLACK_CLIENT_SECRET)"
    )
    login.add_argument("--token-path", default=None)
    login.add_argument("--no-browser", action="store_true")
    login.set_defaults(func=_cmd_auth)

    e = sub.add_parser("eval", help="Run the eval suite")
    e.add_argument(
        "--models",
        default="mock:golden,mock:flawed",
        help="Comma-separated provider:model specs (anthropic:, openai:, gemini:, mock:)",
    )
    e.add_argument("--cases", default="evals/cases", help="Case file or directory")
    e.add_argument("--only", default=None, help="Comma-separated case ids or tags")
    e.add_argument("--conditions", default="clean,chaos", help="clean,chaos,storm")
    e.add_argument("--trials", type=int, default=1)
    e.add_argument("--concurrency", type=int, default=4)
    e.add_argument("--out", default="reports/latest")
    e.add_argument(
        "--unsafe-retry-writes",
        action="store_true",
        help="Make the server blindly retry ambiguous writes (to see what the eval catches)",
    )
    e.add_argument("--fail-under", type=float, default=None, help="Exit 1 if any model's composite is lower")
    e.add_argument("--quiet", action="store_true")
    e.set_defaults(func=_cmd_eval)

    x = sub.add_parser("export-tools", help="Dump the MCP tool definitions as JSON (for linting)")
    x.add_argument("--out", default="-")
    x.set_defaults(func=_cmd_export_tools)

    r = sub.add_parser("report", help="Re-render reports from results.json")
    r.add_argument("results")
    r.add_argument("--out", default=None)
    r.set_defaults(func=_cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # surface a clean one-line error for expected failures
        from toolproof.evals.models import ModelError
        from toolproof.slack import SlackError

        if isinstance(exc, (SlackError, ModelError, ValueError, FileNotFoundError)):
            print(f"toolproof: error: {exc}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
