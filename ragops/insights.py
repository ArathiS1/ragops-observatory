"""Data behind the 0.9 dashboard: the overview cockpit, the evidence graph, failure intelligence, the release
guardian, the benchmark lab and the integrations / security page.

Everything here reads existing tables (traces, evaluations, diagnoses, runs, alerts, audit log); no schema change.
"""

from __future__ import annotations

import importlib.util
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ragops.diagnosis import LABEL_HELP
from ragops.models import Alert, ApiKey, AuditLog, ConfigVersion, EvalRun, Evaluation, Project, Trace, utcnow

QUALITY = ["faithfulness", "answer_relevance", "context_relevance", "context_recall", "answer_correctness"]
STEPS = ["retrieve", "rerank", "generate"]


def _pctl(v: list[float], q: float) -> float | None:
    return round(float(np.percentile(v, q)), 1) if v else None


def _buckets(since: datetime, until: datetime, hourly: bool) -> list[datetime]:
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    t = since.replace(minute=0, second=0, microsecond=0) if hourly else since.replace(hour=0, minute=0, second=0,
                                                                                    microsecond=0)
    out = []
    while t <= until:
        out.append(t)
        t += step
    return out


def _key(t: datetime, hourly: bool) -> str:
    return t.strftime("%Y-%m-%dT%H:00") if hourly else t.strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------------ cockpit
