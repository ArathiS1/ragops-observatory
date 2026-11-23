"""CI release gate: replay a golden set through the baseline and candidate pipeline configs and block the
change if quality regresses *significantly*.

Your pipeline is any Python callable ``fn(query: str, config: dict) -> dict`` returning::

    {"answer": str,
     "contexts":  [{"text": ..., "doc_id": ..., "chunk_id": ..., "score": ...}],   # what the LLM saw
     "retrieved": [...same shape...]}                                              # optional pre-rerank list

Significance comes from a paired bootstrap over golden cases, so a 1-case wobble cannot block a PR and a real
regression cannot hide behind a small mean change.
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ragops.diagnosis import CorpusView
from ragops.eval.judges import HeuristicJudge, Judge

DEFAULT_GATE_METRICS = ["faithfulness", "answer_relevance", "context_relevance", "context_recall", "abstention_quality"]


@dataclass
class MetricDiff:
    metric: str
    baseline: float
    candidate: float
    delta: float
    ci_low: float
    ci_high: float
    n: int
    status: str  # regressed | improved | unchanged


@dataclass
class GateReport:
    passed: bool
    n_cases: int
    metrics: list[MetricDiff]
    stage_failures_baseline: dict[str, int]
    stage_failures_candidate: dict[str, int]
    hit_rate_baseline: float | None
    hit_rate_candidate: float | None
    newly_failing: list[dict] = field(default_factory=list)
    newly_fixed: list[dict] = field(default_factory=list)
    max_drop: float = 0.02
    reasons: list[str] = field(default_factory=list)
    latency_p95_baseline: float | None = None
    latency_p95_candidate: float | None = None
    budgets: list[dict] = field(default_factory=list)  # {name, limit, value, passed}

    def to_junit(self, suite: str = "ragops-release-gate") -> str:
        """JUnit XML for CI systems (GitHub Actions, GitLab, Jenkins, Azure DevOps): one test per metric and
        budget, failed when it regressed or broke its budget."""
        from xml.sax.saxutils import escape, quoteattr

        cases = []
        for m in self.metrics:
            msg = (f"{m.metric}: {m.baseline:.3f} -> {m.candidate:.3f} (delta {m.delta:+.3f}, "
                   f"95% CI {m.ci_low:+.3f}..{m.ci_high:+.3f}, n={m.n})")
            fail = f'<failure message={quoteattr("regressed")}>{escape(msg)}</failure>' if m.status == "regressed" else ""
            cases.append(f'<testcase classname="quality" name={quoteattr(m.metric)}>{fail}<system-out>{escape(msg)}'
                         f"</system-out></testcase>")
        for b in self.budgets:
            msg = f"{b['name']}: {b['value']} (limit {b['limit']})"
            fail = "" if b["passed"] else f'<failure message={quoteattr("over budget")}>{escape(msg)}</failure>'
            cases.append(f'<testcase classname="budget" name={quoteattr(b["name"])}>{fail}</testcase>')
        failures = sum(1 for m in self.metrics if m.status == "regressed") + sum(1 for b in self.budgets if not b["passed"])
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name={quoteattr(suite)} tests="{len(cases)}" '
                f'failures="{failures}">\n' + "\n".join(cases) + "\n</testsuite>\n")

    def to_dict(self):
        return asdict(self)

    def to_markdown(self, title: str = "RAGOps release gate") -> str:
        icon = "✅" if self.passed else "❌"
        lines = [f"## {icon} {title}: {'PASSED' if self.passed else 'BLOCKED'}", "",
                 f"Replayed **{self.n_cases}** golden cases against baseline and candidate configs. "
                 f"A metric blocks the release when it drops by more than {self.max_drop:.0%} *and* the 95% "
                 "bootstrap interval of the paired difference is entirely below zero.", "",
                 "| Metric | Baseline | Candidate | Δ | 95% CI | Status |", "|---|---|---|---|---|---|"]
        for m in self.metrics:
            st = {"regressed": "🔻 regressed", "improved": "🟢 improved", "unchanged": "– unchanged"}[m.status]
            lines.append(f"| {m.metric} | {m.baseline:.3f} | {m.candidate:.3f} | {m.delta:+.3f} | "
                         f"[{m.ci_low:+.3f}, {m.ci_high:+.3f}] | {st} |")
        if self.hit_rate_baseline is not None:
            lines += ["", f"**Expected-document hit rate:** {self.hit_rate_baseline:.0%} → {self.hit_rate_candidate:.0%}"]
        labels = sorted(set(self.stage_failures_baseline) | set(self.stage_failures_candidate))
        if labels:
            lines += ["", "### Failures by pipeline stage", "", "| Stage | Baseline | Candidate |", "|---|---|---|"]
            for lab in labels:
                b, c = self.stage_failures_baseline.get(lab, 0), self.stage_failures_candidate.get(lab, 0)
                arrow = " ⚠️" if c > b else ""
                lines.append(f"| {lab} | {b} | {c}{arrow} |")
        if self.latency_p95_baseline is not None:
            lines += ["", f"**p95 latency:** {self.latency_p95_baseline:.0f} ms → {self.latency_p95_candidate:.0f} ms"]
        if self.budgets:
            lines += ["", "### Budgets", "", "| Budget | Limit | Candidate | Status |", "|---|---|---|---|"]
            for b in self.budgets:
                lines.append(f"| {b['name']} | {b['limit']} | {b['value']} | {'ok' if b['passed'] else '❌ over'} |")
        if self.newly_failing:
            lines += ["", f"### Newly failing cases ({len(self.newly_failing)})", ""]
            for f in self.newly_failing[:10]:
                lines.append(f"- `{f['query'][:90]}` → **{f['label']}**")
        if self.reasons:
            lines += ["", "**Why blocked:** " + "; ".join(self.reasons)]
        return "\n".join(lines) + "\n"


def load_callable(spec: str) -> Callable:
    """'package.module:function' or 'path/to/file.py:function'."""
    mod_name, _, fn_name = spec.partition(":")
    if mod_name.endswith(".py"):
        p = Path(mod_name).resolve()
        sys.path.insert(0, str(p.parent))
        mod = importlib.import_module(p.stem)
    else:
        sys.path.insert(0, str(Path.cwd()))
        mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name or "pipeline")


def load_json_or_yaml(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith((".yaml", ".yml")):
        import yaml  # type: ignore

        return yaml.safe_load(text)
    return json.loads(text)


def paired_bootstrap(diffs: np.ndarray, n_boot: int = 2000, seed: int = 7) -> tuple[float, float]:
    from ragops.runs import paired_bootstrap as pb

    return pb(diffs, n_boot, seed)


def run_gate(cases: list[dict], pipeline: Callable, baseline_cfg: dict, candidate_cfg: dict,
             judge: Judge | None = None, metrics: list[str] | None = None, max_drop: float = 0.02,
             corpus: CorpusView | None = None, n_boot: int = 2000, max_p95_ms: float | None = None,
             max_latency_increase: float | None = None, max_error_rate: float | None = None) -> GateReport:
    from ragops.runs import compare_rows, run_cases

    judge = judge or HeuristicJudge()
    metrics = metrics or DEFAULT_GATE_METRICS
    base = run_cases(pipeline, cases, baseline_cfg, judge, metrics, corpus)
    cand = run_cases(pipeline, cases, candidate_cfg, judge, metrics, corpus)
    rep = compare_rows(base, cand, metrics, max_drop, n_boot)
    return apply_budgets(rep, base, cand, max_p95_ms, max_latency_increase, max_error_rate)


def apply_budgets(rep: GateReport, base: list[dict], cand: list[dict], max_p95_ms: float | None = None,
                  max_latency_increase: float | None = None, max_error_rate: float | None = None) -> GateReport:
    """Operational budgets next to the quality comparison: a release that answers as well but twice as slowly, or
    crashes more, can also be blocked."""
    def p95(rows):
        v = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
        return round(float(np.percentile(v, 95)), 1) if v else None

    rep.latency_p95_baseline, rep.latency_p95_candidate = p95(base), p95(cand)
    b, c = rep.latency_p95_baseline, rep.latency_p95_candidate
    if max_p95_ms is not None and c is not None:
        rep.budgets.append({"name": "p95 latency (ms)", "limit": max_p95_ms, "value": c, "passed": c <= max_p95_ms})
    if max_latency_increase is not None and b and c is not None:
        inc = round(c / b - 1, 3)
        rep.budgets.append({"name": "p95 latency increase", "limit": max_latency_increase, "value": inc,
                            "passed": inc <= max_latency_increase})
    if max_error_rate is not None and cand:
        er = round(sum(1 for r in cand if r.get("error")) / len(cand), 4)
        rep.budgets.append({"name": "pipeline error rate", "limit": max_error_rate, "value": er,
                            "passed": er <= max_error_rate})
    for x in rep.budgets:
        if not x["passed"]:
            rep.passed = False
            rep.reasons.append(f"{x['name']} {x['value']} over budget {x['limit']}")
    return rep
