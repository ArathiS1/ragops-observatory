"""Service layer: everything that touches the database. The API, worker, CLI and demo all call into here."""

from __future__ import annotations

import hashlib
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import numpy as np
from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session, selectinload

from ragops import drift as drift_mod
from ragops import kb_health as kb_mod
from ragops.alerts import deliver, format_root_causes
from ragops.diagnosis import LABEL_HELP, LABELS, CorpusChunk, CorpusView, Thresholds, diagnose
from ragops.embeddings import embed, embed_one
from ragops.eval.calibration import calibrate
from ragops.eval.judges import evaluate, get_judge
from ragops.eval.sampler import decide, stratum_for
from ragops.eval.types import LLM_METRICS, METRICS, ContextChunk, RagSample
from ragops.models import (
    SLO,
    Alert,
    AlertChannel,
    ApiKey,
    AuditLog,
    Chunk,
    ConfigVersion,
    Diagnosis,
    Document,
    Evaluation,
    GoldenCase,
    GoldenSet,
    HumanLabel,
    Job,
    Project,
    RetrievedChunk,
    Trace,
    utcnow,
)
from ragops.redaction import hash_text, redact
from ragops.schemas import AutoGoldenRule, CorpusIn, TraceIn
from ragops.security import generate_key
from ragops.services_chunking import chunk_text
from ragops.slo import evaluate_slo
from ragops.textutil import is_abstention

log = logging.getLogger("ragops")


# ---------------------------------------------------------------------------------------------
# projects, keys, audit
# ---------------------------------------------------------------------------------------------
DEFAULT_ORG = "default"


def get_org(db: Session, ref: str):
    from ragops.models import Organization

    return db.get(Organization, ref) or db.scalar(select(Organization).where(Organization.name == ref))


def get_or_create_org(db: Session, name: str = DEFAULT_ORG, settings: dict | None = None):
    from ragops.models import Organization

    o = db.scalar(select(Organization).where(Organization.name == name))
    if o is None:
        o = Organization(name=name, settings=settings or {})
        db.add(o)
        db.flush()
    return o


def create_project(db: Session, name: str, settings: dict | None = None, org_id: str | None = None) -> Project:
    from ragops.models import DEFAULT_PROJECT_SETTINGS

    if org_id is None:
        org_id = get_or_create_org(db).id
    p = Project(name=name, org_id=org_id, settings={**DEFAULT_PROJECT_SETTINGS, **(settings or {})})
    db.add(p)
    db.flush()
    return p


def get_project_by_name(db: Session, name: str) -> Project | None:
    return db.scalar(select(Project).where(Project.name == name))


def create_api_key(db: Session, project_id: str | None, name: str, role: str, org_id: str | None = None) -> str:
    raw, prefix, h = generate_key()
    db.add(ApiKey(project_id=project_id, org_id=org_id if project_id is None else None, name=name, prefix=prefix,
                  key_hash=h, role=role))
    db.flush()
    return raw


def audit(db: Session, project_id: str | None, actor: str, action: str, detail: dict | None = None) -> None:
    db.add(AuditLog(project_id=project_id, actor=actor, action=action, detail=detail or {}))


def update_settings(db: Session, project: Project, patch: dict) -> dict:
    project.settings = {**(project.settings or {}), **patch}
    return project.settings


# ---------------------------------------------------------------------------------------------
# job queue (durable, DB-backed)
# ---------------------------------------------------------------------------------------------
def enqueue(db: Session, kind: str, payload: dict, delay_s: float = 0) -> Job:
    j = Job(kind=kind, payload=payload, run_after=utcnow() + timedelta(seconds=delay_s))
    db.add(j)
    db.flush()
    if delay_s <= 0:
        from ragops.queue_arq import note_new_job

        note_new_job(db, j.id)  # pushed to Redis after commit when RAGOPS_REDIS_URL is set
    return j


# ---------------------------------------------------------------------------------------------
# config versions
# ---------------------------------------------------------------------------------------------
def get_or_create_config(db: Session, project_id: str, label: str | None, config: dict | None,
                         when: datetime | None = None) -> ConfigVersion | None:
    if not label:
        return None
    cv = db.scalar(select(ConfigVersion).where(ConfigVersion.project_id == project_id, ConfigVersion.label == label))
    if cv is None:
        cv = ConfigVersion(project_id=project_id, label=label, config=config or {}, created_at=when or utcnow())
        db.add(cv)
        db.flush()
    elif when and when < cv.created_at:
        cv.created_at = when
    return cv


# ---------------------------------------------------------------------------------------------
# corpus registry
# ---------------------------------------------------------------------------------------------
def upsert_corpus(db: Session, project: Project, body: CorpusIn, when: datetime | None = None) -> dict:
    when = when or utcnow()
    stats = {"added": 0, "updated": 0, "unchanged": 0, "deleted": 0, "chunks": 0}
    seen = set()
    for d in body.documents:
        seen.add(d.doc_id)
        pieces = [c.text or "" for c in d.chunks] if d.chunks else chunk_text(d.text or "", body.chunk_words)
        # Version = content. Re-chunking the same text (a new chunk size) replaces the chunks but is NOT a new
        # document version, so it does not make past answers look stale.
        h = hashlib.sha256(" ".join(" ".join(pieces).split()).encode()).hexdigest()
        chunk_sig = hashlib.sha256("\x1e".join(pieces).encode()).hexdigest()[:16]
        cur = db.scalar(select(Document).where(Document.project_id == project.id, Document.doc_id == d.doc_id,
                                               Document.is_current.is_(True)))
        if cur is not None and cur.content_hash == h and not cur.deleted:
            if (cur.meta or {}).get("chunk_signature") == chunk_sig:
                stats["unchanged"] += 1
                continue
            db.execute(Chunk.__table__.update().where(Chunk.project_id == project.id, Chunk.doc_id == d.doc_id)
                       .values(is_current=False))
            cur.meta = {**(cur.meta or {}), "chunk_signature": chunk_sig}
            _add_chunks(db, project.id, d, pieces, cur.version)
            stats["rechunked"] = stats.get("rechunked", 0) + 1
            stats["chunks"] += len(pieces)
            continue
        version = 1
        if cur is not None:
            version = cur.version + 1
            cur.is_current = False
            db.execute(Chunk.__table__.update().where(Chunk.project_id == project.id, Chunk.doc_id == d.doc_id)
                       .values(is_current=False))
            stats["updated"] += 1
        else:
            prev = db.scalar(select(func.max(Document.version)).where(Document.project_id == project.id,
                                                                       Document.doc_id == d.doc_id))
            version = (prev or 0) + 1
            stats["added"] += 1
        db.add(Document(project_id=project.id, doc_id=d.doc_id, version=version, title=d.title, content_hash=h,
                        meta={**(d.metadata or {}), "chunk_signature": chunk_sig}, created_at=when))
        _add_chunks(db, project.id, d, pieces, version)
        stats["chunks"] += len(pieces)
    if body.delete_missing:
        for doc in db.scalars(select(Document).where(Document.project_id == project.id, Document.is_current.is_(True),
                                                     Document.deleted.is_(False))):
            if doc.doc_id not in seen:
                delete_document(db, project, doc.doc_id, when)
                stats["deleted"] += 1
    db.flush()
    _corpus_cache.pop(project.id, None)
    return stats


def _add_chunks(db: Session, project_id: str, d, pieces: list[str], version: int) -> None:
    vecs = embed(pieces)
    for i, (txt, v) in enumerate(zip(pieces, vecs)):
        cid = (d.chunks[i].chunk_id if d.chunks and d.chunks[i].chunk_id else f"{d.doc_id}#{i}")
        db.add(Chunk(project_id=project_id, chunk_id=cid, doc_id=d.doc_id, doc_version=version, position=i,
                     text=txt, embedding=[round(float(x), 5) for x in v]))


