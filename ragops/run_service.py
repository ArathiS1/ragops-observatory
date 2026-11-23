"""Server side of bring-your-own-RAG: pipeline registry, test runs executed by the worker, run comparison."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ragops import services as svc
from ragops.db import session_scope
from ragops.models import EvalRun, EvalRunCase, GoldenCase, GoldenSet, Project, RagPipeline, utcnow
from ragops.runs import DEFAULT_METRICS, compare_rows, run_cases, summarize

SECRET_HEADER_WORDS = ("authorization", "api-key", "apikey", "token", "secret", "x-api-key")


def public_pipeline(p: RagPipeline) -> dict:
    spec = dict(p.spec or {})
    if spec.get("headers"):
        spec["headers"] = {k: ("••••" if any(w in k.lower() for w in SECRET_HEADER_WORDS) else v)
                           for k, v in spec["headers"].items()}
    return {"name": p.name, "kind": p.kind, "spec": spec, "default_config": p.default_config,
            "description": p.description, "created_at": p.created_at.isoformat()}


def upsert_pipeline(db: Session, project: Project, name: str, spec: dict, default_config: dict | None,
                    description: str | None) -> RagPipeline:
    p = db.scalar(select(RagPipeline).where(RagPipeline.project_id == project.id, RagPipeline.name == name))
    if p is None:
        p = RagPipeline(project_id=project.id, name=name)
        db.add(p)
    p.kind = spec.get("kind", "http")
    p.spec = spec
    p.default_config = default_config or {}
    p.description = description
    db.flush()
    return p


def build_pipeline(p: RagPipeline):
    from ragops.pipelines import from_spec

    if p.kind != "http":
        raise ValueError("only HTTP pipelines can run on the server; test Python pipelines with `ragops test`")
    return from_spec({**p.spec, "kind": "http", "guard": True})


def dataset_cases(db: Session, project: Project, dataset: dict) -> tuple[str, list[dict]]:
    if dataset.get("golden_set"):
        g = db.scalar(select(GoldenSet).where(GoldenSet.project_id == project.id, GoldenSet.name == dataset["golden_set"]))
        if g is None:
            raise ValueError(f"golden set '{dataset['golden_set']}' not found")
        cases = db.scalars(select(GoldenCase).where(GoldenCase.golden_set_id == g.id).order_by(GoldenCase.created_at)).all()
        return f"golden:{g.name}@v{g.version}", [{"query": c.query, "expected_answer": c.expected_answer,
                                                   "expected_doc_ids": c.expected_doc_ids} for c in cases]
    cases = dataset.get("cases") or []
    if not cases:
        raise ValueError("dataset needs 'golden_set' or a non-empty 'cases' list")
    return dataset.get("name", "uploaded"), [{"query": c["query"], "expected_answer": c.get("expected_answer"),
                                              "expected_doc_ids": c.get("expected_doc_ids") or []} for c in cases]


def create_run(db: Session, project: Project, pipeline: RagPipeline, dataset: dict, config: dict | None,
               name: str | None) -> EvalRun:
    label, cases = dataset_cases(db, project, dataset)
    cfg = {**(pipeline.default_config or {}), **(config or {})}
    run = EvalRun(project_id=project.id, name=name or f"{pipeline.name} on {label}", pipeline=pipeline.name,
                  config=cfg, dataset=label, total=len(cases), status="queued")
    db.add(run)
    db.flush()
    for i, c in enumerate(cases):
        db.add(EvalRunCase(run_id=run.id, idx=i, query=c["query"], expected_answer=c["expected_answer"],
                           expected_doc_ids=c["expected_doc_ids"]))
    svc.enqueue(db, "eval_run", {"run_id": run.id})
    return run


def _row_to_case(row: dict, case: EvalRunCase) -> None:
    case.answer, case.contexts, case.scores = row["answer"], row["contexts"], row["scores"]
    case.label, case.failed, case.hit = row["label"], row["failed"], row["hit"]
    case.latency_ms, case.error = row["latency_ms"], row["error"]
    case.evidence = {k: v for k, v in (row.get("evidence") or {}).items() if k != "best_corpus_match"} | (
        {"best_corpus_match": row["evidence"]["best_corpus_match"]} if (row.get("evidence") or {}).get("best_corpus_match")
        else {}) | ({"kind": row["kind"]} if row.get("kind") else {})


def execute_run(run_id: str) -> None:
    """Worker job. Commits progress every few cases so the UI can show a live progress bar."""
    with session_scope() as db:
        run = db.get(EvalRun, run_id)
        if run is None or run.status == "done":
            return
        pipe = db.scalar(select(RagPipeline).where(RagPipeline.project_id == run.project_id,
                                                   RagPipeline.name == run.pipeline))
        cases = [{"query": c.query, "expected_answer": c.expected_answer, "expected_doc_ids": c.expected_doc_ids,
                  "_id": c.id} for c in run.cases]
        cfg, project_id = dict(run.config or {}), run.project_id
        try:
            fn = build_pipeline(pipe) if pipe else None
            if fn is None:
                raise ValueError(f"pipeline '{run.pipeline}' no longer exists")
        except Exception as e:  # noqa: BLE001
            run.status, run.error, run.finished_at = "failed", str(e), utcnow()
            return
        run.status = "running"
        corpus = svc.load_corpus_view(db, project_id)
    pending: list[tuple[int, dict]] = []

    def flush():
        with session_scope() as db:
            run = db.get(EvalRun, run_id)
            for cid, row in pending:
                _row_to_case(row, db.get(EvalRunCase, cid))
            run.progress += len(pending)
        pending.clear()

    def on_case(i, row):
        pending.append((cases[i]["_id"], row))
        if len(pending) >= 5:
            flush()

    rows = run_cases(fn, cases, cfg, None, DEFAULT_METRICS, corpus, on_case)
    if pending:
        flush()
    with session_scope() as db:
        run = db.get(EvalRun, run_id)
        run.summary = summarize(rows)
        run.status = "done" if run.summary["errors"] < len(rows) or not rows else "failed"
        if run.status == "failed":
            run.error = rows[0]["error"] if rows else "no cases"
        run.finished_at = utcnow()


def store_uploaded_run(db: Session, project: Project, name: str, pipeline: str, config: dict, dataset: str,
                       rows: list[dict]) -> EvalRun:
    """Store a run executed client-side (`ragops test --push`)."""
    run = EvalRun(project_id=project.id, name=name, pipeline=pipeline, config=config or {}, dataset=dataset,
                  total=len(rows), progress=len(rows), status="done", source="cli", summary=summarize(rows),
                  finished_at=utcnow())
    db.add(run)
    db.flush()
    for i, r in enumerate(rows):
        c = EvalRunCase(run_id=run.id, idx=i, query=r["query"], expected_answer=r.get("expected_answer"),
                        expected_doc_ids=r.get("expected_doc_ids") or [])
        _row_to_case({**{"answer": None, "contexts": [], "scores": {}, "label": None, "failed": False, "hit": None,
                         "latency_ms": None, "error": None, "evidence": {}}, **r}, c)
        db.add(c)
    return run


def run_rows(run: EvalRun) -> list[dict]:
    return [{"query": c.query, "answer": c.answer, "contexts": c.contexts, "scores": c.scores or {}, "label": c.label,
             "failed": c.failed, "hit": c.hit, "latency_ms": c.latency_ms, "error": c.error, "evidence": c.evidence,
             "expected_answer": c.expected_answer, "expected_doc_ids": c.expected_doc_ids} for c in run.cases]


def run_dict(run: EvalRun, with_cases: bool = False) -> dict:
    d = {"id": run.id, "name": run.name, "pipeline": run.pipeline, "config": run.config, "dataset": run.dataset,
         "status": run.status, "progress": run.progress, "total": run.total, "summary": run.summary,
         "error": run.error, "source": run.source, "created_at": run.created_at.isoformat(),
         "finished_at": run.finished_at.isoformat() if run.finished_at else None}
    if with_cases:
        d["cases"] = run_rows(run)
    return d


def compare_runs(base: EvalRun, cand: EvalRun, max_drop: float = 0.02) -> dict:
    rep = compare_rows(run_rows(base), run_rows(cand), DEFAULT_METRICS, max_drop)
    return {"base": base.id, "candidate": cand.id, "report": rep.to_dict(),
            "markdown": rep.to_markdown(f"{base.name} → {cand.name}")}