def cockpit(db: Session, project: Project, days: int = 7, environment: str | None = None) -> dict:
    """KPI cards with trends and change vs the previous window, the pipeline flow with per-stage latency,
    Hit@K, quality cards, feedback split, environments and recent alerts."""
    now = utcnow()
    since, prev_since = now - timedelta(days=days), now - timedelta(days=2 * days)
    hourly = days <= 2

    def load(a, b):
        stmt = (select(Trace).options(selectinload(Trace.chunks), selectinload(Trace.diagnosis))
                .where(Trace.project_id == project.id, Trace.created_at >= a, Trace.created_at < b)
                .order_by(Trace.created_at).limit(50_000))
        if environment:
            stmt = stmt.where(Trace.meta["environment"].as_string() == environment.lower())
        return db.scalars(stmt).all()

    cur, prev = load(since, now + timedelta(seconds=1)), load(prev_since, since)

    def evals(a, b):
        rows = db.execute(select(Evaluation.trace_id, Evaluation.metric, Evaluation.score, Evaluation.created_at)
                          .where(Evaluation.project_id == project.id, Evaluation.created_at >= a,
                                 Evaluation.created_at < b, Evaluation.metric.in_(QUALITY),
                                 Evaluation.score.is_not(None))).all()
        if environment:
            ids = {t.id for t in (cur if a == since else prev)}
            rows = [r for r in rows if r.trace_id in ids]
        return rows

    ecur, eprev = evals(since, now + timedelta(seconds=1)), evals(prev_since, since)

    def kpis(ts: list[Trace], es) -> dict:
        evaluated = [t for t in ts if t.eval_status == "done"]
        fails = sum(1 for t in evaluated if t.is_failure)
        lat = [t.latency_ms for t in ts if t.latency_ms is not None]
        fb = [t.user_feedback for t in ts if t.user_feedback is not None]
        by_m = defaultdict(list)
        for r in es:
            by_m[r.metric].append(r.score)
        return {"requests": len(ts),
                "success_rate": round(1 - fails / len(evaluated), 4) if evaluated else None,
                "latency_p95_ms": _pctl(lat, 95),
                # None (not reported) rather than 0 when the app never sends tokens or costs
                "tokens": (sum((t.prompt_tokens or 0) + (t.completion_tokens or 0) for t in ts)
                           if any(t.prompt_tokens is not None or t.completion_tokens is not None for t in ts) else None),
                "cost_usd": (round(sum(t.cost_usd or 0 for t in ts), 6) if any(t.cost_usd is not None for t in ts)
                             else None),
                "positive_feedback": round(sum(1 for f in fb if f > 0) / len(fb), 4) if fb else None,
                **{m: (round(float(np.mean(v)), 4) if v else None) for m, v in by_m.items()}}

    kc, kp = kpis(cur, ecur), kpis(prev, eprev)
    keys = [_key(b, hourly) for b in _buckets(since, now, hourly)]
    groups: dict[str, list[Trace]] = defaultdict(list)
    for t in cur:
        groups[_key(t.created_at, hourly)].append(t)
    egroups: dict[str, list] = defaultdict(list)
    for r in ecur:
        egroups[_key(r.created_at, hourly)].append(r)
    series = defaultdict(list)
    for k in keys:
        s = kpis(groups.get(k, []), egroups.get(k, []))
        for name in ("requests", "success_rate", "latency_p95_ms", "tokens", "cost_usd", "positive_feedback", *QUALITY):
            series[name].append({"t": k, "v": s.get(name)})
    cards = {name: {"value": kc.get(name), "previous": kp.get(name), "series": series[name]}
             for name in ("requests", "success_rate", "latency_p95_ms", "tokens", "cost_usd", "positive_feedback")}
    quality = {m: {"value": kc.get(m), "previous": kp.get(m), "series": series[m]} for m in QUALITY
               if kc.get(m) is not None or kp.get(m) is not None}

    # pipeline flow: how many requests reach each stage, and how long each stage takes
    step_lat = {s: [] for s in STEPS}
    for t in cur:
        for s, ms in ((t.meta or {}).get("step_latency_ms") or {}).items():
            if s in step_lat and isinstance(ms, (int, float)):
                step_lat[s].append(float(ms))
    retrieved = sum(1 for t in cur if any(c.step in ("retrieve", "context") for c in t.chunks))
    reranked = sum(1 for t in cur if any(c.step == "rerank" for c in t.chunks)) or len(step_lat["rerank"])
    answered = sum(1 for t in cur if t.answer)
    evaluated = [t for t in cur if t.eval_status == "done"]
    good = sum(1 for t in evaluated if not t.is_failure)
    lat = [t.latency_ms for t in cur if t.latency_ms is not None]
    flow = [
        {"stage": "query", "name": "Query", "count": len(cur), "share": 1.0 if cur else None},
        {"stage": "retrieve", "name": "Retrieval", "count": retrieved, "share": retrieved / len(cur) if cur else None,
         "p50_ms": _pctl(step_lat["retrieve"], 50), "p95_ms": _pctl(step_lat["retrieve"], 95)},
        {"stage": "rerank", "name": "Reranking", "count": reranked, "share": reranked / len(cur) if cur else None,
         "p50_ms": _pctl(step_lat["rerank"], 50), "p95_ms": _pctl(step_lat["rerank"], 95)},
        {"stage": "generate", "name": "Generation", "count": answered, "share": answered / len(cur) if cur else None,
         "p50_ms": _pctl(step_lat["generate"], 50), "p95_ms": _pctl(step_lat["generate"], 95)},
        {"stage": "response", "name": "Correct answer", "count": good, "share": good / len(evaluated) if evaluated else None,
         "p50_ms": _pctl(lat, 50), "p95_ms": _pctl(lat, 95), "note": f"of {len(evaluated)} evaluated"},
    ]

    # Hit@K on labelled traffic: rank of the first expected document among the retrieved chunks
    ranks = []
    for t in cur:
        exp = set((t.meta or {}).get("expected_doc_ids") or [])
        if not exp:
            continue
        cand = [c for c in t.chunks if c.step == "retrieve"] or [c for c in t.chunks if c.step == "context"]
        seen, r = [], None
        for c in sorted(cand, key=lambda c: c.rank):
            if c.doc_id and c.doc_id not in seen:
                seen.append(c.doc_id)
                if c.doc_id in exp:
                    r = len(seen)
                    break
        ranks.append(r)
    hit = {"labelled": len(ranks), **{f"top_{k}": (round(sum(1 for r in ranks if r and r <= k) / len(ranks), 4)
                                               if ranks else None) for k in (1, 3, 5, 10)}}

    fb = Counter("positive" if t.user_feedback > 0 else "negative" if t.user_feedback < 0 else "neutral"
                 for t in cur if t.user_feedback is not None)
    labels = Counter(t.diagnosis.label for t in cur if t.diagnosis and t.is_failure)
    envs = Counter((t.meta or {}).get("environment") or "production" for t in cur)
    alerts = db.scalars(select(Alert).where(Alert.project_id == project.id).order_by(Alert.id.desc()).limit(6)).all()
    configs = Counter()
    for t in cur:
        configs[t.config_version_id] += 1
    cv = {c.id: c.label for c in db.scalars(select(ConfigVersion).where(ConfigVersion.id.in_(list(configs)))).all()}
    return {
        "window_days": days, "bucket": "hour" if hourly else "day", "environment": environment,
        "cards": cards, "quality": quality, "flow": flow, "hit_at_k": hit,
        "feedback": {"positive": fb["positive"], "neutral": fb["neutral"], "negative": fb["negative"],
                     "none": len(cur) - sum(fb.values())},
        "failures_by_stage": dict(labels.most_common()), "environments": dict(envs),
        "versions": [{"label": cv.get(k, "unversioned"), "requests": n} for k, n in configs.most_common(6)],
        "alerts": [{"id": a.id, "severity": a.severity, "title": a.title, "body": a.body[:300],
                    "at": a.created_at.isoformat()} for a in alerts],
    }


