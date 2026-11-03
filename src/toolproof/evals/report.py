"""Render evaluation results as Markdown and standalone HTML."""

from __future__ import annotations

import html
import json
from typing import Any

DIMS = ["outcome", "selection", "args", "safety", "efficiency"]


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def _num(v: float | None, digits: int = 2) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}"


def to_markdown(results: dict[str, Any]) -> str:
    meta = results["meta"]
    lines = [
        "# toolproof eval report",
        "",
        f"Conditions: {', '.join(meta['conditions'])} | trials per cell: {meta['trials']} | "
        f"cases: {len(meta['cases'])} | unsafe write retries: {meta['unsafe_retry_writes']}",
        "",
        "## Leaderboard",
        "",
        "| Model | Composite | Clean pass | Stress pass | Resilience | Blast radius | Duplicate posts |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for m in results["models"]:
        lines.append(
            f"| `{m['model']}` | {_num(m['overall_composite'])} | {_pct(m['clean_pass_rate'])} | "
            f"{_pct(m['stress_pass_rate'])} | {_num(m['resilience_index'])} | {_num(m['blast_radius'])} | "
            f"{m['duplicate_posts']} |"
        )
    lines += [
        "",
        "## By condition",
        "",
        "| Model | Condition | pass@1 | pass^k | "
        + " | ".join(DIMS)
        + " | invalid calls | avg calls | infra errors |",
        "|---|---|" + "---:|" * (len(DIMS) + 6),
    ]
    for s in results["summary"]:
        if not s.get("valid_trials"):
            lines.append(
                f"| `{s['model']}` | {s['condition']} | - | - |"
                + " - |" * len(DIMS)
                + f" - | - | {s.get('infra_errors', 0)} |"
            )
            continue
        dims = " | ".join(_num(s[f"mean_{d}"]) for d in DIMS)
        lines.append(
            f"| `{s['model']}` | {s['condition']} | {_pct(s['pass_rate'])} | {_pct(s['pass_hat_k'])} | {dims} | "
            f"{_pct(s['invalid_call_rate'])} | {s['mean_calls']} | {s['infra_errors']} |"
        )
    failures = [t for t in results["trials"] if not t["passed"]]
    if failures:
        lines += ["", "## Failures", ""]
        for t in failures[:50]:
            why = t["infra_error"] or "; ".join(
                f"{c['name']}" + (f" ({c['detail']})" if c["detail"] else "")
                for c in t["checks"]
                if not c["ok"]
            )
            lines.append(f"- `{t['model']}` · **{t['case_id']}** · {t['condition']} #{t['trial']}: {why}")
        if len(failures) > 50:
            lines.append(f"- ...and {len(failures) - 50} more (see results.json)")
    lines += [
        "",
        "_Resilience = stress pass rate / clean pass rate. Blast radius = mean unrequested "
        "mutations per trial. pass^k = share of cases passed in every trial._",
        "",
    ]
    return "\n".join(lines)


def _cell_color(rate: float | None) -> str:
    if rate is None:
        return "var(--muted-bg)"
    hue = int(rate * 130)  # 0 = red, 130 = green
    return f"hsl({hue} 65% var(--cell-l))"


def to_html(results: dict[str, Any]) -> str:
    e = html.escape
    meta = results["meta"]
    models = [m["model"] for m in results["models"]]
    conds = meta["conditions"]
    cases = meta["cases"]

    rate: dict[tuple[str, str, str], list[bool]] = {}
    for t in results["trials"]:
        if t["infra_error"] is None:
            rate.setdefault((t["model"], t["case_id"], t["condition"]), []).append(t["passed"])

    cards = []
    for m in results["models"]:
        cards.append(f"""
        <article class="card">
          <h3>{e(m["model"])}</h3>
          <div class="kpis">
            <div><span class="k">Composite</span><span class="v">{_num(m["overall_composite"])}</span></div>
            <div><span class="k">Clean pass</span><span class="v">{_pct(m["clean_pass_rate"])}</span></div>
            <div><span class="k">Stress pass</span><span class="v">{_pct(m["stress_pass_rate"])}</span></div>
            <div><span class="k">Resilience</span><span class="v">{_num(m["resilience_index"])}</span></div>
            <div><span class="k">Blast radius</span><span class="v {"bad" if (m["blast_radius"] or 0) > 0 else ""}">{_num(m["blast_radius"])}</span></div>
            <div><span class="k">Duplicates</span><span class="v {"bad" if m["duplicate_posts"] else ""}">{m["duplicate_posts"]}</span></div>
          </div>
        </article>""")

    dim_rows = []
    for s in results["summary"]:
        if not s.get("valid_trials"):
            continue
        bars = "".join(
            f'<div class="bar" title="{d}: {s[f"mean_{d}"]:.2f}"><span class="lbl">{d}</span>'
            f'<span class="track"><span class="fill" style="width:{s[f"mean_{d}"] * 100:.1f}%"></span></span>'
            f'<span class="val">{s[f"mean_{d}"]:.2f}</span></div>'
            for d in DIMS
        )
        dim_rows.append(
            f'<div class="dimgroup"><h4>{e(s["model"])} <small>{e(s["condition"])}</small></h4>{bars}</div>'
        )

    head = "".join(f'<th colspan="{len(conds)}">{e(m)}</th>' for m in models)
    sub = "".join(f"<th>{e(c)}</th>" for _ in models for c in conds)
    body = []
    for c in cases:
        cells = []
        for m in models:
            for cond in conds:
                vals = rate.get((m, c["id"], cond))
                r = sum(vals) / len(vals) if vals else None
                label = "–" if r is None else f"{sum(vals)}/{len(vals)}"
                cells.append(f'<td class="hm" style="background:{_cell_color(r)}">{label}</td>')
        body.append(
            f'<tr><th class="case">{e(c["title"])}<small>{e(c["id"])}</small></th>{"".join(cells)}</tr>'
        )

    fails = []
    for t in [t for t in results["trials"] if not t["passed"]][:80]:
        failed = [c for c in t["checks"] if not c["ok"]]
        why = e(
            t["infra_error"]
            or "; ".join(c["name"] + (f" — {c['detail']}" if c["detail"] else "") for c in failed)
        )
        traj = e(json.dumps(t["trajectory"], indent=2, ensure_ascii=False))
        fails.append(f"""<details><summary><code>{e(t["model"])}</code> · <b>{e(t["case_id"])}</b> ·
          {e(t["condition"])} #{t["trial"]}<span class="why">{why}</span></summary>
          <p><b>Final answer:</b> {e(t["final_answer"]) or "<i>(none)</i>"}</p><pre>{traj}</pre></details>""")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>toolproof report</title>
<style>
:root{{--bg:#f7f7f5;--fg:#1c1c1a;--muted:#6b6b66;--card:#fff;--line:#e4e3de;--accent:#3b5bdb;--bad:#c92a2a;--muted-bg:#eee;--cell-l:78%}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--fg:#ecebe6;--muted:#9b9a94;--card:#1e1e1c;--line:#2e2e2b;--accent:#748ffc;--bad:#ff8787;--muted-bg:#2a2a28;--cell-l:32%}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:32px 16px 64px}}h1{{margin:0;font-size:28px}}h2{{margin:40px 0 12px;font-size:19px}}
.sub{{color:var(--muted);margin:6px 0 0}}code{{font:13px ui-monospace,SFMono-Regular,Menlo,monospace}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin-top:24px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}.card h3{{margin:0 0 12px;font:600 14px ui-monospace,monospace}}
.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.k{{display:block;color:var(--muted);font-size:12px}}.v{{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums}}.v.bad{{color:var(--bad)}}
.dims{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}}.dimgroup{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}}
.dimgroup h4{{margin:0 0 8px;font:600 13px ui-monospace,monospace}}.dimgroup small{{color:var(--muted);font-weight:400;margin-left:6px}}
.bar{{display:grid;grid-template-columns:78px 1fr 40px;align-items:center;gap:8px;font-size:12px;margin:4px 0}}.lbl{{color:var(--muted)}}
.track{{height:8px;background:var(--muted-bg);border-radius:4px;overflow:hidden}}.fill{{display:block;height:100%;background:var(--accent)}}.val{{text-align:right;font-variant-numeric:tabular-nums}}
.scroll{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}}table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:center;white-space:nowrap}}thead th{{font-weight:600}}
th.case{{text-align:left;font-weight:500;white-space:normal;min-width:220px}}th.case small{{display:block;color:var(--muted);font:11px ui-monospace,monospace}}
td.hm{{font-variant-numeric:tabular-nums;font-weight:600}}
details{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:8px 0}}summary{{cursor:pointer}}
.why{{display:block;color:var(--bad);font-size:13px;margin-top:2px}}pre{{overflow:auto;background:var(--muted-bg);padding:10px;border-radius:8px;font-size:12px;max-height:360px}}
.note{{color:var(--muted);font-size:13px;max-width:820px}}
</style></head><body><main>
<h1>toolproof eval report</h1>
<p class="sub">{len(cases)} cases · conditions: {e(", ".join(conds))} · {meta["trials"]} trial(s) per cell ·
unsafe write retries: {meta["unsafe_retry_writes"]} · {e(meta["created_at"])}</p>
<section class="cards">{"".join(cards)}</section>
<h2>Scoring dimensions</h2><div class="dims">{"".join(dim_rows)}</div>
<h2>Pass rate by case</h2>
<div class="scroll"><table><thead><tr><th></th>{head}</tr><tr><th></th>{sub}</tr></thead><tbody>{"".join(body)}</tbody></table></div>
<h2>Failures ({len([t for t in results["trials"] if not t["passed"]])})</h2>{"".join(fails) or '<p class="note">None.</p>'}
<p class="note">Outcome is graded on the twin's mutation journal, not on matching a golden trajectory. Resilience
= stress pass rate ÷ clean pass rate. Blast radius = mean unrequested mutations per trial. pass^k = share of
cases passed in every trial.</p>
</main></body></html>"""
