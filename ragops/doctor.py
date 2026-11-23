"""RAG doctor: is my RAG working, what mistakes is it making, and which fix actually helps?

Three steps, all on YOUR pipeline:

1. **Check-up** – run a test set, score every answer, diagnose every failure by pipeline stage.
2. **Diagnosis** – group mistakes by stage, show the evidence and real examples, explain what to change.
3. **Treatment trial** (optional) – re-run the test set with candidate config changes ("knobs" your pipeline reads
   from its config dict, e.g. ``top_k``, ``rerank``, ``chunk_size``, ``temperature``) and compare each against the
   baseline with a paired bootstrap. A fix is only *verified* when it removes failures without any metric
   regressing significantly and without newly breaking cases.

Used by ``ragops doctor`` (CLI), ``POST /v1/projects/{p}/runs/{id}/doctor`` and the UI's Doctor page, and by
``GET /v1/projects/{p}/doctor`` for production traffic.
"""

from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

# --------------------------------------------------------------------------------------------- the playbook
# For every failure stage: what it means, how to confirm it, concrete fixes, and which config knobs to try
# (knob name, list of candidate values or a function of the current value).
PLAYBOOK: dict[str, dict[str, Any]] = {
    "knowledge_gap": {
        "title": "Missing knowledge",
        "meaning": "No document in your corpus answers these questions. No retriever or prompt can fix that.",
        "confirm": "Search your docs for the example questions; if nothing answers them, it is a content gap.",
        "fixes": ["Write or ingest the missing documents (the examples below are your to-do list).",
                  "Until then, make the bot say it does not know instead of guessing (abstention instruction).",
                  "Track these questions as a content backlog: `GET /v1/projects/{p}/kb-health` lists the gaps."],
        "knobs": [("abstain_when_unsure", [True])],
    },
    "stale_knowledge": {
        "title": "Outdated documents",
        "meaning": "Answers were built from an old version of a document that has since changed or been deleted.",
        "confirm": "Compare the doc_version in the trace with the current version in the corpus registry.",
        "fixes": ["Re-index changed documents (`ragops corpus push docs/` registers new versions).",
                  "Delete removed documents from the vector store, not just from the source system.",
                  "Add a freshness filter or version metadata to retrieval."],
        "knobs": [],
    },
    "chunking_defect": {
        "title": "Answers split across chunks",
        "meaning": "The answer is in your corpus but cut in half at a chunk boundary, so no single chunk carries it.",
        "confirm": "Open the two chunk ids in the evidence: the answer starts in one and ends in the next.",
        "fixes": ["Increase chunk size or add overlap (e.g. 15-20%).",
                  "Chunk by structure (headings, paragraphs, sentences) instead of fixed word counts.",
                  "Retrieve neighbouring chunks (parent-document / sentence-window retrieval)."],
        "knobs": [("chunk_size", lambda v: [int(v * 1.5), int(v * 2)] if isinstance(v, (int, float)) else [512, 1024]),
                  ("chunk_overlap", lambda v: [max(int(v or 0) * 2, 50)] if isinstance(v, (int, float)) or v is None else [64]),
                  ("expand_neighbors", [1])],
    },
    "retrieval_miss": {
        "title": "Retriever misses the right document",
        "meaning": "The answer exists in your corpus but the retriever did not return it.",
        "confirm": "The evidence shows the best-matching chunk in your corpus and what was retrieved instead.",
        "fixes": ["Retrieve more candidates (raise top_k) and rerank them.",
                  "Add keyword search next to vectors (hybrid BM25 + dense).",
                  "Try a stronger or domain-tuned embedding model.",
                  "Rewrite or expand the query (HyDE, multi-query) for short or vague questions."],
        "knobs": [("top_k", lambda v: [int(v * 2), int(v * 3)] if isinstance(v, (int, float)) else [10, 20]),
                  ("hybrid", [True]), ("query_rewrite", [True])],
    },
    "ranking_failure": {
        "title": "Right document retrieved, then dropped",
        "meaning": "The retriever found the right chunk, but reranking / top-k cut it before the LLM saw it.",
        "confirm": "Evidence shows the rank of the right chunk among the candidates and the context size.",
        "fixes": ["Pass more chunks to the LLM (raise the context top_k / rerank_top_n).",
                  "Use a better reranker (cross-encoder) or lower the score cutoff.",
                  "Check that the reranker sees the full chunk text, not a truncated preview."],
        "knobs": [("rerank_top_n", lambda v: [int(v * 2)] if isinstance(v, (int, float)) else [8]),
                  ("rerank", [True]), ("score_threshold", lambda v: [round(v / 2, 3)] if isinstance(v, (int, float)) and v else [])],
    },
    "incomplete_context": {
        "title": "Right document, wrong passage",
        "meaning": "The retriever found the right document, but the passage holding the answer did not make it into "
                   "the prompt – usually chunks that are too small or too few chunks passed to the model.",
        "confirm": "Context recall against the expected answer is low although the expected document is in context.",
        "fixes": ["Use larger chunks or add overlap so a passage carries a complete fact.",
                  "Pass more chunks to the model (raise top_k), ideally after reranking.",
                  "Retrieve neighbouring chunks of each hit (sentence-window / parent-document retrieval)."],
        "knobs": [("chunk_size", lambda v: [int(v * 2), int(v * 4)] if isinstance(v, (int, float)) else [512, 1024]),
                  ("top_k", lambda v: [int(v) + 2, int(v) * 3 + 2] if isinstance(v, (int, float)) else [5, 10]),
                  ("chunk_overlap", lambda v: [max(int(v or 0) * 2, 50)] if isinstance(v, (int, float)) or v is None else [64])],
    },
    "context_overload": {
        "title": "Right chunk buried in noise",
        "meaning": "The right chunk was in the prompt, but far down among many irrelevant ones, and the model missed it.",
        "confirm": "Evidence shows the position of the right chunk in the context and the fraction of relevant chunks.",
        "fixes": ["Send fewer, better chunks (lower top_k after reranking).",
                  "Put the most relevant chunk first (or first and last) in the prompt.",
                  "Compress or filter chunks before generation."],
        "knobs": [("top_k", lambda v: [max(2, int(v // 2))] if isinstance(v, (int, float)) else [3]),
                  ("rerank", [True])],
    },
    "generation_failure": {
        "title": "Good context, wrong answer",
        "meaning": "The model had the right information but answered incorrectly, invented details or refused.",
        "confirm": "Read the answer next to its context: unsupported claims show up in the faithfulness reasoning.",
        "fixes": ["Instruct the model to answer only from the context and cite sources.",
                  "Lower the temperature (0 - 0.2) for factual answers.",
                  "Try a stronger model, or add 2-3 worked examples to the prompt.",
                  "If it refuses with good context, relax over-strict refusal instructions."],
        "knobs": [("temperature", [0.0]), ("grounded_prompt", [True])],
    },
    "pipeline_error": {
        "title": "Pipeline crashed",
        "meaning": "Your RAG raised an exception or the endpoint returned an error, so there was no answer to judge.",
        "confirm": "The error column shows the exception or HTTP status for each case.",
        "fixes": ["Fix the error first – the other diagnoses only cover cases that ran.",
                  "For HTTP pipelines check the URL, auth header and answer_path/contexts_path mapping."],
        "knobs": [],
    },
    "unknown": {
        "title": "Unattributed failures",
        "meaning": "The answer was judged bad but the failure could not be pinned to one stage.",
        "confirm": "Register your corpus (--corpus docs/) so RAGOps can tell missing knowledge from retrieval problems.",
        "fixes": ["Run with --corpus pointing at your documents.", "Add expected_doc_ids to your test cases."],
        "knobs": [],
    },
}

# extra hints from the metric pattern, independent of the stage label
METRIC_HINTS = {
    "faithfulness": "The answers contain claims that are not supported by the retrieved context (hallucination).",
    "answer_relevance": "The answers do not address the question that was asked.",
    "context_relevance": "Much of the retrieved context is irrelevant to the question.",
    "context_recall": "The retrieved context does not contain the facts in the expected answers.",
    "citation_correctness": "Citations point at sources that do not support the cited sentence.",
    "abstention_quality": "The bot answers when it should say 'I don't know', or refuses when it had the answer.",
}

HEALTHY, NEEDS_WORK, BROKEN = "healthy", "needs work", "broken"


@dataclass
class Finding:
    stage: str
    title: str
    cases: int
    share: float
    meaning: str
    confirm: str
    fixes: list[str]
    examples: list[dict] = field(default_factory=list)


@dataclass
class Trial:
    change: dict
    failures_before: int
    failures_after: int
    fixed: int
    broken: int
    improved_metrics: list[str]
    regressed_metrics: list[str]
    verified: bool
    note: str = ""


@dataclass
class DoctorReport:
    verdict: str
    summary: str
    cases: int
    failures: int
    pass_rate: float | None
    metrics: dict
    weak_metrics: list[dict]
    findings: list[Finding]
    trials: list[Trial] = field(default_factory=list)
    best_fix: dict | None = None
    next_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self, title: str = "RAG doctor") -> str:
        pct = lambda x: "–" if x is None else f"{x:.0%}"  # noqa: E731
        icon = {HEALTHY: "🟢", NEEDS_WORK: "🟠", BROKEN: "🔴"}[self.verdict]
        out = [f"# {title}", "", f"## {icon} Verdict: {self.verdict}", "", self.summary, "",
               f"{self.cases} test cases · pass rate {pct(self.pass_rate)} · {self.failures} mistakes", ""]
        if self.weak_metrics:
            out += ["## Weak spots", ""]
            out += [f"- **{w['metric'].replace('_', ' ')}** {w['score']:.2f} (want ≥ {w['target']:.2f}): {w['hint']}"
                    for w in self.weak_metrics]
            out.append("")
        if self.findings:
            out += ["## Mistakes, by cause", ""]
            for i, f in enumerate(self.findings, 1):
                out += [f"### {i}. {f.title} — {f.cases} case{'s' if f.cases != 1 else ''} ({f.share:.0%} of mistakes)",
                        "", f.meaning, "", f"*How to confirm:* {f.confirm}", "", "**How to fix it:**", ""]
                out += [f"- {x}" for x in f.fixes]
                if f.examples:
                    out += ["", "Examples:", ""]
                    for e in f.examples:
                        line = f"- `{e['query'][:110]}`"
                        if e.get("why"):
                            line += f" — {e['why']}"
                        out.append(line)
                out.append("")
        if self.trials:
            out += ["## Treatment trial: fixes tested on your pipeline", "",
                    "| Change | Mistakes before → after | Fixed | Newly broken | Metrics | Verified |", "|---|---|---|---|---|---|"]
            for t in self.trials:
                ch = ", ".join(f"{k}={v}" for k, v in t.change.items())
                met = ", ".join([f"+{m}" for m in t.improved_metrics] + [f"−{m}" for m in t.regressed_metrics]) or "no significant change"
                out.append(f"| `{ch}` | {t.failures_before} → {t.failures_after} | {t.fixed} | {t.broken} | {met} | "
                           f"{'✅' if t.verified else '❌'} {t.note} |")
            out.append("")
            if self.best_fix:
                ch = ", ".join(f"{k}={v}" for k, v in self.best_fix["change"].items())
                out += [f"**Recommended change: `{ch}`** — {self.best_fix['why']}", ""]
            else:
                out += ["No tested change was a verified improvement; follow the fixes above.", ""]
        if self.next_steps:
            out += ["## Next steps", ""] + [f"{i}. {s}" for i, s in enumerate(self.next_steps, 1)] + [""]
        return "\n".join(out)


# --------------------------------------------------------------------------------------------- diagnosis
METRIC_TARGETS = {"faithfulness": 0.7, "answer_relevance": 0.6, "context_relevance": 0.5, "context_recall": 0.6,
                  "citation_correctness": 0.7, "abstention_quality": 0.8}


def _why(row: dict) -> str:
    if row.get("error"):
        return row["error"][:140]
    ev = row.get("evidence") or {}
    bits = list(ev.get("failure_reasons") or [])[:2]
    if ev.get("note"):
        bits.append(ev["note"])
    if ev.get("best_corpus_match") and row.get("label") in ("retrieval_miss", "ranking_failure"):
        bits.append(f"right source: {ev['best_corpus_match']['doc_id']}")
    if ev.get("gold_rank_in_candidates"):
        bits.append(f"right chunk was candidate #{ev['gold_rank_in_candidates']}")
    if ev.get("gold_position_in_context"):
        bits.append(f"right chunk at position {ev['gold_position_in_context']} of {ev.get('context_size')}")
    if ev.get("split_across"):
        bits.append("split across " + " + ".join(ev["split_across"]))
    return "; ".join(bits)[:220]


def refine_label(row: dict) -> str:
    """Sharpen the stage label with evidence that only a labelled test set gives: if the expected document reached
    the prompt but the facts of the expected answer did not, the generator is not to blame."""
    lab = row.get("label") or "unknown"
    rec = (row.get("scores") or {}).get("context_recall")
    if lab == "generation_failure" and rec is not None and rec < 0.5 and (row.get("expected_answer") or row.get("hit")):
        return "incomplete_context"
    return lab


def diagnose_rows(rows: list[dict], summary: dict | None = None, examples: int = 3) -> DoctorReport:
    """Turn evaluated rows (from runs.run_cases, a stored run, or production traces) into a diagnosis."""
    from ragops.runs import summarize

    rows = [{**r, "label": refine_label(r)} if r.get("failed") else r for r in rows]
    summary = summary or summarize(rows)
    n, fails = summary["cases"], [r for r in rows if r["failed"]]
    by_stage = Counter(r["label"] for r in fails)
    findings = []
    for stage, c in by_stage.most_common():
        pb = PLAYBOOK.get(stage, PLAYBOOK["unknown"])
        ex = [{"query": r["query"], "answer": (r.get("answer") or "")[:300], "why": _why(r)}
              for r in fails if r["label"] == stage][:examples]
        findings.append(Finding(stage, pb["title"], c, round(c / len(fails), 3), pb["meaning"], pb["confirm"],
                                list(pb["fixes"]), ex))
    weak = [{"metric": m, "score": v, "target": METRIC_TARGETS[m], "hint": METRIC_HINTS[m]}
            for m, v in (summary.get("metrics") or {}).items()
            if v is not None and m in METRIC_TARGETS and v < METRIC_TARGETS[m]]
    rate = summary.get("failure_rate") or 0
    if n == 0:
        verdict, text = BROKEN, "No test cases ran."
    elif summary.get("errors", 0) > n / 2:
        verdict, text = BROKEN, f"{summary['errors']} of {n} cases crashed — the pipeline is not working end to end."
    elif rate <= 0.1 and not weak:
        verdict, text = HEALTHY, "Your RAG answers the test set well. Keep this run as the baseline for the release gate."
    elif rate <= 0.4:
        top = findings[0].title.lower() if findings else "low scores"
        verdict, text = NEEDS_WORK, f"Your RAG works but makes mistakes on {rate:.0%} of questions; the main cause is {top}."
    else:
        top = findings[0].title.lower() if findings else "low scores"
        verdict, text = BROKEN, f"Your RAG fails {rate:.0%} of questions; the main cause is {top}."
    steps = []
    if summary.get("errors"):
        steps.append("Fix the pipeline errors first, then run the doctor again.")
    crashed_all = bool(fails) and all(r.get("error") for r in fails)
    if not crashed_all and (any(f.stage == "unknown" for f in findings) or not any(
            (r.get("evidence") or {}).get("best_corpus_match") for r in fails)):
        if fails:
            steps.append("Pass your documents with --corpus so mistakes can be traced to retrieval vs. missing content.")
    if findings:
        steps.append(f"Start with '{findings[0].title}': it explains {findings[0].share:.0%} of the mistakes.")
    if fails:
        steps.append("Save the failing cases as a golden set and gate releases on it: `ragops gate` / the GitHub Action.")
    return DoctorReport(verdict, text, n, len(fails), summary.get("pass_rate"), summary.get("metrics") or {}, weak,
                        findings, next_steps=steps)


# --------------------------------------------------------------------------------------------- treatment trial
def propose_changes(report: DoctorReport, config: dict, knobs: dict[str, list] | None = None,
                    max_trials: int = 6) -> list[dict]:
    """Candidate config changes. Explicit ``knobs`` (from ragops.yaml / --try) win; otherwise the playbook proposes
    changes for knobs that already appear in your config (so we only touch settings your pipeline reads)."""
    out: list[dict] = []
    if knobs:
        for k, vals in knobs.items():
            for v in (vals if isinstance(vals, list) else [vals]):
                if config.get(k) != v:
                    out.append({k: v})
        return out[:max_trials]
    for f in report.findings:
        for knob, vals in PLAYBOOK.get(f.stage, {}).get("knobs", []):
            if knob not in config:
                continue
            cands = vals(config[knob]) if callable(vals) else vals
            for v in cands:
                ch = {knob: v}
                if config.get(knob) != v and ch not in out:
                    out.append(ch)
    return out[:max_trials]


def missing_knobs(report: DoctorReport, config: dict) -> list[str]:
    """Knobs the playbook would try for the observed failures that the pipeline config does not expose."""
    want = []
    for f in report.findings:
        for knob, _ in PLAYBOOK.get(f.stage, {}).get("knobs", []):
            if knob not in config and knob not in want:
                want.append(knob)
    return want


def trial_from_rows(change: dict, base_rows: list[dict], rows: list[dict], max_drop: float = 0.02) -> Trial:
    """Compare a candidate run with the baseline run (paired by query) and decide whether the change is a
    verified fix: fewer mistakes, more cases fixed than broken, no new crashes, no metric significantly worse."""
    from ragops.runs import compare_rows

    base_fail = sum(bool(r["failed"]) for r in base_rows)
    rep = compare_rows(base_rows, rows, max_drop=max_drop)
    after = sum(bool(r["failed"]) for r in rows)
    improved = [m.metric for m in rep.metrics if m.status == "improved"]
    regressed = [m.metric for m in rep.metrics if m.status == "regressed"]
    errors = sum(1 for r in rows if r.get("error")) - sum(1 for r in base_rows if r.get("error"))
    verified = after < base_fail and not regressed and errors <= 0 and len(rep.newly_failing) < len(rep.newly_fixed)
    note = ""
    if errors > 0:
        note = f"{errors} new pipeline errors"
    elif regressed:
        note = "a metric got significantly worse"
    elif after >= base_fail:
        note = "no fewer mistakes"
    elif len(rep.newly_failing) >= len(rep.newly_fixed):
        note = "breaks as many cases as it fixes"
    return Trial(change, base_fail, after, len(rep.newly_fixed), len(rep.newly_failing), improved, regressed,
                 verified, note)


def run_trials(pipeline: Callable, cases: list[dict], base_rows: list[dict], config: dict, changes: list[dict],
               judge=None, corpus=None, max_drop: float = 0.02,
               on_trial: Callable[[dict], None] | None = None) -> list[Trial]:
    from ragops.runs import run_cases

    trials = []
    for ch in changes:
        if on_trial:
            on_trial(ch)
        rows = run_cases(pipeline, cases, {**config, **ch}, judge, corpus=corpus)
        trials.append(trial_from_rows(ch, base_rows, rows, max_drop))
    return trials


def pick_best(trials: list[Trial]) -> dict | None:
    ok = [t for t in trials if t.verified]
    if not ok:
        return None
    t = min(ok, key=lambda t: (t.failures_after, t.broken, -len(t.improved_metrics)))
    why = (f"removes {t.failures_before - t.failures_after} of {t.failures_before} mistakes "
           f"({t.fixed} fixed, {t.broken} newly broken) with no significant metric regression")
    if t.improved_metrics:
        why += f"; significantly improves {', '.join(t.improved_metrics)}"
    return {"change": t.change, "why": why}


def combine_best(pipeline: Callable, cases: list[dict], base_rows: list[dict], config: dict, trials: list[Trial],
                 judge=None, corpus=None) -> Trial | None:
    """If two or more single changes were verified, also test them together."""
    ok = [t for t in trials if t.verified]
    if len(ok) < 2:
        return None
    merged: dict = {}
    for t in sorted(ok, key=lambda t: t.failures_after)[:3]:
        if not (set(t.change) & set(merged)):
            merged.update(t.change)
    if len(merged) < 2:
        return None
    return run_trials(pipeline, cases, base_rows, config, [merged], judge, corpus)[0]


def doctor(pipeline: Callable, cases: list[dict], config: dict | None = None, judge=None, corpus=None,
           knobs: dict[str, list] | None = None, try_fixes: bool = True, max_trials: int = 6,
           on_case: Callable | None = None, on_trial: Callable | None = None) -> tuple[DoctorReport, list[dict]]:
    """Full check-up → diagnosis → treatment trial on a user's pipeline. Returns (report, baseline rows)."""
    from ragops.runs import run_cases, summarize

    config = dict(config or {})
    rows = run_cases(pipeline, cases, config, judge, corpus=corpus, on_case=on_case)
    rep = diagnose_rows(rows, summarize(rows))
    if try_fixes and rep.failures:
        changes = propose_changes(rep, config, knobs, max_trials)
        if changes:
            rep.trials = run_trials(pipeline, cases, rows, config, changes, judge, corpus, on_trial=on_trial)
            combo = combine_best(pipeline, cases, rows, config, rep.trials, judge, corpus)
            if combo:
                rep.trials.append(combo)
            rep.best_fix = pick_best(rep.trials)
            if rep.best_fix:
                ch = ", ".join(f"{k}={v}" for k, v in rep.best_fix["change"].items())
                rep.next_steps.insert(0, f"Apply `{ch}` to your pipeline config — it was verified on your test set.")
        else:
            want = missing_knobs(rep, config)
            if want:
                rep.next_steps.append(
                    "To let the doctor test fixes automatically, make your pipeline read these settings from its "
                    f"config dict and add them to `config:` in ragops.yaml: {', '.join(want)}.")
    return rep, rows


def grid(knobs: dict[str, list]) -> list[dict]:
    """All combinations of knob values (for explicit sweeps)."""
    keys = list(knobs)
    return [dict(zip(keys, combo)) for combo in itertools.product(*[knobs[k] for k in keys])]