# ------------------------------------------------------------------------------------------------ evidence graph
def evidence_graph(trace_detail: dict) -> dict:
    """Documents -> chunks -> answer claims, from a trace_detail() dict. Edges carry the claim verdict, so the
    dashboard can show which source supports (or contradicts) each sentence of the answer."""
    from ragops.reliability import claim_evidence

    chunks = [c for c in trace_detail.get("chunks", []) if c["step"] == "context"] or \
             [c for c in trace_detail.get("chunks", []) if c["step"] == "retrieve"]
    dx = trace_detail.get("diagnosis") or {}
    claims = (dx.get("evidence") or {}).get("claims")
    if claims is None:
        claims = claim_evidence(trace_detail.get("answer"), chunks)
    nodes, edges = [], []
    docs: dict[str, dict] = {}
    for c in chunks:
        d = c.get("doc_id") or "unknown document"
        docs.setdefault(d, {"id": f"doc:{d}", "kind": "document", "label": d, "version": c.get("doc_version"),
                            "stale": False, "chunks": 0})
        docs[d]["chunks"] += 1
        docs[d]["stale"] = docs[d]["stale"] or bool(c.get("stale"))
    nodes += list(docs.values())
    key = {}
    for i, c in enumerate(chunks):
        cid = f"chunk:{c.get('chunk_id') or i}:{c['rank']}"
        key[(c.get("chunk_id"), c.get("doc_id"))] = cid
        nodes.append({"id": cid, "kind": "chunk", "label": f"#{c['rank']} {c.get('chunk_id') or ''}".strip(),
                      "text": c.get("text"), "rank": c["rank"], "score": c.get("score"), "stale": bool(c.get("stale")),
                      "gold": bool(c.get("is_gold"))})
        edges.append({"source": f"doc:{c.get('doc_id') or 'unknown document'}", "target": cid, "kind": "contains"})
    for j, cl in enumerate(claims):
        nid = f"claim:{j}"
        nodes.append({"id": nid, "kind": "claim", "label": cl["claim"], "verdict": cl["verdict"],
                      "evidence": cl.get("evidence")})
        src = key.get((cl.get("chunk_id"), cl.get("doc_id"))) or next(
            (v for (ch, d), v in key.items() if d == cl.get("doc_id")), None)
        if src and cl["verdict"] != "unsupported":
            edges.append({"source": src, "target": nid, "kind": cl["verdict"], "overlap": cl.get("overlap")})
    n = len(claims)
    sup = sum(1 for c in claims if c["verdict"] == "supported")
    return {"trace_id": trace_detail["id"], "question": trace_detail["query"], "answer": trace_detail.get("answer"),
            "nodes": nodes, "edges": edges,
            "summary": {"claims": n, "supported": sup,
                        "contradicted": sum(1 for c in claims if c["verdict"] == "contradicted"),
                        "unsupported": sum(1 for c in claims if c["verdict"] == "unsupported"),
                        "documents": len(docs), "chunks": len(chunks),
                        "grounded_share": round(sup / n, 4) if n else None}}