def delete_document(db: Session, project: Project, doc_id: str, when: datetime | None = None) -> bool:
    cur = db.scalar(select(Document).where(Document.project_id == project.id, Document.doc_id == doc_id,
                                           Document.is_current.is_(True)))
    if cur is None:
        return False
    cur.is_current = False
    db.add(Document(project_id=project.id, doc_id=doc_id, version=cur.version + 1, title=cur.title,
                    content_hash="deleted", deleted=True, is_current=True, created_at=when or utcnow()))
    db.execute(Chunk.__table__.update().where(Chunk.project_id == project.id, Chunk.doc_id == doc_id)
               .values(is_current=False))
    _corpus_cache.pop(project.id, None)
    return True


_corpus_cache: dict[str, tuple[tuple, CorpusView]] = {}


def load_corpus_view(db: Session, project_id: str) -> CorpusView:
    sig = (db.scalar(select(func.count()).select_from(Chunk).where(Chunk.project_id == project_id, Chunk.is_current.is_(True))),
           db.scalar(select(func.max(Document.created_at)).where(Document.project_id == project_id)))
    hit = _corpus_cache.get(project_id)
    if hit and hit[0] == sig:
        return hit[1]
    rows = db.execute(select(Chunk.chunk_id, Chunk.doc_id, Chunk.doc_version, Chunk.position, Chunk.text, Chunk.embedding)
                      .where(Chunk.project_id == project_id, Chunk.is_current.is_(True))).all()
    docs = db.execute(select(Document.doc_id, Document.version, Document.deleted)
                      .where(Document.project_id == project_id, Document.is_current.is_(True))).all()
    chunks = [CorpusChunk(r.chunk_id, r.doc_id, r.doc_version, r.position, r.text) for r in rows]
    emb = np.array([r.embedding for r in rows], dtype=np.float32) if rows else None
    view = CorpusView(chunks, emb, {d.doc_id: d.version for d in docs if not d.deleted},
                      {d.doc_id for d in docs if d.deleted})
    _corpus_cache[project_id] = (sig, view)
    return view


def corpus_for_query(db: Session, project_id: str, query: str, q_emb=None,
                     trace_doc_ids: set[str] | None = None) -> CorpusView:
    """Corpus view for diagnosing one query.

    Small corpora: the whole (cached) corpus. Large corpora (> RAGOPS_INMEMORY_CORPUS_LIMIT chunks): the top-200
    nearest chunks from the vector index (HNSW under pgvector) plus their documents' other chunks, so knowledge-gap,
    retrieval-miss and chunking checks still see the best candidates and their neighbours.
    """
    from ragops.config import get_settings
    from ragops.vectors import nearest_chunks

    n = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.project_id == project_id,
                                                                Chunk.is_current.is_(True))) or 0
    if n <= get_settings().inmemory_corpus_limit:
        return load_corpus_view(db, project_id)
    vec = q_emb if q_emb is not None else embed_one(query)
    cands = nearest_chunks(db, project_id, vec, k=200)
    docs = {c["doc_id"] for c in cands} | set(trace_doc_ids or ())
    rows = db.execute(select(Chunk.chunk_id, Chunk.doc_id, Chunk.doc_version, Chunk.position, Chunk.text, Chunk.embedding)
                      .where(Chunk.project_id == project_id, Chunk.is_current.is_(True), Chunk.doc_id.in_(docs))).all()
    dv = db.execute(select(Document.doc_id, Document.version, Document.deleted)
                    .where(Document.project_id == project_id, Document.is_current.is_(True),
                           Document.doc_id.in_(docs))).all()
    chunks = [CorpusChunk(r.chunk_id, r.doc_id, r.doc_version, r.position, r.text) for r in rows]
    emb = np.array([r.embedding for r in rows], dtype=np.float32) if rows else None
    return CorpusView(chunks, emb, {d.doc_id: d.version for d in dv if not d.deleted},
                      {d.doc_id for d in dv if d.deleted})


def search_corpus(db: Session, project: Project, query: str, k: int = 10) -> dict:
    """What would a perfect retriever have returned? Used by the trace explorer and for debugging retrieval."""
    from ragops.vectors import backend_name, nearest_chunks

    hits = nearest_chunks(db, project.id, embed_one(query), k)
    return {"backend": backend_name(), "query": query, "results": hits}


def org_summary(db: Session, org, days: int = 7) -> dict:
    """Every project in an organisation with its traffic, failure rate and health, for the org overview."""
    out = []
    for p in db.scalars(select(Project).where(Project.org_id == org.id).order_by(Project.name)):
        ov = overview(db, p, days)
        out.append({"id": p.id, "name": p.name, "traces": ov["traces"], "evaluated": ov["evaluated"],
                    "failure_rate": ov["failure_rate"], "top_root_cause": next(iter(ov["root_causes"]), None),
                    "health": ov["health"], "slos_breached": sum(1 for s in ov["slos"] if s["status"] == "breached")})
    return {"id": org.id, "name": org.name, "window_days": days, "projects": out,
            "traces": sum(x["traces"] for x in out)}


# ---------------------------------------------------------------------------------------------
# trace ingestion
# ---------------------------------------------------------------------------------------------
def _clean(project: Project, field: str, text: str | None) -> str | None:
    if text is None:
        return None
    if field in project.setting("redact_fields"):
        text, _ = redact(text, project.setting("redaction_entities"))
    if not project.setting("store_raw_text"):
        return hash_text(text, project.id)
    return text


def _infer_version(db: Session, project_id: str, chunk_id: str | None, text: str | None, cache: dict) -> int | None:
    if not chunk_id or not text:
        return None
    key = (chunk_id, hashlib.md5(text.encode()).hexdigest())
    if key in cache:
        return cache[key]
    v = db.scalar(select(Chunk.doc_version).where(Chunk.project_id == project_id, Chunk.chunk_id == chunk_id,
                                                  Chunk.text == text).order_by(Chunk.doc_version.desc()).limit(1))
    cache[key] = v
    return v


def ingest_trace(db: Session, project: Project, t: TraceIn, enqueue_processing: bool = True) -> Trace:
    if t.id:
        existing = db.get(Trace, t.id)
        if existing is not None:
            return existing
    when = t.timestamp.replace(tzinfo=None) if t.timestamp else utcnow()
    cv = get_or_create_config(db, project.id, t.config_label, t.config, when)
    redacted_query = redact(t.query, project.setting("redaction_entities"))[0] if "query" in project.setting("redact_fields") else t.query
    meta = dict(t.metadata or {})
    if t.expected_answer:
        meta["expected_answer"] = t.expected_answer
    if t.expected_doc_ids:
        meta["expected_doc_ids"] = t.expected_doc_ids
    if t.force_eval:
        meta["force_eval"] = True
    meta["environment"] = (t.environment or meta.get("environment") or "production").strip().lower()[:32]
    cost = t.cost_usd
    if cost is None:
        from ragops.reliability import estimate_cost

        cost, pv = estimate_cost(t.model, t.prompt_tokens, t.completion_tokens, project.setting("pricing"))
        if cost is not None:
            meta.update(cost_estimated=True, pricing_version=pv)
    trace = Trace(
        id=t.id or None, project_id=project.id, config_version_id=cv.id if cv else None,
        query=_clean(project, "query", t.query), query_embedding=embed_one(redacted_query),
        answer=_clean(project, "answer", t.answer), model=t.model, latency_ms=t.latency_ms,
        prompt_tokens=t.prompt_tokens, completion_tokens=t.completion_tokens, cost_usd=cost,
        user_feedback=t.user_feedback, session_id=t.session_id, meta=meta, created_at=when,
        source=meta.pop("_source", "sdk"),
    )
    if trace.id is None:
        from ragops.models import new_id

        trace.id = new_id()
    vcache: dict = {}
    contexts = t.contexts or t.reranked or t.retrieved
    for step, items in (("retrieve", t.retrieved), ("rerank", t.reranked), ("context", contexts)):
        for i, c in enumerate(items):
            dv = c.doc_version if c.doc_version is not None else _infer_version(db, project.id, c.chunk_id, c.text, vcache)
            trace.chunks.append(RetrievedChunk(step=step, rank=c.rank or i + 1, chunk_id=c.chunk_id, doc_id=c.doc_id,
                                               doc_version=dv, score=c.score, text=_clean(project, "chunk_text", c.text)))
    db.add(trace)
    db.flush()
    if enqueue_processing:
        enqueue(db, "process_trace", {"trace_id": trace.id})
    return trace


