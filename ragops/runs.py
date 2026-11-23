"""Test runs: execute a dataset through a user's RAG pipeline, evaluate and diagnose every case, compare runs.

Shared by ``ragops test`` (local), the server's pipeline runner (UI "Run test" button) and the CI gate.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path
from typing import Callable

import numpy as np

from ragops.diagnosis import CorpusView, diagnose
from ragops.eval.judges import HeuristicJudge, Judge, evaluate
from ragops.eval.types import ContextChunk, RagSample

DEFAULT_METRICS = ["answer_correctness", "faithfulness", "answer_relevance", "context_relevance", "context_recall",
                   "abstention_quality", "citation_correctness"]


# ------------------------------------------------------------------------------------------------ datasets
def load_dataset(path: str) -> list[dict]:
    """JSONL, JSON (list or {"cases": [...]}) or CSV with columns query[, expected_answer, expected_doc_ids]."""
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith(".jsonl"):
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif path.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(text)))
    elif path.endswith((".yaml", ".yml")):
        import yaml

        rows = yaml.safe_load(text)
    else:
        rows = json.loads(text)
    if isinstance(rows, dict):
        rows = rows.get("cases") or rows.get("data") or []
    out = []
    for r in rows:
        q = r.get("query") or r.get("question") or r.get("input")
        if not q:
            continue
        ids = r.get("expected_doc_ids") or r.get("doc_ids") or []
        if isinstance(ids, str):
            ids = [x.strip() for x in ids.replace(";", ",").split(",") if x.strip()]
        out.append({"query": q, "expected_answer": r.get("expected_answer") or r.get("answer") or None,
                    "expected_doc_ids": ids, **({"kind": r["kind"]} if r.get("kind") else {})})
    return out


def _chunks(items) -> list[ContextChunk]:
    out = []
    for i, c in enumerate(items or []):
        if isinstance(c, str):
            c = {"text": c}
        out.append(ContextChunk(text=c.get("text") or "", doc_id=c.get("doc_id"), chunk_id=c.get("chunk_id"),
                                doc_version=c.get("doc_version"), score=c.get("score"), rank=c.get("rank") or i + 1))
    return out


# ------------------------------------------------------------------------------------------------ execution
def evaluate_case(case: dict, out: dict, judge: Judge, metrics: list[str], corpus: CorpusView | None) -> dict:
    sample = RagSample(query=case["query"], answer=out.get("answer"), contexts=_chunks(out.get("contexts")),
                       retrieved=_chunks(out.get("retrieved")), expected_answer=case.get("expected_answer"),
                       expected_doc_ids=case.get("expected_doc_ids") or [])
    if corpus is not None and not corpus.empty:
        sample.corpus_max_similarity = float(corpus.relevance(case["query"]).max())
    res = evaluate(sample, judge, metrics)
    scores = {k: v.score for k, v in res.items()}
    dx = diagnose(sample, scores, corpus)
    hit = bool(set(sample.expected_doc_ids) & {c.doc_id for c in sample.contexts}) if sample.expected_doc_ids else None
    label = dx.label
    if dx.is_failure:  # test cases carry a reference answer, which sharpens the stage label (see ragops.doctor)
        from ragops.doctor import refine_label

        label = refine_label({"label": label, "scores": scores, "expected_answer": case.get("expected_answer"), "hit": hit})
    from ragops.reliability import claim_evidence, claim_summary, ranking_metrics

    ranking = ranking_metrics([c.doc_id for c in (sample.retrieved or sample.contexts)], sample.expected_doc_ids)
    claims = claim_evidence(sample.answer, [{"text": c.text, "chunk_id": c.chunk_id, "doc_id": c.doc_id}
                                            for c in sample.contexts])
    evidence = {**(dx.evidence or {}), **({"claims": claims, "claim_summary": claim_summary(claims)} if claims else {}),
                **({"ranking": ranking} if ranking else {})}
    return {"scores": scores, "label": label, "failed": dx.is_failure, "hit": hit, "evidence": evidence,
            "ranking": ranking,
            "reasons": {k: v.reasoning for k, v in res.items()}}


def run_cases(pipeline: Callable[[str, dict], dict], cases: list[dict], config: dict | None = None,
              judge: Judge | None = None, metrics: list[str] | None = None, corpus: CorpusView | None = None,
              on_case: Callable[[int, dict], None] | None = None) -> list[dict]:
    """Run every case; a pipeline exception becomes a failed case (label pipeline_error), never a crash."""
    import time

    from ragops.pipelines import normalise

    judge = judge or HeuristicJudge()
    metrics = metrics or DEFAULT_METRICS
    rows = []
    for i, case in enumerate(cases):
        t0 = time.perf_counter()
        try:
            out = normalise(pipeline(case["query"], config or {}))
            lat = (time.perf_counter() - t0) * 1000
            row = {"query": case["query"], "answer": out["answer"], "contexts": out["contexts"],
                   "latency_ms": round(lat, 1), "error": None, **evaluate_case(case, out, judge, metrics, corpus)}
        except Exception as e:  # noqa: BLE001
            row = {"query": case["query"], "answer": None, "contexts": [], "latency_ms": None,
                   "error": f"{type(e).__name__}: {e}"[:500], "scores": {}, "label": "pipeline_error", "failed": True,
                   "hit": None, "evidence": {}, "reasons": {}}
        row["expected_answer"] = case.get("expected_answer")
        row["expected_doc_ids"] = case.get("expected_doc_ids") or []
        if case.get("kind"):
            row["kind"] = case["kind"]
        if case.get("kind") == "security":
            from ragops.testgen import leaked

            if leaked(row.get("answer")):  # followed an injected instruction or exposed data: always a failure
                row["failed"], row["label"] = True, "security_leak"
                row.setdefault("evidence", {})["failure_reasons"] = ["answer leaked a canary, contact or key"]
        rows.append(row)
        if on_case:
            on_case(i, row)
    return rows


def summarize(rows: list[dict], metrics: list[str] | None = None) -> dict:
    metrics = metrics or DEFAULT_METRICS
    n = len(rows)
    fails = [r for r in rows if r["failed"]]
    lat = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    means = {}
    for m in metrics:
        v = [r["scores"].get(m) for r in rows if r["scores"].get(m) is not None]
        means[m] = round(float(np.mean(v)), 4) if v else None
    hits = [r["hit"] for r in rows if r.get("hit") is not None]
    stages = Counter(r["label"] for r in fails)
    ranking = {}
    for m in ("precision_at_k", "recall_at_k", "mrr", "ndcg"):
        v = [rk[m] for r in rows if (rk := (r.get("ranking") or (r.get("evidence") or {}).get("ranking") or {})).get(m)
             is not None]
        if v:
            ranking[m] = round(float(np.mean(v)), 4)
    claims = [c for r in rows for c in ((r.get("evidence") or {}).get("claims") or [])]
    from ragops.reliability import claim_summary

    return {"ranking": ranking, "claims": claim_summary(claims),"cases": n, "failures": len(fails), "failure_rate": round(len(fails) / n, 4) if n else None,
            "pass_rate": round(1 - len(fails) / n, 4) if n else None, "metrics": means,
            "hit_rate": round(float(np.mean(hits)), 4) if hits else None,
            "stages": dict(stages.most_common()), "errors": sum(1 for r in rows if r.get("error")),
            "latency_p50_ms": round(float(np.percentile(lat, 50)), 1) if lat else None,
            "latency_p95_ms": round(float(np.percentile(lat, 95)), 1) if lat else None}


def report_markdown(name: str, summary: dict, rows: list[dict], top: int = 10) -> str:
    from ragops.diagnosis import LABEL_HELP

    pct = lambda x: "–" if x is None else f"{x:.0%}"  # noqa: E731
    lines = [f"# RAG test report: {name}", "",
             f"**{summary['cases']} cases · pass rate {pct(summary['pass_rate'])} · "
             f"{summary['failures']} failures**" + (f" · {summary['errors']} pipeline errors" if summary["errors"] else ""),
             "", "| Metric | Mean |", "|---|---|"]
    for m, v in summary["metrics"].items():
        lines.append(f"| {m.replace('_', ' ')} | {'–' if v is None else f'{v:.3f}'} |")
    if summary["hit_rate"] is not None:
        lines.append(f"| expected-document hit rate | {summary['hit_rate']:.0%} |")
    if summary["latency_p50_ms"] is not None:
        lines.append(f"| latency p50 / p95 | {summary['latency_p50_ms']:.0f} / {summary['latency_p95_ms']:.0f} ms |")
    if summary["stages"]:
        lines += ["", "## Where it fails", "", "| Stage | Cases | What to do |", "|---|---|---|"]
        for lab, c in summary["stages"].items():
            lines.append(f"| {lab.replace('_', ' ')} | {c} | {LABEL_HELP.get(lab, 'Check the endpoint and its logs.')} |")
        lines += ["", f"## Failed cases (first {top})", ""]
        for r in [r for r in rows if r["failed"]][:top]:
            why = r.get("error") or "; ".join((r.get("evidence") or {}).get("failure_reasons", [])[:2])
            lines.append(f"- **{r['label']}**: `{r['query'][:100]}` ({why})")
            if not r.get("error"):
                ans = " ".join((r.get("answer") or "").split())[:300]
                lines.append(f"  - answered: {ans or '(empty)'}")
                if r.get("expected_answer"):
                    lines.append(f"  - expected: {' '.join(str(r['expected_answer']).split())[:300]}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------ comparison
def paired_bootstrap(diffs: np.ndarray, n_boot: int = 2000, seed: int = 7) -> tuple[float, float]:
    if len(diffs) == 0:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diffs), size=(n_boot, len(diffs)))
    means = diffs[idx].mean(1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def compare_rows(base: list[dict], cand: list[dict], metrics: list[str] | None = None, max_drop: float = 0.02,
                 n_boot: int = 2000):
    """Paired comparison of two runs over the same queries. Returns a gate.GateReport."""
    from ragops.gate import GateReport, MetricDiff

    metrics = metrics or DEFAULT_METRICS
    by_q = {r["query"]: r for r in cand}
    pairs = [(b, by_q[b["query"]]) for b in base if b["query"] in by_q]
    diffs_out, reasons = [], []
    for m in metrics:
        vals = [(b["scores"].get(m), c["scores"].get(m)) for b, c in pairs]
        vals = [(x, y) for x, y in vals if x is not None and y is not None]
        if not vals:
            continue
        bv, cv = np.array([v[0] for v in vals]), np.array([v[1] for v in vals])
        d = cv - bv
        lo, hi = paired_bootstrap(d, n_boot)
        delta = float(d.mean())
        if hi < 0 and delta < -max_drop:
            status = "regressed"
            reasons.append(f"{m} dropped {delta:+.3f} (95% CI {lo:+.3f}..{hi:+.3f})")
        elif lo > 0 and delta > max_drop:
            status = "improved"
        else:
            status = "unchanged"
        diffs_out.append(MetricDiff(m, round(float(bv.mean()), 4), round(float(cv.mean()), 4), round(delta, 4),
                                    round(lo, 4), round(hi, 4), len(vals), status))
    sb = Counter(b["label"] for b, _ in pairs if b["failed"])
    sc = Counter(c["label"] for _, c in pairs if c["failed"])
    newly_failing = [{"query": b["query"], "label": c["label"]} for b, c in pairs if c["failed"] and not b["failed"]]
    newly_fixed = [{"query": b["query"], "label": b["label"]} for b, c in pairs if b["failed"] and not c["failed"]]
    errs = sum(1 for _, c in pairs if c.get("error"))
    if errs and errs > sum(1 for b, _ in pairs if b.get("error")):
        reasons.append(f"{errs} pipeline errors in the candidate run")
    hb = [b["hit"] for b, _ in pairs if b.get("hit") is not None]
    hc = [c["hit"] for _, c in pairs if c.get("hit") is not None]
    return GateReport(not reasons, len(pairs), diffs_out, dict(sb), dict(sc),
                      round(float(np.mean(hb)), 4) if hb else None, round(float(np.mean(hc)), 4) if hc else None,
                      newly_failing, newly_fixed, max_drop, reasons)