# ------------------------------------------------------------------------------------------------ failure intelligence
def failure_intelligence(db: Session, project: Project, days: int = 7, environment: str | None = None) -> dict:
    """Failure patterns with a trend (new, growing, shrinking, steady), first and last seen, a daily series, the
    likely root cause with its confidence, and the evidence behind it."""
    from ragops.reliability import failure_patterns

    now = utcnow()
    since = now - timedelta(days=days)
    stmt = (select(Trace).options(selectinload(Trace.chunks), selectinload(Trace.diagnosis))
            .where(Trace.project_id == project.id, Trace.created_at >= since, Trace.is_failure.is_(True))
            .order_by(Trace.created_at.desc()).limit(3000))
    if environment:
        stmt = stmt.where(Trace.meta["environment"].as_string() == environment.lower())
    traces = {t.id: t for t in db.scalars(stmt).all()}
    rows = [{"id": t.id, "query": t.query, "label": t.diagnosis.label if t.diagnosis else "unknown",
             "doc_ids": sorted({c.doc_id for c in t.chunks if c.doc_id and c.step == "context"})}
            for t in traces.values()]
    mid = since + (now - since) / 2
    keys = [_key(b, False) for b in _buckets(since, now, False)]
    out = []
    for p in failure_patterns(rows, max_patterns=30):
        members = [traces[i] for i in p["trace_ids"] if i in traces]
        first = sum(1 for t in members if t.created_at < mid)
        second = len(members) - first
        trend = ("new" if first == 0 and second >= 1 else "growing" if second > first * 1.5 and second - first >= 2
                 else "shrinking" if second < first / 1.5 and first - second >= 2 else "steady")
        per_day = Counter(_key(t.created_at, False) for t in members)
        conf = [t.diagnosis.confidence for t in members if t.diagnosis]
        ev = Counter()
        stale = Counter()
        for t in members:
            e = (t.diagnosis.evidence or {}) if t.diagnosis else {}
            b = (e.get("best_corpus_match") or {}).get("doc_id")
            if b:
                ev[b] += 1
            for s in e.get("stale_documents") or []:
                stale[s.get("doc_id") if isinstance(s, dict) else str(s)] += 1
        lab = p["stage"]
        cause = {"knowledge_gap": "No document answers these questions.",
                 "stale_knowledge": "Answers come from outdated document versions.",
                 "chunking_defect": "The answer is split across chunk boundaries.",
                 "retrieval_miss": "The right document exists but search does not find it.",
                 "ranking_failure": "The right passage is retrieved but ranked out of the prompt.",
                 "context_overload": "Too many passages in the prompt drown the right one.",
                 "generation_failure": "The model had the right passage but answered badly.",
                 "incomplete_context": "The right document is found, but not the passage with the answer.",
                 "security_leak": "The assistant followed injected instructions or exposed data."}.get(lab, "Unclassified.")
        cv = Counter(t.config_version_id for t in members)
        top_cfg = cv.most_common(1)[0][0] if cv else None
        cfg_label = db.scalar(select(ConfigVersion.label).where(ConfigVersion.id == top_cfg)) if top_cfg else None
        out.append({**p, "trace_ids": p["trace_ids"][:50], "trend": trend,
                    "first_seen": min(t.created_at for t in members).isoformat() if members else None,
                    "last_seen": max(t.created_at for t in members).isoformat() if members else None,
                    "series": [{"t": k, "v": per_day.get(k, 0)} for k in keys],
                    "likely_cause": cause, "confidence": round(float(np.mean(conf)), 3) if conf else None,
                    "evidence": {"closest_documents": [d for d, _ in ev.most_common(3)],
                                 "stale_documents": [d for d, _ in stale.most_common(3)],
                                 "mostly_on_version": cfg_label,
                                 "version_share": round(cv.most_common(1)[0][1] / len(members), 3) if members else None},
                    "fix": LABEL_HELP.get(lab, "")})
    rank = {"new": 0, "growing": 1, "steady": 2, "shrinking": 3}
    out.sort(key=lambda x: (rank[x["trend"]] if x["count"] >= 2 else 4, -x["count"]))
    trends = Counter(x["trend"] for x in out)
    return {"window_days": days, "failures": len(rows), "patterns": out, "trends": dict(trends)}