def add_feedback(db: Session, trace: Trace, score: float, comment: str | None) -> None:
    trace.user_feedback = score
    if comment:
        trace.meta = {**trace.meta, "feedback_comment": comment}
    if score < 0 and trace.eval_status in ("pending", "sampled_out"):
        trace.meta = {**trace.meta, "force_eval": True}
        enqueue(db, "process_trace", {"trace_id": trace.id})


# ---------------------------------------------------------------------------------------------
# evaluation + diagnosis
# ---------------------------------------------------------------------------------------------
def thresholds_for(project: Project) -> Thresholds:
    q = project.setting("quality_thresholds")
    return Thresholds(faithfulness=q.get("faithfulness", 0.7), answer_relevance=q.get("answer_relevance", 0.5),
                      overload_chunks=project.setting("context_overload_chunks"))


def sample_from_trace(t: Trace, corpus: CorpusView | None) -> RagSample:
    def conv(step):
        return [ContextChunk(text=c.text or "", doc_id=c.doc_id, chunk_id=c.chunk_id, doc_version=c.doc_version,
                             score=c.score, rank=c.rank) for c in t.chunks if c.step == step]

    s = RagSample(query=t.query, answer=t.answer, contexts=conv("context"), retrieved=conv("retrieve"),
                  expected_answer=t.meta.get("expected_answer"), expected_doc_ids=t.meta.get("expected_doc_ids") or [])
    if corpus is not None and not corpus.empty:
        s.corpus_max_similarity = float(corpus.relevance(t.query, np.asarray(t.query_embedding, dtype=np.float32)
                                                         if t.query_embedding else None).max())
    return s


def _day_start() -> datetime:
    n = utcnow()
    return datetime(n.year, n.month, n.day)


def process_trace(db: Session, trace_id: str, judge=None) -> str:
    t = db.get(Trace, trace_id, options=[selectinload(Trace.chunks)])
    if t is None:
        return "missing"
    project = db.get(Project, t.project_id)
    judge = judge or get_judge()
    est_cost = judge.cost_per_call * len(LLM_METRICS)
    force = bool(t.meta.get("force_eval"))
    ctx = [c for c in t.chunks if c.step == "context"]
    cv = db.get(ConfigVersion, t.config_version_id) if t.config_version_id else None
    stratum = stratum_for(cv.label if cv else None, ctx[0].score if ctx else None, len(ctx), t.user_feedback)
    if not force:
        day0 = t.created_at.replace(hour=0, minute=0, second=0, microsecond=0)
        spent = db.scalar(select(func.coalesce(func.sum(Evaluation.cost_usd), 0.0)).where(
            Evaluation.project_id == project.id, Evaluation.created_at >= _day_start())) or 0.0
        done_today = db.execute(select(Trace.meta).where(Trace.project_id == project.id, Trace.eval_status == "done",
                                                         Trace.created_at >= day0,
                                                         Trace.created_at < day0 + timedelta(days=1))).scalars().all()
        counts = Counter(m.get("stratum", "?") for m in done_today)
        d = decide(t.id, stratum, t.user_feedback, project.setting("sample_rate"),
                   project.setting("daily_eval_budget_usd"), float(spent), est_cost, dict(counts),
                   project.setting("always_eval_negative_feedback"))
        t.meta = {**t.meta, "stratum": stratum, "sample_reason": d.reason}
        if not d.evaluate:
            t.eval_status = "sampled_out"
            return "sampled_out"
    else:
        t.meta = {**t.meta, "stratum": stratum, "sample_reason": "forced"}

    corpus = corpus_for_query(db, project.id, t.query, t.query_embedding, {c.doc_id for c in t.chunks if c.doc_id})
    sample = sample_from_trace(t, corpus)
    # Release the database before judging. LLM judges take seconds per call (minutes when a free tier rate-limits);
    # keeping this transaction open meanwhile would hold SQLite's write lock and make ingestion fail with HTTP 500.
    db.commit()
    results = evaluate(sample, judge, METRICS)
    db.execute(delete(Evaluation).where(Evaluation.trace_id == t.id))
    for r in results.values():
        db.add(Evaluation(trace_id=t.id, project_id=project.id, metric=r.metric, score=r.score, judge=r.judge,
                          reasoning=r.reasoning, cost_usd=r.cost_usd, created_at=t.created_at))
    scores = {k: v.score for k, v in results.items()}
    from ragops.reliability import claim_evidence, claim_summary, ranking_metrics

    ranked = [c.doc_id for c in (sample.retrieved or sample.contexts)]
    for m, v in ranking_metrics(ranked, sample.expected_doc_ids).items():
        db.add(Evaluation(trace_id=t.id, project_id=project.id, metric=m, score=v, judge="rules",
                          reasoning="ranking against the expected documents", cost_usd=0.0, created_at=t.created_at))
    claims = claim_evidence(t.answer, [{"text": c.text, "chunk_id": c.chunk_id, "doc_id": c.doc_id}
                                       for c in sample.contexts])
    dx = diagnose(sample, scores, corpus, t.user_feedback, thresholds_for(project))
    if claims:
        dx.evidence = {**(dx.evidence or {}), "claims": claims, "claim_summary": claim_summary(claims)}
    db.execute(delete(Diagnosis).where(Diagnosis.trace_id == t.id))
    db.add(Diagnosis(trace_id=t.id, project_id=project.id, label=dx.label, confidence=dx.confidence,
                     evidence=dx.evidence, created_at=t.created_at))
    t.is_failure = dx.is_failure
    t.eval_status = "done"
    db.flush()

    from ragops import clickhouse

    if clickhouse.get_clickhouse() is not None:
        db.expire(t, ["evaluations", "diagnosis"])
        clickhouse.mirror(t, cv.label if cv else "")

    rule = AutoGoldenRule(**(project.settings or {}).get("auto_golden", {"enabled": False}))
    if rule.enabled and dx.is_failure and dx.label in rule.labels and dx.confidence >= rule.min_confidence:
        promote_trace(db, project, t, rule.golden_set, None, None, dx)
    return "done"


# ---------------------------------------------------------------------------------------------
# golden sets
# ---------------------------------------------------------------------------------------------
def fingerprint(query: str) -> str:
    return hashlib.sha256(" ".join(query.lower().split()).encode()).hexdigest()[:32]


def get_or_create_golden(db: Session, project_id: str, name: str) -> GoldenSet:
    g = db.scalar(select(GoldenSet).where(GoldenSet.project_id == project_id, GoldenSet.name == name))
    if g is None:
        g = GoldenSet(project_id=project_id, name=name, version=0)
        db.add(g)
        db.flush()
    return g


def add_golden_case(db: Session, g: GoldenSet, query: str, expected_doc_ids: list[str], expected_answer: str | None,
                    source_trace_id: str | None = None, failure_label: str | None = None) -> GoldenCase | None:
    fp = fingerprint(query)
    if db.scalar(select(GoldenCase).where(GoldenCase.golden_set_id == g.id, GoldenCase.fingerprint == fp)):
        return None  # dedup
    g.version += 1
    case = GoldenCase(golden_set_id=g.id, query=query, expected_doc_ids=expected_doc_ids, expected_answer=expected_answer,
                      source_trace_id=source_trace_id, failure_label=failure_label, fingerprint=fp,
                      added_in_version=g.version)
    db.add(case)
    db.flush()
    return case


