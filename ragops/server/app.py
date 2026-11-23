"""RAGOps Observatory REST API (v1). OpenAPI docs at /docs."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from ragops import __version__
from ragops import services as svc
from ragops.config import get_settings
from ragops.db import get_session, init_db
from ragops.models import (
    SLO,
    Alert,
    AlertChannel,
    ApiKey,
    AuditLog,
    Chunk,
    Document,
    EvalRun,
    GoldenSet,
    HumanLabel,
    Project,
    RagPipeline,
    Trace,
    utcnow,
)
from ragops.reports import build_report, to_html, to_markdown
from ragops.schemas import (
    AlertChannelIn,
    ApiKeyIn,
    CorpusIn,
    FeedbackIn,
    GoldenCaseIn,
    GoldenPromoteIn,
    GuardIn,
    HumanLabelIn,
    OrgIn,
    PipelineCheckIn,
    PipelineIn,
    ProjectIn,
    RunIn,
    RunUploadIn,
    SLOIn,
    TraceBatchIn,
    TraceIn,
)
from ragops.security import hash_key, role_allows
from ragops.server import importers, otel

log = logging.getLogger("ragops.api")

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if get_settings().is_dev_admin_key:
        log.warning("Using the development admin key. Set RAGOPS_ADMIN_KEY in production.")
    yield


app = FastAPI(
    lifespan=lifespan,
    title="RAGOps Observatory",
    version=__version__,
    description="Production reliability for RAG: stage-level root-cause diagnosis, four-layer drift detection, "
                "failure-to-golden-set loop and CI release gates.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------------------------------- auth
@dataclass
class Principal:
    role: str
    project_id: str | None  # set => project key, valid for that project only
    name: str
    org_id: str | None = None  # set => organisation key or SSO user, valid for every project in the org

    @property
    def instance_admin(self) -> bool:
        return self.project_id is None and self.org_id is None and self.role == "admin"


def principal(authorization: str | None = Header(None), x_api_key: str | None = Header(None),
              db: Session = Depends(get_session)) -> Principal:
    raw = x_api_key or (authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None)
    if not raw:
        raise HTTPException(401, "missing API key (Authorization: Bearer <key> or X-API-Key)")
    if raw == get_settings().admin_key:
        return Principal("admin", None, "instance-admin")
    if raw.startswith("rgs_"):  # SSO session token
        from ragops.sso import resolve_session

        sess = resolve_session(db, raw)
        if sess is None:
            raise HTTPException(401, "session expired; sign in again")
        return Principal(sess.role, None, f"sso:{sess.email}", sess.org_id)
    key = db.scalar(select(ApiKey).where(ApiKey.prefix == raw[:12], ApiKey.revoked.is_(False)))
    if key is None or key.key_hash != hash_key(raw):
        raise HTTPException(401, "invalid API key")
    return Principal(key.role, key.project_id, f"key:{key.name}", key.org_id)


def resolve(db: Session, who: Principal, ref: str, role: str) -> Project:
    p = db.get(Project, ref) or svc.get_project_by_name(db, ref)
    if p is None:
        raise HTTPException(404, f"project '{ref}' not found")
    if who.project_id is not None and who.project_id != p.id:
        raise HTTPException(403, "key is not valid for this project")
    if who.project_id is None and who.org_id is not None and p.org_id != who.org_id:
        raise HTTPException(403, "project belongs to another organisation")
    if not role_allows(who.role, role):
        raise HTTPException(403, f"role '{who.role}' cannot perform this action (needs {role})")
    return p


def _inline(bg: BackgroundTasks, db: Session) -> None:
    """Single-process mode: process queued jobs after the response. Commit first so the worker sees the rows."""
    db.commit()
    if get_settings().inline_jobs:
        from ragops.worker import drain

        bg.add_task(drain)


# ------------------------------------------------------------------------------------------- meta
@app.get("/health")
def health(db: Session = Depends(get_session)):
    db.execute(select(1))
    return {"status": "ok", "version": __version__}


@app.get("/v1/version")
def version():
    st = get_settings()
    from ragops.vectors import backend_name

    return {"version": __version__, "judge": st.judge, "embedder": st.embedder, "vector_backend": backend_name(),
            "clickhouse": bool(st.clickhouse_url)}


@app.get("/v1/jobs/stats")
def job_stats(who: Principal = Depends(principal)):
    if not who.instance_admin:
        raise HTTPException(403, "instance admin only")
    from ragops.worker import queue_stats

    return queue_stats()


# ------------------------------------------------------------------------------------------- projects & keys
@app.post("/v1/projects", status_code=201)
def create_project(body: ProjectIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Instance admins create projects in any organisation; organisation admins create them in their own."""
    if who.instance_admin:
        org = svc.get_org(db, body.org) if body.org else svc.get_or_create_org(db)
        if org is None:
            raise HTTPException(404, f"organisation '{body.org}' not found")
    elif who.org_id is not None and who.project_id is None and who.role == "admin":
        org = svc.get_org(db, who.org_id)
        if body.org and body.org not in (org.id, org.name):
            raise HTTPException(403, "organisation admins can only create projects in their own organisation")
    else:
        raise HTTPException(403, "instance or organisation admin required")
    if svc.get_project_by_name(db, body.name):
        raise HTTPException(409, "project exists")
    p = svc.create_project(db, body.name, body.settings, org.id)
    key = svc.create_api_key(db, p.id, "default-admin", "admin")
    svc.audit(db, p.id, who.name, "project.create", {"name": body.name, "org": org.name})
    return {"id": p.id, "name": p.name, "org": org.name, "settings": p.settings, "admin_key": key}


@app.get("/v1/projects")
def list_projects(who: Principal = Depends(principal), db: Session = Depends(get_session)):
    from ragops.models import Organization

    stmt = select(Project, Organization.name).join(Organization, Organization.id == Project.org_id, isouter=True)
    if who.project_id is not None:
        stmt = stmt.where(Project.id == who.project_id)
    elif who.org_id is not None:
        stmt = stmt.where(Project.org_id == who.org_id)
    return [{"id": p.id, "name": p.name, "org": o, "created_at": p.created_at.isoformat()}
            for p, o in db.execute(stmt.order_by(Project.name))]


# ------------------------------------------------------------------------------------------- SSO (OIDC)
@app.get("/auth/config", tags=["auth"])
def auth_config():
    """Tells the UI whether to show the SSO button."""
    from ragops import sso

    cfg = sso.config()
    return {"sso": cfg is not None, "provider_name": cfg.provider_name if cfg else None}