# ------------------------------------------------------------------------------------------------ release guardian
DEFAULT_GATES = {"min_pass_rate": 0.80, "max_pass_rate_drop": 0.02, "max_metric_drop": 0.02,
                 "max_p95_ms": None, "max_latency_increase": 0.30, "max_error_rate": 0.02,
                 "block_on_security_leak": True, "review_if_newly_failing": 1}


def gates_for(project: Project) -> dict:
    return {**DEFAULT_GATES, **((project.settings or {}).get("release_gates") or {})}


def release_check(base: EvalRun, cand: EvalRun, gates: dict) -> dict:
    """Pass / Review / Block for a candidate run against the current one, with every gate and a case-by-case diff."""
    from ragops.run_service import run_rows
    from ragops.runs import DEFAULT_METRICS, compare_rows, summarize
    from ragops.testgen import leaked

    br, cr = run_rows(base), run_rows(cand)
    for rows, run in ((br, base), (cr, cand)):
        for r, c in zip(rows, run.cases):
            if (c.evidence or {}).get("kind"):
                r["kind"] = c.evidence["kind"]
    rep = compare_rows(br, cr, DEFAULT_METRICS, gates.get("max_metric_drop") or 0.02)
    sb, sc = summarize(br), summarize(cr)
    checks = []

    def gate(name, value, limit, ok, severity="block", shown=None):
        checks.append({"gate": name, "value": value, "limit": limit, "passed": bool(ok), "severity": severity,
                       "shown": shown or f"{value} (limit {limit})"})

    pr, ppr = sc.get("pass_rate"), sb.get("pass_rate")
    if gates.get("min_pass_rate") is not None and pr is not None:
        gate("Pass rate", pr, gates["min_pass_rate"], pr >= gates["min_pass_rate"],
             shown=f"{pr:.0%} (needs {gates['min_pass_rate']:.0%})")
    if gates.get("max_pass_rate_drop") is not None and pr is not None and ppr is not None:
        drop = round(ppr - pr, 4)
        gate("Pass rate change", -drop, -gates["max_pass_rate_drop"], drop <= gates["max_pass_rate_drop"],
             shown=f"{ppr:.0%} → {pr:.0%}")
    reg = [m for m in rep.metrics if m.status == "regressed"]
    gate("No significant metric regression", len(reg), 0, not reg,
         shown=", ".join(f"{m.metric} {m.delta:+.3f}" for m in reg) or "none regressed")
    bl, cl = sb.get("latency_p95_ms"), sc.get("latency_p95_ms")
    if gates.get("max_p95_ms") and cl is not None:
        gate("p95 latency", cl, gates["max_p95_ms"], cl <= gates["max_p95_ms"], shown=f"{cl:.0f} ms (limit {gates['max_p95_ms']} ms)")
    if gates.get("max_latency_increase") is not None and bl and cl is not None:
        inc = round(cl / bl - 1, 3)
        gate("p95 latency change", inc, gates["max_latency_increase"], inc <= gates["max_latency_increase"],
             severity="review", shown=f"{bl:.{0 if bl >= 10 else 1}f} → {cl:.{0 if cl >= 10 else 1}f} ms ({inc:+.0%})")
    if gates.get("max_error_rate") is not None and cr:
        er = round(sum(1 for r in cr if r.get("error")) / len(cr), 4)
        gate("Pipeline errors", er, gates["max_error_rate"], er <= gates["max_error_rate"], shown=f"{er:.0%} of cases")
    probes = [r for r in cr if r.get("kind") == "security"]
    if gates.get("block_on_security_leak") and probes:
        leaks = sum(1 for r in probes if leaked(r.get("answer")))
        gate("No security leaks", leaks, 0, leaks == 0, shown=f"{leaks} of {len(probes)} probes leaked")

    by_b = {r["query"]: r for r in br}
    by_c = {r["query"]: r for r in cr}
    cases = []
    for q in list(by_b) + [q for q in by_c if q not in by_b]:
        b, c = by_b.get(q), by_c.get(q)
        if b and c:
            st = ("regression" if c["failed"] and not b["failed"] else "fixed" if b["failed"] and not c["failed"]
                  else "still_failing" if c["failed"] else "unchanged")
        else:
            st = "added" if c else "removed"
        side = lambda r: None if r is None else {  # noqa: E731
            "failed": r["failed"], "label": r.get("label"), "answer": (r.get("answer") or "")[:400],
            "correctness": (r.get("scores") or {}).get("answer_correctness"),
            "faithfulness": (r.get("scores") or {}).get("faithfulness"), "latency_ms": r.get("latency_ms")}
        cases.append({"query": q, "status": st, "expected": (b or c).get("expected_answer"),
                      "base": side(b), "candidate": side(c)})
    newly = sum(1 for x in cases if x["status"] == "regression")
    if gates.get("review_if_newly_failing") is not None:
        gate("Newly failing questions", newly, gates["review_if_newly_failing"] - 1,
             newly < gates["review_if_newly_failing"], severity="review", shown=f"{newly} questions")
    blocked = [c for c in checks if not c["passed"] and c["severity"] == "block"]
    review = [c for c in checks if not c["passed"] and c["severity"] == "review"]
    decision = "block" if blocked else "review" if review else "pass"
    counts = Counter(x["status"] for x in cases)
    return {"decision": decision, "base": {"id": base.id, "name": base.name, "summary": sb},
            "candidate": {"id": cand.id, "name": cand.name, "summary": sc}, "gates": gates, "checks": checks,
            "metrics": [m.__dict__ for m in rep.metrics], "counts": dict(counts), "cases": cases}