def extract_reference_answer(query: str, chunk_texts: list[str]) -> str | None:
    """Draft reference answer: the sentence from the gold chunks that best covers the query. Marked 'extracted' so a
    human can review it; good enough for context_recall and as a starting point."""
    from ragops.textutil import content_tokens, coverage, sentences

    q = content_tokens(query)
    best, score = None, 0.0
    for text in chunk_texts:
        for s in sentences(text):
            c = coverage(q, content_tokens(s))
            if c > score:
                best, score = s, c
    return best if score >= 0.34 else None


def promote_trace(db: Session, project: Project, t: Trace, set_name: str, expected_answer: str | None,
                  expected_doc_ids: list[str] | None, dx=None) -> GoldenCase | None:
    diag = dx or t.diagnosis
    ev = (diag.evidence if diag is not None else {}) or {}
    corpus = load_corpus_view(db, project.id)
    gold = set(ev.get("gold_chunks") or [])
    gold_chunks = [c for c in corpus.chunks if c.chunk_id in gold]
    if expected_doc_ids is None:
        docs = sorted({c.doc_id for c in gold_chunks})
        if not docs and ev.get("best_corpus_match") and diag is not None and diag.label != "knowledge_gap":
            docs = [ev["best_corpus_match"]["doc_id"]]
        expected_doc_ids = docs
    source = "human" if expected_answer else None
    if expected_answer is None and gold_chunks and (diag is None or diag.label != "knowledge_gap"):
        expected_answer = extract_reference_answer(t.query, [c.text for c in gold_chunks])
        source = "extracted" if expected_answer else None
    g = get_or_create_golden(db, project.id, set_name)
    label = diag.label if diag is not None else None
    case = add_golden_case(db, g, t.query, expected_doc_ids, expected_answer, t.id, label)
    if case is not None:
        case.expected_answer_source = source
    return case


def export_golden(db: Session, g: GoldenSet) -> dict:
    cases = db.scalars(select(GoldenCase).where(GoldenCase.golden_set_id == g.id).order_by(GoldenCase.created_at)).all()
    return {"name": g.name, "version": g.version, "cases": [
        {"id": c.id, "query": c.query, "expected_doc_ids": c.expected_doc_ids, "expected_answer": c.expected_answer,
         "expected_answer_source": c.expected_answer_source,
         "failure_label": c.failure_label, "source_trace_id": c.source_trace_id, "added_in_version": c.added_in_version}
        for c in cases]}


# ---------------------------------------------------------------------------------------------
# analytics
# ---------------------------------------------------------------------------------------------
def _since(days: float) -> datetime:
    return utcnow() - timedelta(days=days)


def overview(db: Session, project: Project, days: int = 7) -> dict:
    since = _since(days)
    base = select(Trace).where(Trace.project_id == project.id, Trace.created_at >= since)
    n = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    n_eval = db.scalar(select(func.count()).where(Trace.project_id == project.id, Trace.created_at >= since,
                                                  Trace.eval_status == "done")) or 0
    n_fail = db.scalar(select(func.count()).where(Trace.project_id == project.id, Trace.created_at >= since,
                                                  Trace.is_failure.is_(True))) or 0
    lat = [x for x in db.scalars(select(Trace.latency_ms).where(Trace.project_id == project.id, Trace.created_at >= since,
                                                                 Trace.latency_ms.is_not(None))).all()]
    cost = db.scalar(select(func.coalesce(func.sum(Trace.cost_usd), 0.0)).where(Trace.project_id == project.id,
                                                                                  Trace.created_at >= since)) or 0.0
    judge_cost = db.scalar(select(func.coalesce(func.sum(Evaluation.cost_usd), 0.0)).where(
        Evaluation.project_id == project.id, Evaluation.created_at >= since)) or 0.0
    metrics = {m: (None if v is None else round(float(v), 4)) for m, v in db.execute(
        select(Evaluation.metric, func.avg(Evaluation.score)).where(Evaluation.project_id == project.id,
                                                                    Evaluation.created_at >= since)
        .group_by(Evaluation.metric)).all()}
    rc = root_cause_counts(db, project, days)
    slos = slo_statuses(db, project)
    failure_rate = n_fail / n_eval if n_eval else None
    worst = max(rc.items(), key=lambda kv: kv[1])[0] if rc else None
    breached = [s for s in slos if s["status"] == "breached"]
    at_risk = [s for s in slos if s["status"] == "at_risk"]
    if breached:
        level, msg = "critical", f"{len(breached)} SLO(s) breached: {', '.join(s['name'] for s in breached)}."
    elif at_risk or (failure_rate is not None and failure_rate > 0.2):
        level, msg = "warning", (f"{len(at_risk)} SLO(s) at risk. " if at_risk else "") + (
            f"Failure rate {failure_rate:.0%}." if failure_rate is not None else "")
    elif n_eval == 0:
        level, msg = "info", "No evaluated traffic yet. Send traces with the SDK or OpenTelemetry."
    else:
        level, msg = "healthy", f"All SLOs met. Failure rate {failure_rate:.0%} on {n_eval} evaluated requests."
    if worst and level in ("critical", "warning"):
        msg += f" Most failures are {worst.replace('_', ' ')}: {LABEL_HELP[worst]}"
    return {
        "window_days": days, "traces": n, "evaluated": n_eval, "eval_coverage": round(n_eval / n, 3) if n else None,
        "failures": n_fail, "failure_rate": None if failure_rate is None else round(failure_rate, 4),
        "latency_p50_ms": float(np.percentile(lat, 50)) if lat else None,
        "latency_p95_ms": float(np.percentile(lat, 95)) if lat else None,
        "llm_cost_usd": round(float(cost), 4), "judge_cost_usd": round(float(judge_cost), 4),
        "metrics": metrics, "root_causes": rc, "slos": slos, "health": {"level": level, "message": msg},
    }


def root_cause_counts(db: Session, project: Project, days: int = 7) -> dict[str, int]:
    # Failures only: a correct "I don't know" carries a knowledge_gap label for the knowledge-base to-do list,
    # but it is not a place where the pipeline broke.
    rows = db.execute(select(Diagnosis.label, func.count()).join(Trace, Trace.id == Diagnosis.trace_id)
                      .where(Diagnosis.project_id == project.id, Diagnosis.created_at >= _since(days),
                             Diagnosis.label != "ok", Trace.is_failure.is_(True))
                      .group_by(Diagnosis.label)).all()
    return {lab: int(c) for lab, c in rows}


def root_cause_detail(db: Session, project: Project, days: int = 7) -> dict:
    counts = root_cause_counts(db, project, days)
    total = sum(counts.values())
    daily = db.execute(select(func.date(Diagnosis.created_at), Diagnosis.label, func.count())
                       .where(Diagnosis.project_id == project.id, Diagnosis.created_at >= _since(days),
                              Diagnosis.label != "ok")
                       .group_by(func.date(Diagnosis.created_at), Diagnosis.label)).all()
    examples = {}
    for lab in counts:
        rows = db.execute(select(Trace.id, Trace.query, Diagnosis.confidence).join(Diagnosis, Diagnosis.trace_id == Trace.id)
                          .where(Diagnosis.project_id == project.id, Diagnosis.label == lab,
                                 Diagnosis.created_at >= _since(days))
                          .order_by(Diagnosis.confidence.desc()).limit(5)).all()
        examples[lab] = [{"trace_id": r.id, "query": r.query, "confidence": r.confidence} for r in rows]
    return {
        "total_failures": total,
        "breakdown": [{"label": k, "count": v, "share": round(v / total, 4) if total else 0, "fix": LABEL_HELP[k]}
                      for k, v in sorted(counts.items(), key=lambda kv: -kv[1])],
        "daily": [{"day": str(d), "label": lab, "count": int(c)} for d, lab, c in daily],
        "examples": examples, "labels": LABELS,
    }


