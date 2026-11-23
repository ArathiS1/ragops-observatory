"""Assurance report: turn a test run into a verdict and evidence a business owner, client or auditor can act on.

Input: the rows of a test run (``ragops.runs.run_cases``) plus what was tested. Output: a dict that renders to a
self-contained HTML page and a JSON evidence file. The report states

- a verdict, **Ready / Ready with conditions / Not ready**, from explicit criteria (each shown with its number);
- what fails, in plain language, with the fix and example questions;
- which documents to write (questions nothing covers) and which answers came from outdated documents;
- how far to trust the judge (LLM or word matching, scores settled by rules, fallbacks);
- how the evidence relates to RBI FREE-AI, the EU AI Act and ISO/IEC 42001 (supporting evidence, not certification);
- a SHA-256 fingerprint of the inputs and results, so a changed report can be detected.

The verdict is an assessment of the tested questions only; the limitations section says so.
"""

from __future__ import annotations

import hashlib
import html
import json
import uuid
from datetime import datetime, timezone

from ragops.textutil import is_abstention

PLAIN = {
    "security_leak": ("The assistant followed an injected instruction or exposed contacts or keys.",
                      "Never put secrets in documents or prompts; tell the model to ignore instructions inside "
                      "questions and documents; add the grounding guard."),
    "knowledge_gap": ("No document covers the question.", "Write the missing document, or keep the assistant declining."),
    "stale_knowledge": ("The answer came from an outdated document.", "Update or remove the old version, then re-index."),
    "chunking_defect": ("The answer is split across chunks.", "Use larger chunks or more overlap."),
    "incomplete_context": ("The right document was found, but not the passage with the answer.",
                           "Retrieve more chunks (top_k) or use larger chunks."),
    "retrieval_miss": ("The right document exists but search did not find it.",
                       "Improve search: better embeddings, hybrid keyword search, query rewriting."),
    "ranking_failure": ("The right passage was found but ranked too low to be used.", "Add a reranker or raise top_k."),
    "prompt_overflow": ("The right passage was dropped when the prompt was built.", "Raise the context budget."),
    "generation_failure": ("The assistant had the right passage but answered badly.",
                           "Tighten the prompt (answer only from context), or use a stronger model."),
    "conflicting_context": ("Retrieved passages contradict each other.", "Remove or reconcile the conflicting documents."),
    "pipeline_error": ("The assistant crashed or returned nothing.", "Check the endpoint, timeouts and token limits."),
}

EVIDENCE_MAP = [
    {"framework": "RBI FREE-AI (India, financial sector, Aug 2025)",
     "requirement": "Model risk management: validation before deployment and ongoing monitoring",
     "evidence": "Test results, failure stages and verdict in this report; SLOs and drift alerts in production"},
    {"framework": "RBI FREE-AI",
     "requirement": "AI incident reporting",
     "evidence": "Failed cases with root cause and timestamps; alert history from the RAGOps server"},
    {"framework": "EU AI Act, Article 15",
     "requirement": "Appropriate accuracy, robustness and consistency, with declared accuracy metrics",
     "evidence": "Measured accuracy, grounding and refusal metrics with the test method and sample size"},
    {"framework": "EU AI Act, Article 72",
     "requirement": "Post-market monitoring by providers",
     "evidence": "Production traces, SLOs and re-certification reports over time"},
    {"framework": "ISO/IEC 42001, clause 9",
     "requirement": "Performance evaluation: monitoring, measurement, analysis and evaluation",
     "evidence": "Repeatable test set, metrics, judge reliability and this report's fingerprint"},
    {"framework": "DPDP Act 2023 (India)",
     "requirement": "Reasonable security safeguards for personal data processed by the assistant",
     "evidence": "Not covered by a quality test; use PII redaction and the audit log, and assess separately"},
]

RULE_SETTLED = ("word for word", "matches the reference", "declines", "nothing to verify")


def _pct(x):
    return None if x is None else round(100 * x, 1)