def release_decisions(db: Session, project: Project, limit: int = 50) -> list[dict]:
    rows = db.scalars(select(AuditLog).where(AuditLog.project_id == project.id, AuditLog.action == "release.decision")
                      .order_by(AuditLog.id.desc()).limit(limit)).all()
    return [{"at": a.created_at.isoformat(), "actor": a.actor, **(a.detail or {})} for a in rows]


# ------------------------------------------------------------------------------------------------ benchmark lab
def benchmark(runs: list[EvalRun]) -> dict:
    """Several runs (models, retrievers, chunk sizes) on the same questions: summary per run, the best one, and a
    question-by-question grid."""
    from ragops.run_service import run_rows
    from ragops.runs import summarize

    out, grid = [], {}
    all_rows = {run.id: run_rows(run) for run in runs}
    shared = set.intersection(*[{r["query"] for r in rows} for rows in all_rows.values()]) if all_rows else set()
    for run in runs:
        own = all_rows[run.id]
        # compare like with like: score every run on the questions all of them answered
        rows = [r for r in own if r["query"] in shared] if shared else own
        s = summarize(rows)
        out.append({"id": run.id, "name": run.name, "pipeline": run.pipeline, "config": run.config,
                    "dataset": run.dataset, "created_at": run.created_at.isoformat(), "cases": s["cases"],
                    "pass_rate": s["pass_rate"], "hit_rate": s["hit_rate"], "metrics": s["metrics"],
                    "ranking": s.get("ranking") or {}, "latency_p50_ms": s["latency_p50_ms"],
                    "latency_p95_ms": s["latency_p95_ms"], "errors": s["errors"], "stages": s["stages"],
                    "own_cases": len(own)})
        for r in own:
            grid.setdefault(r["query"], {})[run.id] = {
                "failed": r["failed"], "label": r.get("label"),
                "correctness": (r.get("scores") or {}).get("answer_correctness"),
                "faithfulness": (r.get("scores") or {}).get("faithfulness")}
    scored = [r for r in out if r["pass_rate"] is not None] if shared else []
    best = max(scored, key=lambda r: (r["pass_rate"], (r["metrics"] or {}).get("faithfulness") or 0,
                                      -(r["latency_p95_ms"] or 1e9))) if scored else None
    questions = [{"query": q, "results": v, "disagree": len({x["failed"] for x in v.values()}) > 1}
                 for q, v in grid.items()]
    questions.sort(key=lambda x: (not x["disagree"], x["query"]))
    return {"runs": out, "best": best["id"] if best else None, "shared_questions": len(shared),
            "questions": questions[:300]}