def list_traces(db: Session, project: Project, limit: int = 50, offset: int = 0, label: str | None = None,
                failed: bool | None = None, q: str | None = None, environment: str | None = None) -> list[dict]:
    stmt = select(Trace).where(Trace.project_id == project.id).options(selectinload(Trace.diagnosis))
    if environment:
        stmt = stmt.where(Trace.meta["environment"].as_string() == environment.lower())
    if label:
        stmt = stmt.join(Diagnosis, Diagnosis.trace_id == Trace.id).where(Diagnosis.label == label)
    if failed is not None:
        stmt = stmt.where(Trace.is_failure.is_(failed))
    if q:
        stmt = stmt.where(Trace.query.ilike(f"%{q}%"))
    rows = db.scalars(stmt.order_by(Trace.created_at.desc()).limit(limit).offset(offset)).all()
    return [{"id": t.id, "created_at": t.created_at.isoformat(), "query": t.query, "answer": (t.answer or "")[:300],
             "eval_status": t.eval_status, "is_failure": t.is_failure, "latency_ms": t.latency_ms,
             "label": t.diagnosis.label if t.diagnosis else None,
             "confidence": t.diagnosis.confidence if t.diagnosis else None, "source": t.source,
             "environment": (t.meta or {}).get("environment"), "cost_usd": t.cost_usd} for t in rows]


def trace_detail(db: Session, t: Trace) -> dict:
    corpus = corpus_for_query(db, t.project_id, t.query, t.query_embedding, {c.doc_id for c in t.chunks if c.doc_id})
    cv = db.get(ConfigVersion, t.config_version_id) if t.config_version_id else None

    def chunk(c):
        cur = corpus.current_version.get(c.doc_id) if c.doc_id else None
        stale = bool(c.doc_id and ((c.doc_id in corpus.deleted_docs) or (c.doc_version and cur and c.doc_version < cur)))
        return {"step": c.step, "rank": c.rank, "chunk_id": c.chunk_id, "doc_id": c.doc_id, "doc_version": c.doc_version,
                "current_version": cur, "stale": stale, "score": c.score, "text": c.text}

    gold = set((t.diagnosis.evidence or {}).get("gold_chunks", [])) if t.diagnosis else set()
    chunks = [chunk(c) | {"is_gold": c.chunk_id in gold} for c in t.chunks]
    return {
        "id": t.id, "created_at": t.created_at.isoformat(), "query": t.query, "answer": t.answer, "model": t.model,
        "latency_ms": t.latency_ms, "cost_usd": t.cost_usd, "user_feedback": t.user_feedback, "source": t.source,
        "config": {"label": cv.label, "config": cv.config} if cv else None, "metadata": t.meta,
        "eval_status": t.eval_status, "is_failure": t.is_failure, "chunks": chunks,
        "evaluations": [{"metric": e.metric, "score": e.score, "judge": e.judge, "reasoning": e.reasoning}
                        for e in t.evaluations],
        "diagnosis": None if not t.diagnosis else {
            "label": t.diagnosis.label, "confidence": t.diagnosis.confidence, "evidence": t.diagnosis.evidence,
            "fix": LABEL_HELP.get(t.diagnosis.label, "")},
    }


def daily_quality(db: Session, project: Project, days: int = 30) -> dict[str, list[dict]]:
    rows = db.execute(select(func.date(Evaluation.created_at), Evaluation.metric, func.avg(Evaluation.score), func.count())
                      .where(Evaluation.project_id == project.id, Evaluation.created_at >= _since(days),
                             Evaluation.score.is_not(None))
                      .group_by(func.date(Evaluation.created_at), Evaluation.metric)
                      .order_by(func.date(Evaluation.created_at))).all()
    out: dict[str, list[dict]] = defaultdict(list)
    for d, m, avg, n in rows:
        out[m].append({"day": str(d), "mean": round(float(avg), 4), "n": int(n)})
    return dict(out)


def timeline(db: Session, project: Project, days: int = 30) -> dict:
    since = _since(days)
    cfgs = db.scalars(select(ConfigVersion).where(ConfigVersion.project_id == project.id)
                      .order_by(ConfigVersion.created_at)).all()
    docs = db.execute(select(func.date(Document.created_at), Document.version, Document.deleted, func.count())
                      .where(Document.project_id == project.id, Document.created_at >= since)
                      .group_by(func.date(Document.created_at), Document.version, Document.deleted)).all()
    doc_events: dict[str, dict] = defaultdict(lambda: {"added": 0, "changed": 0, "deleted": 0})
    for d, v, deleted, c in docs:
        key = "deleted" if deleted else "added" if v == 1 else "changed"
        doc_events[str(d)][key] += int(c)
    events = [{"time": c.created_at.isoformat(), "kind": "config", "label": c.label, "detail": c.config}
              for c in cfgs if c.created_at >= since]
    events += [{"time": d + "T00:00:00", "kind": "corpus", "label": ", ".join(f"{v} {k}" for k, v in e.items() if v),
                "detail": e} for d, e in sorted(doc_events.items())]
    events.sort(key=lambda e: e["time"])
    failure_daily = db.execute(select(func.date(Trace.created_at), func.count(),
                                      func.sum(case((Trace.is_failure.is_(True), 1), else_=0)))
                               .where(Trace.project_id == project.id, Trace.created_at >= since,
                                      Trace.eval_status == "done")
                               .group_by(func.date(Trace.created_at)).order_by(func.date(Trace.created_at))).all()
    return {"events": events, "quality": daily_quality(db, project, days),
            "failure_rate": [{"day": str(d), "rate": round((f or 0) / n, 4), "n": int(n)} for d, n, f in failure_daily]}


def analytics(db: Session, project: Project, days: int = 30) -> dict:
    """High-volume analytics. Answered by ClickHouse when configured, otherwise by the primary database."""
    from ragops import clickhouse

    ch = clickhouse.get_clickhouse()
    if ch is not None:
        return ch.analytics(project.id, days)
    since = _since(days)
    ov = overview(db, project, days)
    daily_rows = db.execute(select(func.date(Trace.created_at), func.count(),
                                   func.sum(case((Trace.eval_status == "done", 1), else_=0)),
                                   func.sum(case((Trace.is_failure.is_(True), 1), else_=0)))
                            .where(Trace.project_id == project.id, Trace.created_at >= since)
                            .group_by(func.date(Trace.created_at)).order_by(func.date(Trace.created_at))).all()
    lat_by_day: dict[str, list[float]] = defaultdict(list)
    for d, lat in db.execute(select(func.date(Trace.created_at), Trace.latency_ms).where(
            Trace.project_id == project.id, Trace.created_at >= since, Trace.latency_ms.is_not(None))).all():
        lat_by_day[str(d)].append(float(lat))
    daily = [{"day": str(d), "traces": int(n), "evaluated": int(e or 0), "failures": int(f or 0),
              "failure_rate": (round((f or 0) / e, 4) if e else None),
              "latency_p95_ms": float(np.percentile(lat_by_day[str(d)], 95)) if lat_by_day[str(d)] else None}
             for d, n, e, f in daily_rows]
    by_cfg = []
    for label, n, e, f in db.execute(select(ConfigVersion.label, func.count(),
                                            func.sum(case((Trace.eval_status == "done", 1), else_=0)),
                                            func.sum(case((Trace.is_failure.is_(True), 1), else_=0)))
                                     .join(ConfigVersion, ConfigVersion.id == Trace.config_version_id)
                                     .where(Trace.project_id == project.id, Trace.created_at >= since)
                                     .group_by(ConfigVersion.label)).all():
        means = dict(db.execute(select(Evaluation.metric, func.avg(Evaluation.score))
                                .join(Trace, Trace.id == Evaluation.trace_id)
                                .join(ConfigVersion, ConfigVersion.id == Trace.config_version_id)
                                .where(ConfigVersion.label == label, Trace.project_id == project.id,
                                       Trace.created_at >= since, Evaluation.metric.in_(["faithfulness", "answer_relevance"]))
                                .group_by(Evaluation.metric)).all())
        by_cfg.append({"config_label": label, "traces": int(n), "failure_rate": round((f or 0) / max(e or 0, 1), 4),
                       "faithfulness": means.get("faithfulness"), "answer_relevance": means.get("answer_relevance")})
    by_cfg.sort(key=lambda r: -r["traces"])
    return {"backend": "sql", "window_days": days,
            "totals": {k: ov[k] for k in ("traces", "evaluated", "failures", "latency_p50_ms", "latency_p95_ms",
                                          "llm_cost_usd")},
            "daily": daily, "root_causes": ov["root_causes"], "by_config": by_cfg}