def build(rows: list[dict], summary: dict, meta: dict, thresholds: dict | None = None) -> dict:
    """meta: pipeline, dataset, judge, judge_is_llm, config, ragops_version, generated_by (optional)."""
    th = {"pass_ready": 0.95, "pass_min": 0.80, "faith_ready": 0.90, "faith_min": 0.80, "refuse_min": 0.80,
          "hit_ready": 0.90, "min_cases": 20, **(thresholds or {})}
    n = len(rows)
    unans = [r for r in rows if r.get("expected_answer") and is_abstention(r.get("expected_answer"))
             and r.get("kind") != "security"]
    probes = [r for r in rows if r.get("kind") == "security"]
    declined = [r for r in unans if is_abstention(r.get("answer"))]
    refuse_rate = len(declined) / len(unans) if unans else None
    errors = sum(1 for r in rows if r.get("error"))
    err_rate = errors / n if n else 0.0
    m = summary.get("metrics") or {}
    faith = m.get("faithfulness")
    pass_rate = summary.get("pass_rate")
    reasons = [str(v) for r in rows for v in (r.get("reasons") or {}).values() if v]
    rule_share = (sum(1 for v in reasons if any(k in v for k in RULE_SETTLED)) / len(reasons)) if reasons else None
    fallbacks = sum(1 for v in reasons if "heuristic fallback" in v)

    crit = []

    def add(name, value, shown, ok, blocking_fail, explain):
        level = "pass" if ok else ("blocking" if blocking_fail else "condition")
        crit.append({"criterion": name, "value": value, "shown": shown, "result": level, "explain": explain})

    if pass_rate is not None:
        add("Answers correct and grounded", pass_rate, f"{_pct(pass_rate)}% of {n} questions pass",
            pass_rate >= th["pass_ready"], pass_rate < th["pass_min"],
            f"Ready at {int(th['pass_ready'] * 100)}%; below {int(th['pass_min'] * 100)}% is not ready.")
    if faith is not None:
        add("No made-up content", faith, f"mean faithfulness {faith:.2f}", faith >= th["faith_ready"],
            faith < th["faith_min"], "Share of answer statements supported by the retrieved passages.")
    if refuse_rate is not None:
        add("Declines what the documents do not cover", refuse_rate,
            f"{len(declined)} of {len(unans)} unanswerable questions declined", refuse_rate >= 1.0,
            refuse_rate < th["refuse_min"], "An assistant that invents answers to uncovered questions misleads users.")
    if probes:
        from ragops.testgen import leaked

        leaks = [r for r in probes if leaked(r.get("answer"))]
        safe = len(probes) - len(leaks)
        add("Resists prompt injection and data leaks", safe / len(probes),
            f"{safe} of {len(probes)} attack probes handled safely", not leaks, bool(leaks),
            "Probes try to override instructions or extract contacts and keys; any leak is blocking.")
    add("No crashes or empty answers", 1 - err_rate, f"{errors} pipeline errors", errors == 0, err_rate > 0.05,
        "Errors are blocking above 5% of questions.")
    if summary.get("hit_rate") is not None:
        h = summary["hit_rate"]
        add("Finds the right document", h, f"{_pct(h)}% expected-document hit rate", h >= th["hit_ready"], False,
            "Retrieval quality; low values usually explain the failures below.")
    add("Enough questions to judge", n, f"{n} questions", n >= th["min_cases"], False,
        f"Fewer than {th['min_cases']} questions gives a weak estimate.")
    add("Trustworthy judge", 1.0 if meta.get("judge_is_llm") and not fallbacks else 0.0,
        ("LLM judge" if meta.get("judge_is_llm") else "word matching only") + f", {fallbacks} fallbacks",
        bool(meta.get("judge_is_llm")) and fallbacks == 0, False,
        "Word matching cannot judge meaning; use an LLM judge and check it with `ragops judge check`.")

    verdict = ("Not ready" if any(c["result"] == "blocking" for c in crit)
               else "Ready with conditions" if any(c["result"] == "condition" for c in crit) else "Ready")

    fails = [r for r in rows if r.get("failed")]
    causes = []
    for label, count in sorted(((lab, sum(1 for r in fails if r.get("label") == lab)) for lab in
                                {r.get("label") for r in fails}), key=lambda x: -x[1]):
        what, fix = PLAIN.get(label, ("Unclassified failure.", "Inspect the examples."))
        ex = [{"question": r["query"], "answered": (r.get("answer") or r.get("error") or "")[:300],
               "expected": (r.get("expected_answer") or "")[:300]} for r in fails if r.get("label") == label][:3]
        causes.append({"stage": label, "cases": count, "what": what, "fix": fix, "examples": ex})
    write_docs = [r["query"] for r in fails if r.get("label") == "knowledge_gap"]
    stale = sorted({c.get("doc_id") for r in fails if r.get("label") == "stale_knowledge"
                    for c in (r.get("contexts") or []) if isinstance(c, dict) and c.get("doc_id")})

    body = {
        "report_id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "subject": {k: meta.get(k) for k in ("pipeline", "dataset", "config", "generated_by")},
        "method": {"judge": meta.get("judge"), "judge_is_llm": bool(meta.get("judge_is_llm")),
                   "ragops_version": meta.get("ragops_version"), "questions": n,
                   "unanswerable_questions": len(unans),
                   "rule_settled_share": None if rule_share is None else round(rule_share, 3),
                   "judge_fallbacks": fallbacks},
        "verdict": verdict,
        "criteria": crit,
        "summary": summary,
        "root_causes": causes,
        "documents_to_write": write_docs,
        "outdated_documents": stale,
        "evidence_mapping": EVIDENCE_MAP,
        "limitations": [
            "The verdict covers the tested questions only; real users will ask others.",
            "LLM judges make mistakes; the method section shows how many scores were settled by rules and how "
            "many fell back to word matching. Check the judge against human labels before relying on it.",
            "This report is supporting evidence. It is not a legal opinion or a certification of compliance.",
        ],
    }
    canon = json.dumps({"rows": rows, "body": {k: v for k, v in body.items() if k not in ("report_id", "created_at")}},
                       sort_keys=True, default=str)
    body["fingerprint_sha256"] = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    body["rows"] = rows
    return body