# ------------------------------------------------------------------------------------------------ integrations
def _installed(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def integrations(db: Session, project: Project) -> dict:
    from ragops.config import get_settings
    from ragops.eval.judges import get_judge

    st = get_settings()
    judge = get_judge()
    jname = getattr(judge, "name", "heuristic")
    oidc = bool(os.environ.get("RAGOPS_OIDC_ISSUER"))
    items = [
        {"name": "LLM judge", "status": "active" if jname != "heuristic" else "fallback",
         "detail": f"{jname}" + ("" if jname != "heuristic" else ": word matching; set RAGOPS_JUDGE for meaning-based scores"),
         "how": "RAGOPS_JUDGE=openai + RAGOPS_JUDGE_BASE_URL / _MODEL / _API_KEY; check with `ragops judge check`"},
        {"name": "Ragas", "status": "installed" if _installed("ragas") else "not installed",
         "detail": "Ragas metrics as the judge (RAGOPS_JUDGE=ragas)", "how": "pip install ragas"},
        {"name": "Evidently", "status": "installed" if _installed("evidently") else "not installed",
         "detail": "Drift reports on the Drift page (/drift/evidently)", "how": "pip install evidently"},
        {"name": "Presidio", "status": "installed" if _installed("presidio_analyzer") else "rules only",
         "detail": "PII detection before storage; built-in rules are used otherwise",
         "how": "pip install presidio-analyzer presidio-anonymizer"},
        {"name": "OpenTelemetry (OTLP)", "status": "active", "detail": "POST /otlp/v1/traces (protobuf or JSON)",
         "how": "point an OTLP/HTTP exporter at the server"},
        {"name": "PostgreSQL", "status": "active" if st.database_url.startswith("postgres") else "sqlite",
         "detail": st.database_url.split("://")[0], "how": "RAGOPS_DATABASE_URL=postgresql+psycopg://..."},
        {"name": "Redis queue", "status": "active" if os.environ.get("RAGOPS_REDIS_URL") else "database queue",
         "detail": "background evaluation jobs", "how": "RAGOPS_REDIS_URL=redis://..."},
        {"name": "ClickHouse", "status": "active" if st.clickhouse_url else "off",
         "detail": "high-volume analytics mirror", "how": "RAGOPS_CLICKHOUSE_URL=http://..."},
        {"name": "Single sign-on (OIDC)", "status": "active" if oidc else "off",
         "detail": "Google, Microsoft Entra, Okta, Keycloak...", "how": "RAGOPS_OIDC_ISSUER + client id/secret"},
    ]
    since = utcnow() - timedelta(days=30)
    keys = db.execute(select(ApiKey.role, func.count()).where(
        ApiKey.revoked.is_(False), (ApiKey.project_id == project.id) | (ApiKey.org_id == project.org_id))
        .group_by(ApiKey.role)).all()
    audits = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.project_id == project.id,
                                                                     AuditLog.created_at >= since)) or 0
    s = {**(project.settings or {})}
    from ragops.models import DEFAULT_PROJECT_SETTINGS

    s = {**DEFAULT_PROJECT_SETTINGS, **s}
    posture = [
        {"check": "PII redaction before storage", "ok": bool(s["redact_fields"]),
         "detail": f"fields: {', '.join(s['redact_fields']) or 'none'}; entities: {', '.join(s['redaction_entities'])}"},
        {"check": "Raw text storage", "ok": True,
         "detail": "raw text kept" if s["store_raw_text"] else "salted hashes only"},
        {"check": "Retention", "ok": bool(s["retention_days"]), "detail": f"{s['retention_days']} days"},
        {"check": "Admin key changed from the development default", "ok": not st.is_dev_admin_key,
         "detail": "set RAGOPS_ADMIN_KEY" if st.is_dev_admin_key else "custom admin key"},
        {"check": "Single sign-on", "ok": oidc, "detail": "on" if oidc else "API keys only"},
        {"check": "Audit log", "ok": True, "detail": f"{audits} events in 30 days"},
    ]
    return {"integrations": items, "security": posture, "keys_by_role": {r: int(n) for r, n in keys},
            "organisation": project.org_id}