# ---------------------------------------------------------------------------------------------
# drift
# ---------------------------------------------------------------------------------------------
def _windows(current_days: int, reference_days: int):
    now = utcnow()
    cur0 = now - timedelta(days=current_days)
    ref0 = cur0 - timedelta(days=reference_days)
    return ref0, cur0, now


def query_drift(db: Session, project: Project, current_days: int = 3, reference_days: int = 7) -> dict:
    ref0, cur0, now = _windows(current_days, reference_days)

    def load(a, b):
        rows = db.execute(select(Trace.query, Trace.query_embedding).where(
            Trace.project_id == project.id, Trace.created_at >= a, Trace.created_at < b,
            Trace.query_embedding.is_not(None)).limit(2000)).all()
        return [r.query for r in rows], np.array([r.query_embedding for r in rows], dtype=np.float32)

    rt, re_ = load(ref0, cur0)
    ct, ce = load(cur0, now)
    corpus = load_corpus_view(db, project.id)
    cov = None
    if not corpus.empty and len(ct):
        cov = [float(corpus.relevance(q, e).max()) for q, e in zip(ct, ce)]
    res = drift_mod.query_drift(re_ if len(re_) else np.zeros((0, 1)), ce if len(ce) else np.zeros((0, 1)), ct, cov)
    return res.to_dict() | {"windows": {"reference": [ref0.isoformat(), cur0.isoformat()],
                                        "current": [cur0.isoformat(), now.isoformat()]}}


def retrieval_drift(db: Session, project: Project, current_days: int = 3, reference_days: int = 7) -> dict:
    ref0, cur0, now = _windows(current_days, reference_days)

    def load(a, b):
        traces = db.scalars(select(Trace).where(Trace.project_id == project.id, Trace.created_at >= a, Trace.created_at < b)
                            .options(selectinload(Trace.chunks)).limit(3000)).all()
        out = []
        for t in traces:
            ctx = [c for c in t.chunks if c.step == "context"]
            scores = [c.score for c in ctx if c.score is not None]
            out.append({"query": t.query, "top1": max(scores) if scores else None, "n": len(ctx),
                        "chunk_ids": [c.chunk_id for c in ctx if c.chunk_id]})
        return out

    return drift_mod.retrieval_drift(load(ref0, cur0), load(cur0, now)).to_dict()


def quality_drift(db: Session, project: Project, days: int = 30, metrics: list[str] | None = None) -> list[dict]:
    metrics = metrics or ["faithfulness", "answer_relevance", "context_relevance"]
    out = []
    for m in metrics:
        rows = db.execute(select(func.date(Evaluation.created_at), Evaluation.score).where(
            Evaluation.project_id == project.id, Evaluation.metric == m, Evaluation.created_at >= _since(days),
            Evaluation.score.is_not(None))).all()
        by_day: dict[str, list[float]] = defaultdict(list)
        for d, s in rows:
            by_day[str(d)].append(float(s))
        days_sorted = sorted(by_day)
        cp = drift_mod.change_point(m, [date.fromisoformat(d) for d in days_sorted],
                                    [float(np.mean(by_day[d])) for d in days_sorted], [by_day[d] for d in days_sorted])
        res = cp.to_dict()
        if cp.detected and cp.change_date:
            # daily buckets: a change point can land on the day before or the day after a mid-day deploy
            lo = datetime.fromisoformat(cp.change_date) - timedelta(days=1)
            hi = datetime.fromisoformat(cp.change_date) + timedelta(days=2)
            cfg = db.scalars(select(ConfigVersion).where(
                ConfigVersion.project_id == project.id, ConfigVersion.created_at >= lo,
                ConfigVersion.created_at < hi).order_by(ConfigVersion.created_at)).all()
            ndocs = db.scalar(select(func.count()).where(
                Document.project_id == project.id,
                Document.created_at >= lo, Document.created_at < hi, Document.version > 1))
            res["likely_causes"] = [f"config '{c.label}' deployed {c.created_at:%Y-%m-%d %H:%M}" for c in cfg] + (
                [f"{ndocs} documents changed around the same time"] if ndocs else [])
        out.append(res)
    return out


def corpus_drift(db: Session, project: Project, days: int = 7) -> dict:
    since = _since(days)
    events = [{"doc_id": d, "version": v} for d, v in db.execute(select(Document.doc_id, Document.version).where(
        Document.project_id == project.id, Document.created_at >= since, Document.deleted.is_(False))).all()]
    usage = [{"trace_id": tid, "doc_id": did, "doc_version": dv} for tid, did, dv in db.execute(
        select(RetrievedChunk.trace_id, RetrievedChunk.doc_id, RetrievedChunk.doc_version)
        .join(Trace, Trace.id == RetrievedChunk.trace_id)
        .where(Trace.project_id == project.id, Trace.created_at >= since, RetrievedChunk.step == "context",
               RetrievedChunk.doc_id.is_not(None))).all()]
    corpus = load_corpus_view(db, project.id)
    deleted_recent = {d for (d,) in db.execute(select(Document.doc_id).where(
        Document.project_id == project.id, Document.deleted.is_(True), Document.is_current.is_(True),
        Document.created_at >= since)).all()}
    return drift_mod.corpus_drift(events, usage, corpus.current_version, deleted_recent | corpus.deleted_docs).to_dict()


def evidently_drift_html(db: Session, project: Project, current_days: int = 3, reference_days: int = 7) -> str:
    """Evidently report comparing the current window with the reference window (per-trace numbers + query embeddings)."""
    from sqlalchemy.orm import selectinload

    from ragops.drift import evidently_report

    now = utcnow()
    cur_start = now - timedelta(days=current_days)
    ref_start = cur_start - timedelta(days=reference_days)

    def rows(a, b):
        ts = db.scalars(select(Trace).where(Trace.project_id == project.id, Trace.created_at >= a, Trace.created_at < b)
                        .options(selectinload(Trace.chunks), selectinload(Trace.evaluations))).all()
        out, emb = [], []
        for t in ts:
            ctx = [c for c in t.chunks if c.step == "context"]
            scores = [c.score for c in ctx if c.score is not None]
            r = {"retrieval_top_score": max(scores) if scores else None, "context_chunks": len(ctx),
                 "latency_ms": t.latency_ms}
            r.update({e.metric: e.score for e in t.evaluations if e.score is not None})
            out.append(r)
            if t.query_embedding is not None:
                emb.append(t.query_embedding)
        return out, (np.array(emb, dtype=np.float32) if len(emb) >= 5 else None)

    ref_rows, ref_emb = rows(ref_start, cur_start)
    cur_rows, cur_emb = rows(cur_start, now + timedelta(seconds=1))
    return evidently_report(ref_rows, cur_rows, None, ref_emb, cur_emb)