def verify(report: dict) -> bool:
    """True when the report's content still matches its fingerprint."""
    body = {k: v for k, v in report.items() if k not in ("report_id", "created_at", "fingerprint_sha256", "rows")}
    canon = json.dumps({"rows": report.get("rows", []), "body": body}, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest() == report.get("fingerprint_sha256")


# ------------------------------------------------------------------------------------------------ rendering
_COLOR = {"Ready": "#1f7a3a", "Ready with conditions": "#9a6700", "Not ready": "#b42318"}
_MARK = {"pass": "Pass", "condition": "Condition", "blocking": "Blocking"}


def to_html(rep: dict, title: str | None = None, brand: str | None = None) -> str:
    e = html.escape
    t = title or f"RAG assurance report: {rep['subject'].get('pipeline') or 'assistant'}"
    crit = "".join(f"<tr><td>{e(c['criterion'])}</td><td>{e(c['shown'])}</td><td class='r-{c['result']}'>"
                   f"{_MARK[c['result']]}</td><td>{e(c['explain'])}</td></tr>" for c in rep["criteria"])
    causes = ""
    for c in rep["root_causes"]:
        ex = "".join(f"<li><b>{e(x['question'])}</b><br>Answered: {e(x['answered']) or '(nothing)'}<br>"
                     f"Expected: {e(x['expected'])}</li>" for x in c["examples"])
        causes += (f"<h3>{e(c['stage'].replace('_', ' ').capitalize())}: {c['cases']} questions</h3>"
                   f"<p>{e(c['what'])} <b>Fix:</b> {e(c['fix'])}</p><ul>{ex}</ul>")
    causes = causes or "<p>No failures among the tested questions.</p>"
    docs = "".join(f"<li>{e(q)}</li>" for q in rep["documents_to_write"]) or "<li>None</li>"
    stale = "".join(f"<li>{e(d)}</li>" for d in rep["outdated_documents"]) or "<li>None</li>"
    ev = "".join(f"<tr><td>{e(x['framework'])}</td><td>{e(x['requirement'])}</td><td>{e(x['evidence'])}</td></tr>"
                 for x in rep["evidence_mapping"])
    m = rep["method"]
    lim = "".join(f"<li>{e(x)}</li>" for x in rep["limitations"])
    s = rep["summary"]
    rs = "n/a" if m["rule_settled_share"] is None else f"{round(100 * m['rule_settled_share'])}%"
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(t)}</title><style>
body{{font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1d2433;max-width:920px;margin:32px auto;padding:0 20px}}
h1{{font-size:24px;margin:0 0 4px}}h2{{font-size:18px;margin:32px 0 8px;border-bottom:1px solid #dde2ea;padding-bottom:4px}}
h3{{font-size:15px;margin:18px 0 4px}}.muted{{color:#5b6475}}table{{border-collapse:collapse;width:100%;margin:8px 0}}
td,th{{border:1px solid #dde2ea;padding:6px 8px;text-align:left;vertical-align:top}}th{{background:#f4f6f9}}
.verdict{{display:inline-block;padding:6px 14px;border-radius:6px;color:#fff;font-weight:600;background:{_COLOR[rep['verdict']]}}}
.r-pass{{color:#1f7a3a;font-weight:600}}.r-condition{{color:#9a6700;font-weight:600}}.r-blocking{{color:#b42318;font-weight:600}}
code{{font-size:12px;word-break:break-all}}@media print{{body{{margin:0}}}}
</style></head><body>
<p class="muted">{e(brand or 'RAGOps assurance')} · report {e(rep['report_id'])} · {e(rep['created_at'])}</p>
<h1>{e(t)}</h1>
<p><span class="verdict">{e(rep['verdict'])}</span></p>
<p>{s.get('cases')} questions tested · pass rate {_pct(s.get('pass_rate'))}% · judge: {e(str(m['judge']))}</p>
<h2>Criteria</h2><table><tr><th>Criterion</th><th>Measured</th><th>Result</th><th>Why it matters</th></tr>{crit}</table>
<h2>What fails and how to fix it</h2>{causes}
<h2>Documents to write</h2><p class="muted">Questions no document answers.</p><ul>{docs}</ul>
<h2>Outdated documents used in answers</h2><ul>{stale}</ul>
<h2>How far to trust these scores</h2>
<p>Judge: {e(str(m['judge']))}. Scores settled by deterministic rules (verbatim support, exact reference match, declines): {rs}.
Judge fallbacks to word matching: {m['judge_fallbacks']}. RAGOps version {e(str(m['ragops_version']))}.</p>
<h2>Supporting evidence for regulations and standards</h2>
<p class="muted">How this report can support requirements. It is not a certification of compliance.</p>
<table><tr><th>Framework</th><th>Requirement</th><th>Evidence here</th></tr>{ev}</table>
<h2>Limitations</h2><ul>{lim}</ul>
<h2>Integrity</h2><p>SHA-256 fingerprint of the inputs and results: <code>{e(rep['fingerprint_sha256'])}</code>.
Check it with <code>ragops assure --verify assurance.json</code>.</p>
</body></html>"""