@app.get("/auth/oidc/login", tags=["auth"])
def oidc_login(return_to: str | None = None, db: Session = Depends(get_session)):
    from fastapi.responses import RedirectResponse

    from ragops import sso

    try:
        url = sso.login_url(db, return_to)
    except sso.SsoError as e:
        raise HTTPException(e.status, str(e))
    db.commit()
    return RedirectResponse(url, status_code=302)


@app.get("/auth/oidc/callback", tags=["auth"])
def oidc_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                  error_description: str | None = None, db: Session = Depends(get_session)):
    from fastapi.responses import RedirectResponse

    from ragops import sso

    if error:
        raise HTTPException(401, f"identity provider refused the sign-in: {error_description or error}")
    if not code or not state:
        raise HTTPException(400, "missing code or state")
    try:
        target = sso.callback(db, code, state)
    except sso.SsoError as e:
        db.commit()  # the used login state stays deleted
        raise HTTPException(e.status, str(e))
    db.commit()
    return RedirectResponse(target, status_code=302)


@app.post("/auth/oidc/exchange", tags=["auth"])
def oidc_exchange(body: dict, db: Session = Depends(get_session)):
    """Swap the one-time sign-in code from the callback for a session token."""
    from ragops import sso

    try:
        return sso.exchange(db, str(body.get("code", "")))
    except sso.SsoError as e:
        raise HTTPException(e.status, str(e))


@app.get("/auth/me", tags=["auth"])
def me(who: Principal = Depends(principal), db: Session = Depends(get_session)):
    from ragops.models import Organization

    org = db.get(Organization, who.org_id) if who.org_id else None
    scope = "instance" if who.instance_admin else "project" if who.project_id else "organisation"
    return {"name": who.name, "role": who.role, "scope": scope, "org": org.name if org else None,
            "project_id": who.project_id}


@app.post("/auth/logout", tags=["auth"])
def logout(authorization: str | None = Header(None), db: Session = Depends(get_session)):
    from ragops import sso

    raw = (authorization or "").removeprefix("Bearer ").strip()
    return {"signed_out": raw.startswith("rgs_") and sso.logout(db, raw)}


# ------------------------------------------------------------------------------------------- organisations
def _org_access(db: Session, who: Principal, ref: str, role: str):
    org = svc.get_org(db, ref)
    if org is None:
        raise HTTPException(404, f"organisation '{ref}' not found")
    if who.instance_admin:
        return org
    if who.project_id is not None or who.org_id != org.id:
        raise HTTPException(403, "not a member of this organisation")
    if not role_allows(who.role, role):
        raise HTTPException(403, f"role '{who.role}' cannot perform this action (needs {role})")
    return org