def drift_summary(db: Session, project: Project, current_days: int = 3, reference_days: int = 7) -> dict:
    return {"query": query_drift(db, project, current_days, reference_days),
            "retrieval": retrieval_drift(db, project, current_days, reference_days),
            "corpus": corpus_drift(db, project, current_days + reference_days),
            "quality": quality_drift(db, project, current_days + reference_days + 14)}


# ---------------------------------------------------------------------------------------------
# knowledge-base health
# ---------------------------------------------------------------------------------------------
def kb_health(db: Session, project: Project, days: int = 30) -> dict:
    since = _since(days)
    corpus = load_corpus_view(db, project.id)
    retrieved = Counter({cid: int(c) for cid, c in db.execute(
        select(RetrievedChunk.chunk_id, func.count()).join(Trace, Trace.id == RetrievedChunk.trace_id)
        .where(Trace.project_id == project.id, Trace.created_at >= since, RetrievedChunk.step == "context",
               RetrievedChunk.chunk_id.is_not(None)).group_by(RetrievedChunk.chunk_id)).all()})
    failed = Counter({cid: int(c) for cid, c in db.execute(
        select(RetrievedChunk.chunk_id, func.count()).join(Trace, Trace.id == RetrievedChunk.trace_id)
        .join(Diagnosis, Diagnosis.trace_id == Trace.id)
        .where(Trace.project_id == project.id, Trace.created_at >= since, RetrievedChunk.step == "context",
               Diagnosis.label.in_(["generation_failure", "stale_knowledge", "context_overload"]),
               RetrievedChunk.chunk_id.is_not(None)).group_by(RetrievedChunk.chunk_id)).all()})
    gap_q = [q for (q,) in db.execute(select(Trace.query).join(Diagnosis, Diagnosis.trace_id == Trace.id).where(
        Trace.project_id == project.id, Trace.created_at >= since, Diagnosis.label == "knowledge_gap").limit(500)).all()]
    return kb_mod.kb_health(corpus, dict(retrieved), dict(failed), gap_q).to_dict()


# ---------------------------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------------------------
def calibration(db: Session, project: Project) -> list[dict]:
    th = project.setting("quality_thresholds")
    out = []
    metrics = [m for (m,) in db.execute(select(HumanLabel.metric).where(HumanLabel.project_id == project.id).distinct())]
    for m in metrics:
        pairs = db.execute(select(Evaluation.score, HumanLabel.passed).join(
            HumanLabel, (HumanLabel.trace_id == Evaluation.trace_id) & (HumanLabel.metric == Evaluation.metric))
            .where(HumanLabel.project_id == project.id, HumanLabel.metric == m, Evaluation.score.is_not(None))).all()
        res = calibrate(m, [(float(s), bool(p)) for s, p in pairs], th.get(m, 0.5))
        out.append(res.__dict__)
    return out


# ---------------------------------------------------------------------------------------------
# SLOs and alerts
# ---------------------------------------------------------------------------------------------
REFUSAL_EXEMPT_METRICS = {"answer_relevance"}


def correct_refusal_trace_ids(db: Session, project_id: str, trace_ids: set[str]) -> set[str]:
    """Traces whose answer is an abstention that was correct (abstention_quality == 1)."""
    if not trace_ids:
        return set()
    good = set(db.scalars(select(Evaluation.trace_id).where(
        Evaluation.project_id == project_id, Evaluation.metric == "abstention_quality", Evaluation.score >= 1.0,
        Evaluation.trace_id.in_(trace_ids))).all())
    if not good:
        return set()
    return {tid for tid, ans in db.execute(select(Trace.id, Trace.answer).where(Trace.id.in_(good))).all()
            if is_abstention(ans or "")}


def slo_statuses(db: Session, project: Project) -> list[dict]:
    out = []
    for s in db.scalars(select(SLO).where(SLO.project_id == project.id, SLO.enabled.is_(True))).all():
        rows = db.execute(select(Evaluation.score, Evaluation.created_at, Evaluation.trace_id).where(
            Evaluation.project_id == project.id, Evaluation.metric == s.metric, Evaluation.score.is_not(None),
            Evaluation.created_at >= _since(s.window_days))).all()
        if s.metric in REFUSAL_EXEMPT_METRICS:
            # A correct "I don't know" (the corpus does not cover the question) scores 0 on answer relevance by
            # definition, but it is the right behaviour: the failure rule already treats it as a pass, so the SLO
            # must not count it as a bad request either.
            ok_refusals = correct_refusal_trace_ids(db, project.id, {r[2] for r in rows if r[0] is not None})
            rows = [r for r in rows if r[2] not in ok_refusals]
        day_ago = _since(1)
        st = evaluate_slo(s.name, s.metric, s.threshold, s.target, s.window_days, [r[0] for r in rows],
                          [r[0] for r in rows if r[1] >= day_ago])
        out.append(st.to_dict() | {"id": s.id})
    return out


def check_slos_and_alert(db: Session, project: Project) -> list[Alert]:
    sent = []
    channels = db.scalars(select(AlertChannel).where(AlertChannel.project_id == project.id,
                                                     AlertChannel.enabled.is_(True))).all()
    rc = root_cause_counts(db, project, 1) or root_cause_counts(db, project, 7)
    for st in slo_statuses(db, project):
        slo = db.get(SLO, st["id"])
        prev = slo.last_status
        slo.last_status = st["status"]
        if st["status"] in ("breached", "at_risk") and st["status"] != prev:
            sev = "critical" if st["status"] == "breached" else "warning"
            title = f"[RAGOps] {project.name}: SLO '{st['name']}' {st['status'].replace('_', ' ')}"
            body = (f"{st['metric']} >= {st['threshold']} on {st['good_ratio']:.1%} of {st['n']} samples "
                    f"(target {st['target']:.0%}, {st['window_days']}d). Error budget left: {st['budget_remaining']:.0%}. "
                    f"Burn rate 24h: {st['burn_rate_24h']}.\n{format_root_causes(rc)}")
            try:
                inc = incident_context(db, project, title)
            except Exception:  # noqa: BLE001  (an alert must never fail because its context could not be built)
                log.exception("incident context failed")
                inc = None
            if inc:
                body += ("\nRelated signals: " + "; ".join(inc["signals"]) if inc["signals"] else "") + (
                    "\nCheck first: " + " ".join(f"({i}) {s}" for i, s in enumerate(inc["next_checks"], 1)))
            delivered = [c.kind for c in channels if deliver(c.kind, c.target, title, body, sev, {"project": project.name, "slo": st, "root_causes": rc, "incident": inc})]
            a = Alert(project_id=project.id, slo_id=slo.id, severity=sev, title=title, body=body, root_causes=rc,
                      delivered_to=delivered)
            db.add(a)
            sent.append(a)
        elif st["status"] == "ok" and prev in ("breached", "at_risk"):
            db.add(Alert(project_id=project.id, slo_id=slo.id, severity="info",
                         title=f"[RAGOps] {project.name}: SLO '{st['name']}' recovered", body="Back within target.",
                         root_causes={}, delivered_to=[]))
    db.flush()
    return sent


# ---------------------------------------------------------------------------------------------
# retention
# ---------------------------------------------------------------------------------------------
def apply_retention(db: Session, project: Project) -> int:
    cutoff = _since(project.setting("retention_days"))
    ids = db.scalars(select(Trace.id).where(Trace.project_id == project.id, Trace.created_at < cutoff)).all()
    if ids:
        db.execute(delete(Trace).where(Trace.id.in_(ids)))
    return len(ids)


# ---------------------------------------------------------------------------------------------
# 0.8: environments, cost accounting, failure patterns, incidents, data export and deletion
# ---------------------------------------------------------------------------------------------
def environments(db: Session, project: Project, days: int = 30) -> dict[str, int]:
    rows = db.execute(select(Trace.meta["environment"].as_string(), func.count()).where(
        Trace.project_id == project.id, Trace.created_at >= _since(days)).group_by(
        Trace.meta["environment"].as_string())).all()
    return {(e or "production"): int(c) for e, c in rows}


