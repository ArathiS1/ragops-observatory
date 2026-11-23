"""Native PDF export of the reliability report (ReportLab, no browser needed).

    GET /v1/projects/{p}/report?format=pdf      ragops report <project> --format pdf --out report.pdf
"""

from __future__ import annotations

import io

STAGE_COLORS = {
    "knowledge_gap": "#2a78d6", "stale_knowledge": "#eb6834", "chunking_defect": "#1baf7a",
    "retrieval_miss": "#eda100", "ranking_failure": "#e87ba4", "context_overload": "#008300",
    "generation_failure": "#6250d6",
}
INK, MUTED, LINE = "#1a2230", "#5b6577", "#d9dee6"
LEVEL = {"critical": ("#b42f25", "Needs attention now"), "warning": ("#9a6200", "Watch"),
         "healthy": ("#1f7a46", "Healthy"), "info": ("#2a5bd7", "Getting started")}


def _nice(s: str) -> str:
    return s.replace("_", " ").capitalize()


def to_pdf(r: dict) -> bytes:
    from reportlab.graphics.charts.barcharts import HorizontalBarChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    c = colors.HexColor
    base = ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=c(INK), alignment=TA_LEFT)
    small = ParagraphStyle("s", parent=base, fontSize=8.5, leading=11.5, textColor=c(MUTED))
    h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=12, leading=15, spaceBefore=12,
                        spaceAfter=6)
    esc = lambda s: (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    pct = lambda x: "n/a" if x is None else f"{x:.1%}"  # noqa: E731

    def table(rows, widths, header=True):
        t = Table([[Paragraph(esc(x) if not isinstance(x, Paragraph) else x, base) if not isinstance(x, Paragraph)
                    else x for x in row] for row in rows], colWidths=widths, hAlign="LEFT")
        style = [("LINEBELOW", (0, 0), (-1, -1), 0.4, c(LINE)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                 ("LEFTPADDING", (0, 0), (-1, -1), 2)]
        if header:
            style += [("TEXTCOLOR", (0, 0), (-1, 0), c(MUTED)), ("LINEBELOW", (0, 0), (-1, 0), 0.8, c(MUTED))]
        t.setStyle(TableStyle(style))
        return t

    ov, rc = r["overview"], r["root_causes"]
    story = [Paragraph(f"RAG reliability report: {esc(r['project'])}", h1),
             Paragraph(f"Last {r['days']} days, generated {esc(r['generated_at'])} UTC", small), Spacer(1, 8)]
    col, word = LEVEL.get(ov["health"]["level"], LEVEL["info"])
    status = Table([[Paragraph(f"<b><font color='{col}'>{word}.</font></b> {esc(ov['health']['message'])}", base)]],
                   colWidths=[170 * mm])
    status.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, c(col)), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [status, Paragraph("Key numbers", h2)]
    rows = [["Measure", "Value"], ["Requests", f"{ov['traces']:,}"],
            ["Evaluated (sampled within budget)", f"{ov['evaluated']:,} ({pct(ov['eval_coverage'])})"],
            ["Failure rate", pct(ov["failure_rate"])],
            ["Latency p50 / p95", f"{ov['latency_p50_ms'] or 0:.0f} / {ov['latency_p95_ms'] or 0:.0f} ms"],
            ["LLM spend / judge spend", f"${ov['llm_cost_usd']:.2f} / ${ov['judge_cost_usd']:.2f}"]]
    rows += [[f"{_nice(m)} (mean)", "n/a" if v is None else f"{v:.3f}"] for m, v in sorted(ov["metrics"].items())]
    story.append(table(rows, [95 * mm, 75 * mm]))

    story.append(Paragraph("Where failures come from", h2))
    bd = rc["breakdown"]
    if bd:
        d = Drawing(170 * mm, 10 + 16 * len(bd))
        ch = HorizontalBarChart()
        ch.x, ch.y, ch.width, ch.height = 110, 5, 170 * mm - 150, 16 * len(bd)
        ch.data = [[b["count"] for b in reversed(bd)]]
        ch.categoryAxis.categoryNames = [_nice(b["label"]) for b in reversed(bd)]
        ch.categoryAxis.labels.fontName, ch.categoryAxis.labels.fontSize = "Helvetica", 8.5
        ch.categoryAxis.labels.textAnchor, ch.categoryAxis.labels.dx = "end", -4
        ch.categoryAxis.strokeColor = c(LINE)
        ch.valueAxis.valueMin, ch.valueAxis.visible = 0, False
        ch.bars.strokeWidth, ch.bars.strokeColor, ch.barSpacing, ch.groupSpacing = 0, None, 2, 4
        for i, b in enumerate(reversed(bd)):
            ch.bars[(0, i)].fillColor = c(STAGE_COLORS.get(b["label"], MUTED))
        ch.barLabelFormat, ch.barLabels.fontSize, ch.barLabels.nudge = "%d", 8, 8
        ch.barLabels.fontName = "Helvetica"
        d.add(ch)
        story += [d, Spacer(1, 6)]
        story.append(table([["Stage", "Failures", "Share", "What to do"]] +
                           [[_nice(b["label"]), b["count"], f"{b['share']:.0%}", b["fix"]] for b in bd],
                           [32 * mm, 18 * mm, 14 * mm, 106 * mm]))
    else:
        story.append(Paragraph("No diagnosed failures in this window.", base))

    story.append(Paragraph("Quality changes", h2))
    for q in r["quality_changes"]:
        cause = f" Likely cause: {esc('; '.join(q['likely_causes']))}." if q.get("likely_causes") else ""
        story.append(Paragraph(f"• {esc(q['summary'])}.{cause}", base))

    slos = ov.get("slos") or []
    if slos:
        story.append(Paragraph("Service-level objectives", h2))
        story.append(table([["SLO", "Target", "Actual", "Budget left", "Status"]] +
                           [[s["name"], f"{_nice(s['metric'])} ≥ {s['threshold']} on {s['target']:.0%}",
                             pct(s["good_ratio"]), pct(s["budget_remaining"]), s["status"].replace("_", " ")]
                            for s in slos], [45 * mm, 52 * mm, 22 * mm, 25 * mm, 26 * mm]))

    gaps = (r.get("kb") or {}).get("coverage_gaps") or []
    story.append(Paragraph("Content to write", h2))
    if gaps:
        for g in gaps[:6]:
            story.append(KeepTogether([Paragraph(f"<b>{esc(g['topic'])}</b> ({g['query_count']} unanswered "
                                                 f"questions). {esc(g['suggestion'])}", base), Spacer(1, 3)]))
    else:
        story.append(Paragraph("No coverage gaps: every recent question had a matching document.", base))
    qd = r.get("query_drift") or {}
    if qd.get("summary"):
        story.append(Paragraph(f"Question drift: {esc(qd['summary'])}", small))

    if r.get("alerts"):
        story.append(Paragraph("Alerts", h2))
        for a in r["alerts"][:8]:
            story.append(Paragraph(f"• [{esc(a['severity'])}] {esc(a['title'])} ({esc(a['at'][:16])})", base))
    if r.get("golden_sets"):
        story.append(Paragraph("Regression tests", h2))
        story.append(Paragraph(", ".join(f"{esc(g['name'])} (version {g['version']}, {g['cases']} cases)"
                                         for g in r["golden_sets"]), base))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(c(MUTED))
        canvas.drawString(20 * mm, 10 * mm, f"RAGOps Observatory · {r['project']}")
        canvas.drawRightString(190 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                      bottomMargin=18 * mm, title=f"RAG reliability report: {r['project']}",
                      author="RAGOps Observatory").build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