@app.post("/v1/orgs", status_code=201)
def create_org(body: OrgIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Create an organisation (instance admin). Returns an organisation admin key, shown once."""
    if not who.instance_admin:
        raise HTTPException(403, "instance admin key required")
    if svc.get_org(db, body.name):
        raise HTTPException(409, "organisation exists")
    org = svc.get_or_create_org(db, body.name, body.settings)
    key = svc.create_api_key(db, None, "org-admin", "admin", org_id=org.id)
    svc.audit(db, None, who.name, "org.create", {"name": body.name})
    return {"id": org.id, "name": org.name, "admin_key": key}


@app.get("/v1/orgs")
def list_orgs(who: Principal = Depends(principal), db: Session = Depends(get_session)):
    from ragops.models import Organization

    if who.instance_admin:
        rows = db.scalars(select(Organization).order_by(Organization.name)).all()
    elif who.org_id:
        rows = [db.get(Organization, who.org_id)]
    else:
        p = db.get(Project, who.project_id)
        rows = [db.get(Organization, p.org_id)] if p and p.org_id else []
    return [{"id": o.id, "name": o.name, "created_at": o.created_at.isoformat()} for o in rows if o]


@app.get("/v1/orgs/{org}")
def org_overview(org: str, days: int = 7, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Every project in the organisation with traffic, failure rate, top root cause and SLO breaches."""
    return svc.org_summary(db, _org_access(db, who, org, "viewer"), days)


@app.post("/v1/orgs/{org}/keys", status_code=201)
def create_org_key(org: str, body: ApiKeyIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    o = _org_access(db, who, org, "admin")
    raw = svc.create_api_key(db, None, body.name, body.role, org_id=o.id)
    svc.audit(db, None, who.name, "org.key.create", {"org": o.name, "name": body.name, "role": body.role})
    return {"key": raw, "role": body.role, "scope": f"organisation {o.name}",
            "note": "store this key now; it is not shown again"}


@app.get("/v1/orgs/{org}/keys")
def list_org_keys(org: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    o = _org_access(db, who, org, "admin")
    return [{"id": k.id, "name": k.name, "role": k.role, "prefix": k.prefix, "revoked": k.revoked,
             "created_at": k.created_at.isoformat()} for k in db.scalars(select(ApiKey).where(ApiKey.org_id == o.id))]


@app.delete("/v1/orgs/{org}/keys/{key_id}")
def revoke_org_key(org: str, key_id: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    o = _org_access(db, who, org, "admin")
    k = db.get(ApiKey, key_id)
    if not k or k.org_id != o.id:
        raise HTTPException(404, "key not found")
    k.revoked = True
    svc.audit(db, None, who.name, "org.key.revoke", {"org": o.name, "name": k.name})
    return {"revoked": key_id}


@app.post("/v1/orgs/{org}/projects/{project}/move")
def move_project(org: str, project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Move a project into an organisation (instance admin)."""
    if not who.instance_admin:
        raise HTTPException(403, "instance admin key required")
    o = svc.get_org(db, org)
    p = db.get(Project, project) or svc.get_project_by_name(db, project)
    if o is None or p is None:
        raise HTTPException(404, "organisation or project not found")
    p.org_id = o.id
    svc.audit(db, p.id, who.name, "project.move", {"org": o.name})
    return {"project": p.name, "org": o.name}


@app.post("/v1/projects/{project}/keys", status_code=201)
def create_key(project: str, body: ApiKeyIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "admin")
    raw = svc.create_api_key(db, p.id, body.name, body.role)
    svc.audit(db, p.id, who.name, "key.create", {"name": body.name, "role": body.role})
    return {"key": raw, "role": body.role, "note": "store this key now; it is not shown again"}


@app.get("/v1/projects/{project}/keys")
def list_keys(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "admin")
    return [{"id": k.id, "name": k.name, "prefix": k.prefix, "role": k.role, "revoked": k.revoked}
            for k in db.scalars(select(ApiKey).where(ApiKey.project_id == p.id))]


@app.delete("/v1/projects/{project}/keys/{key_id}")
def revoke_key(project: str, key_id: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "admin")
    k = db.get(ApiKey, key_id)
    if not k or k.project_id != p.id:
        raise HTTPException(404, "key not found")
    k.revoked = True
    svc.audit(db, p.id, who.name, "key.revoke", {"name": k.name})
    return {"revoked": True}


@app.get("/v1/projects/{project}/settings")
def get_project_settings(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "viewer")
    from ragops.models import DEFAULT_PROJECT_SETTINGS

    return {**DEFAULT_PROJECT_SETTINGS, **(p.settings or {})}


@app.patch("/v1/projects/{project}/settings")
def patch_settings(project: str, patch: dict, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "admin")
    out = svc.update_settings(db, p, patch)
    svc.audit(db, p.id, who.name, "settings.update", patch)
    return out


@app.get("/v1/projects/{project}/audit")
def audit_log(project: str, limit: int = 100, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "admin")
    rows = db.scalars(select(AuditLog).where(AuditLog.project_id == p.id).order_by(AuditLog.id.desc()).limit(limit))
    return [{"at": a.created_at.isoformat(), "actor": a.actor, "action": a.action, "detail": a.detail} for a in rows]


# ------------------------------------------------------------------------------------------- ingestion
@app.post("/v1/projects/{project}/traces", status_code=202)
def ingest(project: str, body: TraceIn, bg: BackgroundTasks, who: Principal = Depends(principal),
           db: Session = Depends(get_session)):
    p = resolve(db, who, project, "ingest")
    t = svc.ingest_trace(db, p, body)
    _inline(bg, db)
    return {"id": t.id, "status": "queued"}


@app.post("/v1/projects/{project}/traces/batch", status_code=202)
def ingest_batch(project: str, body: TraceBatchIn, bg: BackgroundTasks, who: Principal = Depends(principal),
                 db: Session = Depends(get_session)):
    p = resolve(db, who, project, "ingest")
    ids = [svc.ingest_trace(db, p, t).id for t in body.traces]
    _inline(bg, db)
    return {"ids": ids, "count": len(ids)}


@app.post("/v1/projects/{project}/traces/{trace_id}/feedback")
def feedback(project: str, trace_id: str, body: FeedbackIn, bg: BackgroundTasks,
             who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "ingest")
    t = db.get(Trace, trace_id)
    if not t or t.project_id != p.id:
        raise HTTPException(404, "trace not found")
    svc.add_feedback(db, t, body.score, body.comment)
    _inline(bg, db)
    return {"ok": True}


@app.post("/v1/projects/{project}/guard")
def guard_answer(project: str, body: GuardIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Grounding guardrail: check an answer against its passages before the user sees it (see ragops.guard)."""
    from ragops.guard import FALLBACK, check

    resolve(db, who, project, "ingest")
    texts = [c if isinstance(c, str) else str((c or {}).get("text") or (c or {}).get("page_content") or "")
             for c in body.contexts]
    judge = None
    if body.use_llm:
        from ragops.eval.judges import get_judge

        judge = get_judge()
    res = check(body.answer, texts, body.question, judge=judge, policy=body.policy,
                min_supported=body.min_supported, fallback=body.fallback or FALLBACK)
    return res.to_dict()


@app.post("/v1/projects/{project}/traces/{trace_id}/reprocess")
def reprocess(project: str, trace_id: str, bg: BackgroundTasks, who: Principal = Depends(principal),
              db: Session = Depends(get_session)):
    p = resolve(db, who, project, "editor")
    t = db.get(Trace, trace_id)
    if not t or t.project_id != p.id:
        raise HTTPException(404, "trace not found")
    t.meta = {**t.meta, "force_eval": True}
    svc.enqueue(db, "process_trace", {"trace_id": t.id})
    _inline(bg, db)
    return {"queued": True}


@app.post("/otlp/v1/traces")
async def otlp_ingest(request: Request, bg: BackgroundTasks, x_ragops_project: str | None = Header(None),
                      who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """OTLP/HTTP receiver: protobuf (the exporters' default) or JSON, optionally gzip-compressed."""
    ref = who.project_id or x_ragops_project
    if not ref:
        raise HTTPException(400, "use a project key, or send X-Ragops-Project with the admin key")
    p = resolve(db, who, ref, "ingest")
    ctype = request.headers.get("content-type", "")
    raw = await request.body()
    try:
        if "protobuf" in ctype:
            payload = otel.protobuf_to_otlp_json(raw, request.headers.get("content-encoding"))
        else:
            if "gzip" in (request.headers.get("content-encoding") or ""):
                import gzip

                raw = gzip.decompress(raw)
            import json as _json

            payload = _json.loads(raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"could not decode OTLP payload: {type(e).__name__}: {e}")
    traces = otel.assemble(otel.parse_otlp_json(payload))
    ids = [svc.ingest_trace(db, p, t).id for t in traces]
    _inline(bg, db)
    return {"partialSuccess": {}, "rag_traces": len(ids)}


@app.post("/v1/projects/{project}/import/{source}")
async def import_history(project: str, source: str, file: UploadFile, bg: BackgroundTasks,
                         who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "editor")
    records = importers.load_records(await file.read())
    if source == "langfuse":
        traces = importers.from_langfuse(records)
    elif source == "phoenix":
        traces = importers.from_phoenix(records)
    else:
        raise HTTPException(400, "source must be 'langfuse' or 'phoenix'")
    ids = [svc.ingest_trace(db, p, t).id for t in traces]
    svc.audit(db, p.id, who.name, f"import.{source}", {"records": len(records), "traces": len(ids)})
    _inline(bg, db)
    return {"records": len(records), "imported_traces": len(ids)}


# ------------------------------------------------------------------------------------------- corpus
@app.post("/v1/projects/{project}/corpus")
def upsert_corpus(project: str, body: CorpusIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "ingest")
    stats = svc.upsert_corpus(db, p, body)
    svc.audit(db, p.id, who.name, "corpus.upsert", stats)
    return stats


@app.delete("/v1/projects/{project}/corpus/{doc_id:path}")
def delete_doc(project: str, doc_id: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "editor")
    if not svc.delete_document(db, p, doc_id):
        raise HTTPException(404, "document not found")
    svc.audit(db, p.id, who.name, "corpus.delete", {"doc_id": doc_id})
    return {"deleted": doc_id}


@app.get("/v1/projects/{project}/corpus/stats")
def corpus_stats(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "viewer")
    docs = db.scalar(select(func.count()).where(Document.project_id == p.id, Document.is_current.is_(True),
                                                Document.deleted.is_(False)))
    chunks = db.scalar(select(func.count()).where(Chunk.project_id == p.id, Chunk.is_current.is_(True)))
    versions = db.scalar(select(func.count()).where(Document.project_id == p.id))
    return {"documents": docs, "chunks": chunks, "document_versions": versions}


@app.get("/v1/projects/{project}/corpus/search")
def corpus_search(project: str, q: str, k: int = Query(10, le=100), who: Principal = Depends(principal),
                  db: Session = Depends(get_session)):
    """Nearest chunks to a query (pgvector HNSW index on PostgreSQL, exact search otherwise)."""
    return svc.search_corpus(db, resolve(db, who, project, "viewer"), q, k)


# ------------------------------------------------------------------------------------------- read APIs
@app.get("/v1/projects/{project}/traces")
def traces(project: str, limit: int = Query(50, le=500), offset: int = 0, label: str | None = None,
           failed: bool | None = None, q: str | None = None, environment: str | None = None,
           who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "viewer")
    return svc.list_traces(db, p, limit, offset, label, failed, q, environment)


@app.get("/v1/projects/{project}/traces/{trace_id}")
def trace(project: str, trace_id: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "viewer")
    t = db.get(Trace, trace_id, options=[selectinload(Trace.chunks), selectinload(Trace.evaluations),
                                         selectinload(Trace.diagnosis)])
    if not t or t.project_id != p.id:
        raise HTTPException(404, "trace not found")
    return svc.trace_detail(db, t)


@app.get("/v1/projects/{project}/traces/{trace_id}/evidence-graph")
def trace_evidence_graph(project: str, trace_id: str, who: Principal = Depends(principal),
                         db: Session = Depends(get_session)):
    """Documents -> chunks -> answer claims, with supported / contradicted / not-found edges."""
    from ragops import insights

    p = resolve(db, who, project, "viewer")
    t = db.get(Trace, trace_id, options=[selectinload(Trace.chunks), selectinload(Trace.evaluations),
                                         selectinload(Trace.diagnosis)])
    if not t or t.project_id != p.id:
        raise HTTPException(404, "trace not found")
    return insights.evidence_graph(svc.trace_detail(db, t))


@app.get("/v1/projects/{project}/cockpit")
def cockpit(project: str, days: int = 7, environment: str | None = None, who: Principal = Depends(principal),
            db: Session = Depends(get_session)):
    """Overview dashboard: KPI trends vs the previous window, pipeline flow, Hit@K, quality, feedback, alerts."""
    from ragops import insights

    return insights.cockpit(db, resolve(db, who, project, "viewer"), days, environment)


@app.get("/v1/projects/{project}/failure-intelligence")
def failure_intel(project: str, days: int = 7, environment: str | None = None, who: Principal = Depends(principal),
                  db: Session = Depends(get_session)):
    """Recurring failures with trend, first/last seen, likely cause, confidence and evidence."""
    from ragops import insights

    return insights.failure_intelligence(db, resolve(db, who, project, "viewer"), days, environment)


@app.get("/v1/projects/{project}/integrations")
def integrations(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Which integrations are installed and active, and the project's security posture."""
    from ragops import insights

    return insights.integrations(db, resolve(db, who, project, "viewer"))


@app.get("/v1/projects/{project}/overview")
def overview(project: str, days: int = 7, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    return svc.overview(db, resolve(db, who, project, "viewer"), days)


@app.get("/v1/projects/{project}/analytics")
def analytics(project: str, days: int = 30, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Traffic, failure rate, latency and per-config quality. Served by ClickHouse when RAGOPS_CLICKHOUSE_URL is set."""
    return svc.analytics(db, resolve(db, who, project, "viewer"), days)


@app.get("/v1/projects/{project}/root-causes")
def root_causes(project: str, days: int = 7, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    return svc.root_cause_detail(db, resolve(db, who, project, "viewer"), days)


@app.get("/v1/projects/{project}/environments")
def environments(project: str, days: int = 30, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Traffic per environment (dev, staging, production, ...) in the window."""
    return svc.environments(db, resolve(db, who, project, "viewer"), days)


@app.get("/v1/projects/{project}/costs")
def costs(project: str, days: int = 30, environment: str | None = None, who: Principal = Depends(principal),
          db: Session = Depends(get_session)):
    """Serving and evaluation cost by model, version, environment and day (versioned price table)."""
    return svc.costs(db, resolve(db, who, project, "viewer"), days, environment)


@app.get("/v1/projects/{project}/failure-patterns")
def failure_patterns(project: str, days: int = 7, environment: str | None = None,
                     who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Recurring failures grouped by stage and topic, with the documents involved and the fix."""
    return svc.failure_patterns(db, resolve(db, who, project, "viewer"), days, environment)


@app.get("/v1/projects/{project}/incident")
def incident(project: str, days: int = 1, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Current incident context: related signals, affected traces, recent changes and checks to run first."""
    p = resolve(db, who, project, "viewer")
    return svc.incident_context(db, p, f"{p.name}: current status", days)


@app.get("/v1/projects/{project}/export")
def export_data(project: str, limit: int = Query(100_000, le=1_000_000), who: Principal = Depends(principal),
                db: Session = Depends(get_session)):
    """Everything stored for the project (data portability, subject-access requests). Admin only; audited."""
    p = resolve(db, who, project, "admin")
    svc.audit(db, p.id, who.name, "project.export", {"limit": limit})
    db.commit()
    return svc.export_project(db, p, limit)


@app.delete("/v1/projects/{project}/data")
def delete_data(project: str, session_id: str | None = None, before: datetime | None = None, confirm: bool = False,
                who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Delete traces: all of them, one user's session (`session_id`), or everything older than `before`.
    Deleting everything needs `confirm=true`. Admin only; audited."""
    p = resolve(db, who, project, "admin")
    if not session_id and not before and not confirm:
        raise HTTPException(400, "pass session_id, before, or confirm=true to delete every trace")
    n = svc.delete_project_data(db, p, session_id, before.replace(tzinfo=None) if before else None)
    svc.audit(db, p.id, who.name, "project.data.delete",
              {"session_id": session_id, "before": before.isoformat() if before else None, "traces": n})
    return {"deleted_traces": n}


@app.get("/v1/projects/{project}/runs/{run_id}/assurance")
def run_assurance(project: str, run_id: str, format: str = "json", title: str | None = None, brand: str | None = None,
                  who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Assurance report (verdict, criteria, root causes, evidence mapping, fingerprint) for a finished test run."""
    from fastapi.responses import HTMLResponse

    from ragops import __version__
    from ragops.assurance import build, to_html
    from ragops.eval.judges import get_judge
    from ragops.models import EvalRun

    p = resolve(db, who, project, "viewer")
    run = db.get(EvalRun, run_id)
    if run is None or run.project_id != p.id:
        raise HTTPException(404, "run not found")
    if run.status != "done":
        raise HTTPException(409, f"run is {run.status}; the report needs a finished run")
    rows = [{"query": c.query, "answer": c.answer, "expected_answer": c.expected_answer,
             "expected_doc_ids": c.expected_doc_ids, "contexts": c.contexts, "scores": c.scores, "label": c.label,
             "failed": c.failed, "hit": c.hit, "latency_ms": c.latency_ms, "error": c.error, "evidence": c.evidence,
             **({"kind": (c.evidence or {})["kind"]} if (c.evidence or {}).get("kind") else {})}
            for c in run.cases]
    jname = (run.summary or {}).get("judge") or getattr(get_judge(), "name", "heuristic")
    meta = {"pipeline": run.pipeline, "dataset": run.dataset, "config": run.config, "judge": jname,
            "judge_is_llm": jname != "heuristic", "ragops_version": __version__, "run_id": run.id}
    rep = build(rows, run.summary or {}, meta, p.setting("assurance_thresholds") if "assurance_thresholds" in
                (p.settings or {}) else None)
    if format == "html":
        return HTMLResponse(to_html(rep, title=title or f"{p.name}: {run.name}", brand=brand))
    return rep


@app.get("/v1/projects/{project}/timeline")
def timeline(project: str, days: int = 30, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    return svc.timeline(db, resolve(db, who, project, "viewer"), days)


@app.get("/v1/projects/{project}/drift")
def drift(project: str, current_days: int = 3, reference_days: int = 7, who: Principal = Depends(principal),
          db: Session = Depends(get_session)):
    return svc.drift_summary(db, resolve(db, who, project, "viewer"), current_days, reference_days)


@app.get("/v1/projects/{project}/drift/evidently", response_class=HTMLResponse)
def drift_evidently(project: str, current_days: int = 3, reference_days: int = 7, who: Principal = Depends(principal),
                    db: Session = Depends(get_session)):
    """Evidently's own drift report (HTML) for this project's windows. Needs the `evidently` extra."""
    p = resolve(db, who, project, "viewer")
    try:
        return HTMLResponse(svc.evidently_drift_html(db, p, current_days, reference_days))
    except ImportError:
        raise HTTPException(501, 'Evidently is not installed: pip install "ragops-observatory[evidently]"')
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/v1/projects/{project}/drift/{layer}")
def drift_layer(project: str, layer: str, current_days: int = 3, reference_days: int = 7, days: int = 30,
                who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "viewer")
    if layer == "query":
        return svc.query_drift(db, p, current_days, reference_days)
    if layer == "retrieval":
        return svc.retrieval_drift(db, p, current_days, reference_days)
    if layer == "quality":
        return svc.quality_drift(db, p, days)
    if layer == "corpus":
        return svc.corpus_drift(db, p, current_days + reference_days)
    raise HTTPException(404, "layer must be query | retrieval | quality | corpus")


@app.get("/v1/projects/{project}/kb-health")
def kb_health(project: str, days: int = 30, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    return svc.kb_health(db, resolve(db, who, project, "viewer"), days)


# ------------------------------------------------------------------------------------------- calibration
@app.post("/v1/projects/{project}/labels")
def add_label(project: str, body: HumanLabelIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "editor")
    db.execute(delete(HumanLabel).where(HumanLabel.trace_id == body.trace_id, HumanLabel.metric == body.metric))
    db.add(HumanLabel(project_id=p.id, trace_id=body.trace_id, metric=body.metric, passed=body.passed,
                      labeler=body.labeler or who.name))
    return {"ok": True}


@app.get("/v1/projects/{project}/calibration")
def calibration(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    return svc.calibration(db, resolve(db, who, project, "viewer"))


# ------------------------------------------------------------------------------------------- golden sets
@app.get("/v1/projects/{project}/golden-sets")
def golden_sets(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "viewer")
    return [{"name": g.name, "version": g.version, "cases": len(g.cases), "created_at": g.created_at.isoformat()}
            for g in db.scalars(select(GoldenSet).where(GoldenSet.project_id == p.id))]


@app.post("/v1/projects/{project}/golden/promote")
def promote(project: str, body: GoldenPromoteIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "editor")
    t = db.get(Trace, body.trace_id)
    if not t or t.project_id != p.id:
        raise HTTPException(404, "trace not found")
    case = svc.promote_trace(db, p, t, body.golden_set, body.expected_answer, body.expected_doc_ids)
    svc.audit(db, p.id, who.name, "golden.promote", {"trace_id": t.id, "set": body.golden_set})
    if case is None:
        return {"added": False, "reason": "duplicate query already in golden set"}
    return {"added": True, "case_id": case.id, "expected_doc_ids": case.expected_doc_ids,
            "golden_set_version": case.added_in_version}


@app.post("/v1/projects/{project}/golden-sets/{name}/cases")
def add_case(project: str, name: str, body: GoldenCaseIn, who: Principal = Depends(principal),
             db: Session = Depends(get_session)):
    p = resolve(db, who, project, "editor")
    g = svc.get_or_create_golden(db, p.id, name)
    c = svc.add_golden_case(db, g, body.query, body.expected_doc_ids, body.expected_answer)
    return {"added": c is not None, "version": g.version}


@app.get("/v1/projects/{project}/golden-sets/{name}/export")
def export_golden(project: str, name: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "viewer")
    g = db.scalar(select(GoldenSet).where(GoldenSet.project_id == p.id, GoldenSet.name == name))
    if g is None:
        raise HTTPException(404, "golden set not found")
    return svc.export_golden(db, g)


# ------------------------------------------------------------------------------------------- bring your own RAG
@app.get("/v1/projects/{project}/pipelines")
def pipelines(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    from ragops.models import RagPipeline
    from ragops.run_service import public_pipeline

    p = resolve(db, who, project, "viewer")
    return [public_pipeline(x) for x in db.scalars(select(RagPipeline).where(RagPipeline.project_id == p.id))]


@app.post("/v1/projects/{project}/pipelines", status_code=201)
def register_pipeline(project: str, body: PipelineIn, who: Principal = Depends(principal),
                      db: Session = Depends(get_session)):
    """Register your RAG system's HTTP endpoint so it can be tested from the UI and API."""
    from ragops.pipelines import PipelineError, check_url_allowed
    from ragops.run_service import public_pipeline, upsert_pipeline

    p = resolve(db, who, project, "editor")
    try:
        check_url_allowed(body.url)
    except PipelineError as e:
        raise HTTPException(400, str(e))
    spec = body.model_dump(exclude={"name", "default_config", "description"}) | {"kind": "http"}
    pipe = upsert_pipeline(db, p, body.name, spec, body.default_config, body.description)
    svc.audit(db, p.id, who.name, "pipeline.upsert", {"name": body.name, "url": body.url})
    return public_pipeline(pipe)


@app.delete("/v1/projects/{project}/pipelines/{name}")
def delete_pipeline(project: str, name: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    from ragops.models import RagPipeline

    p = resolve(db, who, project, "editor")
    db.execute(delete(RagPipeline).where(RagPipeline.project_id == p.id, RagPipeline.name == name))
    svc.audit(db, p.id, who.name, "pipeline.delete", {"name": name})
    return {"deleted": name}


@app.post("/v1/projects/{project}/pipelines/{name}/check")
def check_pipeline(project: str, name: str, body: PipelineCheckIn, who: Principal = Depends(principal),
                   db: Session = Depends(get_session)):
    """Send one question to the registered endpoint and show what RAGOps parsed + how it scores. Use this to verify
    the field mapping before running a full test."""
    from ragops.models import RagPipeline
    from ragops.run_service import build_pipeline
    from ragops.runs import run_cases

    p = resolve(db, who, project, "editor")
    pipe = db.scalar(select(RagPipeline).where(RagPipeline.project_id == p.id, RagPipeline.name == name))
    if pipe is None:
        raise HTTPException(404, "pipeline not found")
    try:
        fn = build_pipeline(pipe)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    row = run_cases(fn, [{"query": body.query}], {**pipe.default_config, **body.config}, corpus=svc.load_corpus_view(db, p.id))[0]
    return row


@app.get("/v1/projects/{project}/runs")
def runs(project: str, limit: int = 50, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    from ragops.models import EvalRun
    from ragops.run_service import run_dict

    p = resolve(db, who, project, "viewer")
    rows = db.scalars(select(EvalRun).where(EvalRun.project_id == p.id).order_by(EvalRun.created_at.desc()).limit(limit))
    return [run_dict(r) for r in rows]


@app.post("/v1/projects/{project}/runs", status_code=202)
def start_run(project: str, body: RunIn, bg: BackgroundTasks, who: Principal = Depends(principal),
              db: Session = Depends(get_session)):
    """Test a registered pipeline on a golden set or uploaded cases. Runs in the worker; poll GET /runs/{id}."""
    from ragops.models import RagPipeline
    from ragops.run_service import create_run, run_dict

    p = resolve(db, who, project, "editor")
    pipe = db.scalar(select(RagPipeline).where(RagPipeline.project_id == p.id, RagPipeline.name == body.pipeline))
    if pipe is None:
        raise HTTPException(404, f"pipeline '{body.pipeline}' not registered")
    try:
        run = create_run(db, p, pipe, {"golden_set": body.golden_set, "cases": [c.model_dump() for c in body.cases]},
                         body.config, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    svc.audit(db, p.id, who.name, "run.start", {"pipeline": body.pipeline, "dataset": run.dataset})
    out = run_dict(run)
    _inline(bg, db)
    return out


@app.post("/v1/projects/{project}/runs/upload", status_code=201)
def upload_run(project: str, body: RunUploadIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Store a run executed locally with `ragops test --push`."""
    from ragops.run_service import run_dict, store_uploaded_run

    p = resolve(db, who, project, "editor")
    return run_dict(store_uploaded_run(db, p, body.name, body.pipeline, body.config, body.dataset, body.rows))


@app.get("/v1/projects/{project}/runs/benchmark")
def runs_benchmark(project: str, ids: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Benchmark lab: 2 to 5 finished runs side by side (comma-separated ids)."""
    from ragops import insights
    from ragops.models import EvalRun

    p = resolve(db, who, project, "viewer")
    wanted = [i for i in ids.split(",") if i][:5]
    runs = [r for r in (db.get(EvalRun, i, options=[selectinload(EvalRun.cases)]) for i in wanted)
            if r is not None and r.project_id == p.id and r.status == "done"]
    if not runs:
        raise HTTPException(404, "no finished runs with these ids")
    return insights.benchmark(runs)


def _two_runs(db, p, base: str, candidate: str):
    from ragops.models import EvalRun

    out = []
    for rid in (base, candidate):
        r = db.get(EvalRun, rid, options=[selectinload(EvalRun.cases)])
        if r is None or r.project_id != p.id:
            raise HTTPException(404, f"run {rid} not found")
        if r.status != "done":
            raise HTTPException(409, f"run {r.name} is {r.status}")
        out.append(r)
    return out


@app.get("/v1/projects/{project}/release/check")
def release_check(project: str, base: str, candidate: str, who: Principal = Depends(principal),
                  db: Session = Depends(get_session)):
    """Release guardian: Pass / Review / Block for a candidate run against the current one, with every gate and a
    case-by-case diff. Gates come from the project setting `release_gates`."""
    from ragops import insights

    p = resolve(db, who, project, "viewer")
    b, c = _two_runs(db, p, base, candidate)
    return insights.release_check(b, c, insights.gates_for(p))


@app.get("/v1/projects/{project}/release/gates")
def release_gates(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    from ragops import insights

    return insights.gates_for(resolve(db, who, project, "viewer"))


@app.put("/v1/projects/{project}/release/gates")
def set_release_gates(project: str, body: dict, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Agreed quality gates. Unknown keys are rejected so a typo cannot silently disable a gate."""
    from ragops import insights

    p = resolve(db, who, project, "admin")
    bad = set(body) - set(insights.DEFAULT_GATES)
    if bad:
        raise HTTPException(422, f"unknown gate(s): {', '.join(sorted(bad))}")
    svc.update_settings(db, p, {"release_gates": {**((p.settings or {}).get("release_gates") or {}), **body}})
    svc.audit(db, p.id, who.name, "release.gates", body)
    return insights.gates_for(p)


@app.post("/v1/projects/{project}/release/decisions", status_code=201)
def release_decide(project: str, body: dict, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Record approve / reject for a candidate. Approving a blocked release is an override: admin only, reason
    required. Every decision lands in the audit log with the gate results it was made on."""
    from ragops import insights

    p = resolve(db, who, project, "editor")
    decision = body.get("decision")
    if decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    b, c = _two_runs(db, p, body.get("base", ""), body.get("candidate", ""))
    chk = insights.release_check(b, c, insights.gates_for(p))
    override = decision == "approve" and chk["decision"] != "pass"
    reason = (body.get("reason") or "").strip()
    if override and not reason:
        raise HTTPException(422, f"the guardian says {chk['decision']}: approving needs a reason")
    if override and chk["decision"] == "block" and not role_allows(who.role, "admin"):
        raise HTTPException(403, "only an admin can approve a blocked release")
    detail = {"base": b.id, "base_name": b.name, "candidate": c.id, "candidate_name": c.name, "decision": decision,
              "guardian": chk["decision"], "override": override, "reason": reason,
              "failed_gates": [x["gate"] for x in chk["checks"] if not x["passed"]],
              "pass_rate": chk["candidate"]["summary"].get("pass_rate")}
    svc.audit(db, p.id, who.name, "release.decision", detail)
    from ragops.models import utcnow

    return {"at": utcnow().isoformat(), "actor": who.name, **detail}


@app.get("/v1/projects/{project}/release/decisions")
def release_history(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    from ragops import insights

    return insights.release_decisions(db, resolve(db, who, project, "viewer"))


@app.get("/v1/projects/{project}/runs/compare")
def compare(project: str, base: str, candidate: str, max_drop: float = 0.02, who: Principal = Depends(principal),
            db: Session = Depends(get_session)):
    from ragops.models import EvalRun
    from ragops.run_service import compare_runs

    p = resolve(db, who, project, "viewer")
    a, b = db.get(EvalRun, base), db.get(EvalRun, candidate)
    if not a or not b or a.project_id != p.id or b.project_id != p.id:
        raise HTTPException(404, "run not found")
    return compare_runs(a, b, max_drop)


@app.get("/v1/projects/{project}/runs/{run_id}/doctor")
def run_doctor(project: str, run_id: str, format: str = "json", who: Principal = Depends(principal),
               db: Session = Depends(get_session)):
    """RAG doctor for a test run: verdict, mistakes by cause with examples and fixes, plus the results of any
    treatment trials started with POST .../doctor/trial (each trial is a normal run of the same pipeline)."""
    from ragops import doctor as dr
    from ragops.run_service import run_rows

    p = resolve(db, who, project, "viewer")
    run = db.scalar(select(EvalRun).where(EvalRun.id == run_id, EvalRun.project_id == p.id))
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != "done":
        raise HTTPException(409, f"run is {run.status}; the doctor needs a finished run")
    base = run_rows(run)
    rep = dr.diagnose_rows(base)
    pending = []
    for t in (run.summary or {}).get("doctor_trials", []):
        cand = db.get(EvalRun, t["run_id"])
        if cand is None:
            continue
        if cand.status != "done":
            pending.append({"change": t["change"], "run_id": cand.id, "status": cand.status,
                            "progress": cand.progress, "total": cand.total})
            continue
        rep.trials.append(dr.trial_from_rows(t["change"], base, run_rows(cand)))
    rep.best_fix = dr.pick_best(rep.trials)
    if rep.best_fix:
        ch = ", ".join(f"{k}={v}" for k, v in rep.best_fix["change"].items())
        rep.next_steps.insert(0, f"Apply `{ch}` to your pipeline config — it was verified on this test set.")
    if format == "md":
        return Response(rep.to_markdown(f"RAG doctor: {run.name}"), media_type="text/markdown")
    out = rep.to_dict()
    out["pending_trials"] = pending
    out["proposed_changes"] = dr.propose_changes(rep, run.config or {}) if run.source != "cli" else []
    out["missing_knobs"] = dr.missing_knobs(rep, run.config or {})
    return out


@app.post("/v1/projects/{project}/runs/{run_id}/doctor/trial", status_code=202)
def run_doctor_trial(project: str, run_id: str, bg: BackgroundTasks, body: dict | None = None,
                     who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Test candidate fixes on a registered (HTTP) pipeline: one new run per config change, same cases.
    Body: {"changes": [{"top_k": 10}, {"chunk_size": 512}]}  – omit to use the doctor's proposals."""
    from ragops import doctor as dr
    from ragops.run_service import create_run, run_rows

    p = resolve(db, who, project, "editor")
    run = db.scalar(select(EvalRun).where(EvalRun.id == run_id, EvalRun.project_id == p.id))
    if not run or run.status != "done":
        raise HTTPException(404 if not run else 409, "run not found" if not run else "run is not finished")
    pipe = db.scalar(select(RagPipeline).where(RagPipeline.project_id == p.id, RagPipeline.name == run.pipeline))
    if pipe is None:
        raise HTTPException(422, "treatment trials need a pipeline registered on the server; for Python pipelines "
                                 "run `ragops doctor` locally")
    changes = (body or {}).get("changes") or dr.propose_changes(dr.diagnose_rows(run_rows(run)), run.config or {})
    if not changes:
        raise HTTPException(422, "nothing to try: pass {\"changes\": [{...}]} or expose knobs like top_k / "
                                 "chunk_size in the pipeline's default config")
    cases = [{"query": c.query, "expected_answer": c.expected_answer, "expected_doc_ids": c.expected_doc_ids}
             for c in run.cases]
    started = []
    for ch in changes[:8]:
        label = ", ".join(f"{k}={v}" for k, v in ch.items())
        cand = create_run(db, p, pipe, {"name": run.dataset, "cases": cases}, {**(run.config or {}), **ch},
                          f"doctor trial: {label}")
        started.append({"change": ch, "run_id": cand.id})
    run.summary = {**(run.summary or {}), "doctor_trials": [*(run.summary or {}).get("doctor_trials", []), *started]}
    svc.audit(db, p.id, who.name, "doctor.trial", {"run": run.id, "changes": changes[:8]})
    _inline(bg, db)
    return {"trials": started}


@app.get("/v1/projects/{project}/doctor")
def production_doctor(project: str, days: int = 7, format: str = "json", who: Principal = Depends(principal),
                      db: Session = Depends(get_session)):
    """RAG doctor for production traffic: evaluated traces of the last `days`, mistakes by cause, examples, fixes."""
    from ragops import doctor as dr

    p = resolve(db, who, project, "viewer")
    since = utcnow() - timedelta(days=days)
    traces = db.scalars(select(Trace).where(Trace.project_id == p.id, Trace.created_at >= since,
                                            Trace.eval_status == "done").order_by(Trace.created_at.desc()).limit(5000)).all()
    rows = []
    for t in traces:
        scores = {e.metric: e.score for e in t.evaluations}
        dx = t.diagnosis
        rows.append({"query": t.query, "answer": t.answer, "scores": scores, "failed": bool(t.is_failure),
                     "label": dx.label if dx else ("ok" if not t.is_failure else "unknown"),
                     "evidence": dx.evidence if dx else {}, "error": None, "hit": None,
                     "latency_ms": t.latency_ms, "expected_answer": None})
    rep = dr.diagnose_rows(rows)
    if format == "md":
        return Response(rep.to_markdown(f"RAG doctor: {p.name}, last {days} days"), media_type="text/markdown")
    return rep.to_dict()


@app.get("/v1/projects/{project}/runs/{run_id}")
def run_detail(project: str, run_id: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    from ragops.models import EvalRun
    from ragops.run_service import run_dict

    p = resolve(db, who, project, "viewer")
    r = db.get(EvalRun, run_id)
    if not r or r.project_id != p.id:
        raise HTTPException(404, "run not found")
    return run_dict(r, with_cases=True)


# ------------------------------------------------------------------------------------------- SLOs & alerts
@app.get("/v1/projects/{project}/slos")
def slos(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    return svc.slo_statuses(db, resolve(db, who, project, "viewer"))


@app.post("/v1/projects/{project}/slos", status_code=201)
def create_slo(project: str, body: SLOIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "editor")
    s = SLO(project_id=p.id, **body.model_dump())
    db.add(s)
    svc.audit(db, p.id, who.name, "slo.create", body.model_dump())
    db.flush()
    return {"id": s.id}


@app.delete("/v1/projects/{project}/slos/{slo_id}")
def delete_slo(project: str, slo_id: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "editor")
    db.execute(delete(SLO).where(SLO.id == slo_id, SLO.project_id == p.id))
    return {"deleted": slo_id}


@app.post("/v1/projects/{project}/slos/check")
def check_slos(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "editor")
    sent = svc.check_slos_and_alert(db, p)
    return {"alerts": [{"title": a.title, "delivered_to": a.delivered_to} for a in sent],
            "slos": svc.slo_statuses(db, p)}


@app.get("/v1/projects/{project}/alerts")
def alerts(project: str, limit: int = 50, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "viewer")
    rows = db.scalars(select(Alert).where(Alert.project_id == p.id).order_by(Alert.id.desc()).limit(limit))
    return [{"at": a.created_at.isoformat(), "severity": a.severity, "title": a.title, "body": a.body,
             "root_causes": a.root_causes, "delivered_to": a.delivered_to} for a in rows]


@app.post("/v1/projects/{project}/alert-channels", status_code=201)
def add_channel(project: str, body: AlertChannelIn, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "admin")
    c = AlertChannel(project_id=p.id, kind=body.kind, target=body.target)
    db.add(c)
    db.flush()
    svc.audit(db, p.id, who.name, "alert_channel.create", {"kind": body.kind})
    return {"id": c.id}


@app.get("/v1/projects/{project}/alert-channels")
def channels(project: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "admin")
    return [{"id": c.id, "kind": c.kind, "target": c.target[:24] + "…", "enabled": c.enabled}
            for c in db.scalars(select(AlertChannel).where(AlertChannel.project_id == p.id))]


@app.post("/v1/projects/{project}/alert-channels/{channel_id}/test")
def test_channel(project: str, channel_id: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    """Send a real test message through one channel and report what the receiver said."""
    from ragops.alerts import send, test_alert

    p = resolve(db, who, project, "admin")
    c = db.scalar(select(AlertChannel).where(AlertChannel.id == channel_id, AlertChannel.project_id == p.id))
    if not c:
        raise HTTPException(404, "alert channel not found")
    title, body, payload = test_alert(p.name)
    res = send(c.kind, c.target, title, body, "info", payload)
    svc.audit(db, p.id, who.name, "alert_channel.test", {"id": c.id, "ok": res.ok})
    return {"ok": res.ok, "kind": c.kind, "detail": res.detail}


@app.delete("/v1/projects/{project}/alert-channels/{channel_id}")
def delete_channel(project: str, channel_id: str, who: Principal = Depends(principal), db: Session = Depends(get_session)):
    p = resolve(db, who, project, "admin")
    db.execute(delete(AlertChannel).where(AlertChannel.id == channel_id, AlertChannel.project_id == p.id))
    svc.audit(db, p.id, who.name, "alert_channel.delete", {"id": channel_id})
    return {"deleted": channel_id}


# ------------------------------------------------------------------------------------------- reports
@app.get("/v1/projects/{project}/report")
def report(project: str, days: int = 7, format: str = "md", who: Principal = Depends(principal),
           db: Session = Depends(get_session)):
    r = build_report(db, resolve(db, who, project, "viewer"), days)
    if format == "json":
        return r
    if format == "html":
        return HTMLResponse(to_html(r))
    if format == "pdf":
        from fastapi.responses import Response

        from ragops.report_pdf import to_pdf

        return Response(to_pdf(r), media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{r["project"]}-reliability-{r["generated_at"][:10]}.pdf"'})
    return PlainTextResponse(to_markdown(r), media_type="text/markdown")


# ------------------------------------------------------------------------------------------- demo RAG endpoint
@app.post("/demo/rag/ask", include_in_schema=False)
async def demo_rag(request: Request):
    """The demo Nimbus help-centre RAG, exposed over HTTP so new users can try "Test your RAG" end to end.
    Disable with RAGOPS_DEMO_ENDPOINT=false."""
    import os

    if os.environ.get("RAGOPS_DEMO_ENDPOINT", "true").lower() == "false":
        raise HTTPException(404, "demo endpoint disabled")
    from ragops.demo.pipeline import run

    body = await request.json()
    return run(str(body.get("query", "")), body.get("config") or {})


# ------------------------------------------------------------------------------------------- web UI
def _ui_dir():
    """Built React UI: RAGOPS_UI_DIR, the copy bundled in the package (Docker), or web/dist in a source checkout."""
    import os
    from pathlib import Path

    here = Path(__file__).resolve()
    for cand in (os.environ.get("RAGOPS_UI_DIR"), here.parent / "ui", here.parents[2] / "web" / "dist"):
        if cand and (Path(cand) / "index.html").exists():
            return Path(cand)
    return None


_UI = _ui_dir()
if _UI is not None:
    from fastapi.responses import RedirectResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/ui", StaticFiles(directory=str(_UI), html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def _root():
        return RedirectResponse("/ui/")