def costs(db: Session, project: Project, days: int = 30, environment: str | None = None) -> dict:
    """Serving cost (your RAG's model calls) and evaluation cost (the judge), by model, version, environment and
    day. Estimated costs (from the price table, when your app sent tokens but no cost) are counted separately."""
    since = _since(days)
    stmt = (select(Trace.created_at, Trace.model, Trace.cost_usd, Trace.prompt_tokens, Trace.completion_tokens,
                   Trace.meta, ConfigVersion.label).outerjoin(ConfigVersion, ConfigVersion.id == Trace.config_version_id)
            .where(Trace.project_id == project.id, Trace.created_at >= since))
    if environment:
        stmt = stmt.where(Trace.meta["environment"].as_string() == environment.lower())
    rows = db.execute(stmt).all()
    by: dict[str, dict] = {"model": {}, "version": {}, "environment": {}, "day": {}}
    total = est = 0.0
    tokens_in = tokens_out = priced = 0
    for r in rows:
        c = r.cost_usd or 0.0
        meta = r.meta or {}
        total += c
        est += c if meta.get("cost_estimated") else 0.0
        priced += r.cost_usd is not None
        tokens_in += r.prompt_tokens or 0
        tokens_out += r.completion_tokens or 0
        for dim, key in (("model", r.model or "unknown"), ("version", r.label or "unversioned"),
                         ("environment", meta.get("environment") or "production"), ("day", r.created_at.date().isoformat())):
            b = by[dim].setdefault(key, {"requests": 0, "cost_usd": 0.0})
            b["requests"] += 1
            b["cost_usd"] += c
    judge = db.execute(select(Evaluation.judge, func.count(), func.coalesce(func.sum(Evaluation.cost_usd), 0.0))
                       .where(Evaluation.project_id == project.id, Evaluation.created_at >= since)
                       .group_by(Evaluation.judge)).all()
    judge_total = float(sum(x[2] for x in judge))
    n = len(rows)
    fmt = lambda d: sorted(({"key": k, "requests": v["requests"], "cost_usd": round(v["cost_usd"], 6),  # noqa: E731
                             "per_request_usd": round(v["cost_usd"] / v["requests"], 6) if v["requests"] else None}
                            for k, v in d.items()), key=lambda x: x["key"])
    from ragops.reliability import price_table

    version, _ = price_table(project.setting("pricing"))
    return {"window_days": days, "requests": n, "priced_requests": priced,
            "serving_cost_usd": round(total, 6), "estimated_share": round(est / total, 4) if total else 0.0,
            "evaluation_cost_usd": round(judge_total, 6), "total_cost_usd": round(total + judge_total, 6),
            "per_request_usd": round(total / priced, 6) if priced else None,
            "tokens": {"input": tokens_in, "output": tokens_out},
            "by_model": fmt(by["model"]), "by_version": fmt(by["version"]), "by_environment": fmt(by["environment"]),
            "daily": fmt(by["day"]),
            "evaluation_by_judge": [{"judge": j, "scores": int(c), "cost_usd": round(float(s), 6)} for j, c, s in judge],
            "pricing_version": version}


def failure_patterns(db: Session, project: Project, days: int = 7, environment: str | None = None) -> dict:
    from ragops.reliability import failure_patterns as group

    stmt = (select(Trace).join(Diagnosis, Diagnosis.trace_id == Trace.id).options(selectinload(Trace.chunks),
                                                                               selectinload(Trace.diagnosis))
            .where(Trace.project_id == project.id, Trace.created_at >= _since(days), Trace.is_failure.is_(True))
            .order_by(Trace.created_at.desc()).limit(2000))
    if environment:
        stmt = stmt.where(Trace.meta["environment"].as_string() == environment.lower())
    traces = db.scalars(stmt).all()
    rows = [{"id": t.id, "query": t.query, "label": t.diagnosis.label if t.diagnosis else "unknown",
             "doc_ids": sorted({c.doc_id for c in t.chunks if c.doc_id and c.step == "context"})} for t in traces]
    pats = group(rows)
    for p in pats:
        p["fix"] = LABEL_HELP.get(p["stage"], "")
    return {"window_days": days, "failures": len(rows), "patterns": pats}


def incident_context(db: Session, project: Project, title: str, days: int = 1) -> dict:
    """What an on-call engineer needs with an alert: related signals, affected traces, recent changes and the
    checks to run first."""
    ov = overview(db, project, days)
    prev = overview(db, project, days * 7)
    signals = []
    for m in ("faithfulness", "answer_relevance", "context_relevance", "context_recall", "answer_correctness"):
        a, b = (ov["metrics"] or {}).get(m), (prev["metrics"] or {}).get(m)
        if a is not None and b is not None and a < b - 0.05:
            signals.append(f"{m.replace('_', ' ')} fell from {b:.2f} to {a:.2f}")
    if ov.get("latency_p95_ms") and prev.get("latency_p95_ms") and ov["latency_p95_ms"] > prev["latency_p95_ms"] * 1.3:
        signals.append(f"p95 latency rose from {prev['latency_p95_ms']:.0f} ms to {ov['latency_p95_ms']:.0f} ms")
    rc = ov.get("root_causes") or {}
    if rc:
        top = max(rc, key=rc.get)
        signals.append(f"most failures are {top.replace('_', ' ')} ({rc[top]})")
    changes = list(timeline(db, project, max(days, 2)).get("events", []))[-5:]
    affected = db.scalars(select(Trace.id).where(Trace.project_id == project.id, Trace.is_failure.is_(True),
                                                Trace.created_at >= _since(days))
                          .order_by(Trace.created_at.desc()).limit(10)).all()
    steps = ["Open the affected traces and compare the retrieved passages with the answer."]
    if changes:
        steps.append("A configuration or document change happened in this window: compare it with the previous "
                     "version (`ragops gate`).")
    if rc:
        steps.append(LABEL_HELP.get(max(rc, key=rc.get), ""))
    steps.append("Check the drift page for new kinds of questions or changed documents.")
    return {"title": title, "signals": signals, "affected_trace_ids": list(affected), "recent_changes": changes,
            "next_checks": [s for s in steps if s]}


def export_project(db: Session, project: Project, limit: int = 100_000) -> dict:
    """Everything stored for a project, for data-portability or subject-access requests."""
    traces = db.scalars(select(Trace).where(Trace.project_id == project.id).options(
        selectinload(Trace.chunks), selectinload(Trace.evaluations), selectinload(Trace.diagnosis))
        .order_by(Trace.created_at).limit(limit)).all()
    docs = db.scalars(select(Document).where(Document.project_id == project.id)).all()
    return {"project": project.name, "settings": project.settings, "exported_at": utcnow().isoformat(),
            "traces": [trace_detail(db, t) for t in traces],
            "documents": [{"doc_id": d.doc_id, "version": d.version, "title": d.title, "is_current": d.is_current,
                           "deleted": d.deleted, "created_at": d.created_at.isoformat()} for d in docs]}


def delete_project_data(db: Session, project: Project, session_id: str | None = None,
                        before: datetime | None = None) -> int:
    """Delete traces (all, one user's session, or older than a date) with their chunks, scores and diagnoses."""
    stmt = select(Trace.id).where(Trace.project_id == project.id)
    if session_id:
        stmt = stmt.where(Trace.session_id == session_id)
    if before:
        stmt = stmt.where(Trace.created_at < before)
    ids = list(db.scalars(stmt).all())
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for model in (Evaluation, Diagnosis, RetrievedChunk):
            db.execute(delete(model).where(model.trace_id.in_(part)))
        db.execute(delete(Trace).where(Trace.id.in_(part)))
    return len(ids)
