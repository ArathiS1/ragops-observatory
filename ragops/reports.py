"""Weekly reliability report: Markdown (for Slack/Confluence/PRs) and print-ready HTML (save as PDF from a browser)."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ragops import services as svc
from ragops.models import Alert, GoldenSet, Project


def build_report(db: Session, project: Project, days: int = 7) -> dict:
    ov = svc.overview(db, project, days)
    rc = svc.root_cause_detail(db, project, days)
    qd = svc.quality_drift(db, project, max(days * 2, 14))
    kb = svc.kb_health(db, project, max(days, 14))
    try:
        qdrift = svc.query_drift(db, project, max(1, days // 2), days)
    except Exception:  # pragma: no cover
        qdrift = {"summary": "query drift unavailable"}
    alerts = db.scalars(select(Alert).where(Alert.project_id == project.id, Alert.created_at >= svc._since(days))
                        .order_by(Alert.created_at.desc())).all()
    golden = db.scalars(select(GoldenSet).where(GoldenSet.project_id == project.id)).all()
    return {"project": project.name, "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"), "days": days,
            "overview": ov, "root_causes": rc, "quality_changes": qd, "kb": kb, "query_drift": qdrift,
            "alerts": [{"title": a.title, "severity": a.severity, "at": a.created_at.isoformat()} for a in alerts],
            "golden_sets": [{"name": g.name, "version": g.version, "cases": len(g.cases)} for g in golden]}


def to_markdown(r: dict) -> str:
    ov = r["overview"]
    pct = lambda x: "n/a" if x is None else f"{x:.1%}"  # noqa: E731
    num = lambda x: "n/a" if x is None else f"{x:.3f}"  # noqa: E731
    lines = [f"# RAG reliability report: {r['project']}", f"_Last {r['days']} days · generated {r['generated_at']} UTC_", "",
             f"**Status: {ov['health']['level'].upper()}**: {ov['health']['message']}", "", "## Key numbers", "",
             "| | |", "|---|---|",
             f"| Requests | {ov['traces']} |", f"| Evaluated (sampled) | {ov['evaluated']} ({pct(ov['eval_coverage'])}) |",
             f"| Failure rate | {pct(ov['failure_rate'])} |",
             f"| Latency p50 / p95 | {ov['latency_p50_ms'] or 0:.0f} / {ov['latency_p95_ms'] or 0:.0f} ms |",
             f"| LLM spend / judge spend | ${ov['llm_cost_usd']:.2f} / ${ov['judge_cost_usd']:.2f} |"]
    for m, v in sorted(ov["metrics"].items()):
        lines.append(f"| {m.replace('_', ' ')} (mean) | {num(v)} |")
    lines += ["", "## Where failures come from", ""]
    if r["root_causes"]["breakdown"]:
        lines += ["| Stage | Failures | Share | What to do |", "|---|---|---|---|"]
        for b in r["root_causes"]["breakdown"]:
            lines.append(f"| {b['label'].replace('_', ' ')} | {b['count']} | {b['share']:.0%} | {b['fix']} |")
    else:
        lines.append("No diagnosed failures.")
    lines += ["", "## Quality changes", ""]
    for q in r["quality_changes"]:
        lines.append(f"- {q['summary']}" + (f" (likely cause: {'; '.join(q['likely_causes'])})" if q.get("likely_causes") else ""))
    lines += ["", "## User behaviour", "", f"- {r['query_drift'].get('summary', '')}"]
    kb = r["kb"]
    lines += ["", "## Knowledge base", "", f"Health score **{kb['health_score']:.2f}** across {kb['total_chunks']} chunks."]
    for g in kb["coverage_gaps"][:5]:
        lines.append(f"- Gap: **{g['topic']}** ({g['query_count']} queries): {g['suggestion']}")
    if kb["hot_chunks"]:
        lines.append(f"- {len(kb['hot_chunks'])} chunks appear repeatedly in failed answers (top: `{kb['hot_chunks'][0]['chunk_id']}`)")
    if kb["contradictions"]:
        lines.append(f"- {len(kb['contradictions'])} pairs of chunks state conflicting figures")
    if kb["never_retrieved_share"]:
        lines.append(f"- {kb['never_retrieved_share']:.0%} of chunks were never retrieved")
    lines += ["", "## SLOs", ""]
    for s in ov["slos"]:
        gr = "n/a" if s["good_ratio"] is None else f"{s['good_ratio']:.1%}"
        lines.append(f"- **{s['name']}**: {s['status']}: {gr} good vs target {s['target']:.0%}")
    if not ov["slos"]:
        lines.append("- No SLOs defined.")
    if r["alerts"]:
        lines += ["", "## Alerts", ""] + [f"- {a['at'][:16]} [{a['severity']}] {a['title']}" for a in r["alerts"][:10]]
    if r["golden_sets"]:
        lines += ["", "## Regression suite", ""] + [f"- `{g['name']}` v{g['version']}: {g['cases']} cases"
                                                     for g in r["golden_sets"]]
    return "\n".join(lines) + "\n"


_BOLD = re.compile(r"[*]{2}(.+?)[*]{2}")


def to_html(r: dict) -> str:
    md = to_markdown(r)
    body = []
    in_table = False
    for line in md.splitlines():
        esc = html.escape(line)
        import re

        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
        esc = re.sub(r"`(.+?)`", r"<code>\1</code>", esc)
        esc = re.sub(r"^_(.+)_$", r"<p class=muted>\1</p>", esc)
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= {"-", ""}:
                continue
            if not in_table:
                body.append("<table>")
                in_table = True
            body.append("<tr>" + "".join("<td>" + _BOLD.sub(r"<b>\1</b>", html.escape(c)) + "</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            body.append("</table>")
            in_table = False
        if line.startswith("# "):
            body.append(f"<h1>{esc[2:]}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{esc[3:]}</h2>")
        elif line.startswith("- "):
            body.append(f"<li>{esc[2:]}</li>")
        elif line.strip():
            body.append(esc if esc.startswith("<p") else f"<p>{esc}</p>")
    if in_table:
        body.append("</table>")
    css = ("body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:860px;margin:32px auto;color:#1f2328;padding:0 16px}"
           "h1{font-size:24px;margin-bottom:0}h2{font-size:17px;border-bottom:1px solid #d0d7de;padding-bottom:4px;margin-top:28px}"
           "table{border-collapse:collapse;width:100%;margin:8px 0}td{border:1px solid #d0d7de;padding:5px 8px;vertical-align:top}"
           "tr:first-child td{background:#f6f8fa;font-weight:600}.muted{color:#656d76;margin-top:2px}code{background:#f6f8fa;padding:1px 4px;border-radius:4px}"
           "li{margin:3px 0}@media print{body{margin:0}}")
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>RAG reliability report: {html.escape(r['project'])}</title><style>{css}</style></head><body>{''.join(body)}</body></html>"
