"""RAG-native data model.

Query -> RetrievedChunk(step=retrieve|rerank|context) -> Generation (answer) -> Evaluation / Diagnosis / Feedback
Every trace is pinned to a ConfigVersion (embedding model, chunking, prompt, LLM, index version) so any
quality change can be traced back to the change that caused it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ragops.vectors import EmbeddingType


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


DEFAULT_PROJECT_SETTINGS = {
    "sample_rate": 0.2,  # fraction of normal traffic evaluated online
    "daily_eval_budget_usd": 5.0,  # judge spend cap per day
    "always_eval_negative_feedback": True,
    "redact_fields": ["query", "answer", "chunk_text"],
    "redaction_entities": ["EMAIL", "PHONE", "CREDIT_CARD", "SSN", "IP_ADDRESS", "IBAN"],
    "store_raw_text": True,  # False => store salted hashes only
    "retention_days": 90,
    "quality_thresholds": {"faithfulness": 0.7, "answer_relevance": 0.5, "context_relevance": 0.3},
    "context_overload_chunks": 8,
    # USD per 1M tokens, extends the built-in table: {"version": "2026-11", "models": {"my-model": [0.5, 1.5]}}
    "pricing": {},
}


class Organization(Base):
    """A company or business unit. Owns projects; organisation keys and SSO users act across all its projects."""

    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SsoSession(Base):
    """A signed-in SSO user. The bearer token (rgs_...) is shown to the browser once; only its hash is stored."""

    __tablename__ = "sso_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    token_hash: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class OidcLoginState(Base):
    """In-flight OIDC login (state, nonce, PKCE verifier). Stored server-side so any replica can finish the login."""

    __tablename__ = "oidc_login_states"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    return_to: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    org_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True,
                                               index=True)
    settings: Mapped[dict] = mapped_column(JSON, default=lambda: dict(DEFAULT_PROJECT_SETTINGS))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def setting(self, key: str):
        merged = {**DEFAULT_PROJECT_SETTINGS, **(self.settings or {})}
        return merged[key]


class ApiKey(Base):
    """A project key (project_id set) or an organisation key (org_id set, valid for every project in the org)."""

    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True,
                                                   nullable=True)
    org_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True,
                                               nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="editor")  # admin | editor | viewer | ingest
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class ConfigVersion(Base):
    """A pipeline configuration. Changes appear on the dashboard's change timeline."""

    __tablename__ = "config_versions"
    __table_args__ = (UniqueConstraint("project_id", "label"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Document(Base):
    """Corpus registry: one row per (doc_id, version)."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("project_id", "doc_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    doc_id: Mapped[str] = mapped_column(String(300), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_proj_doc", "project_id", "doc_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(String(300), index=True)
    doc_id: Mapped[str] = mapped_column(String(300))
    doc_version: Mapped[int] = mapped_column(Integer, default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)  # order inside the document
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(EmbeddingType, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class Trace(Base):
    __tablename__ = "traces"
    __table_args__ = (Index("ix_traces_proj_time", "project_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    config_version_id: Mapped[str | None] = mapped_column(ForeignKey("config_versions.id"), nullable=True)
    query: Mapped[str] = mapped_column(Text)
    query_embedding: Mapped[list | None] = mapped_column(EmbeddingType, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_feedback: Mapped[float | None] = mapped_column(Float, nullable=True)  # -1..1
    session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="sdk")  # sdk | otel | import:langfuse | ...
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    eval_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sampled_out|done|error
    is_failure: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    chunks: Mapped[list["RetrievedChunk"]] = relationship(
        back_populates="trace", cascade="all, delete-orphan", order_by="RetrievedChunk.rank"
    )
    evaluations: Mapped[list["Evaluation"]] = relationship(back_populates="trace", cascade="all, delete-orphan")
    diagnosis: Mapped["Diagnosis | None"] = relationship(
        back_populates="trace", cascade="all, delete-orphan", uselist=False
    )


class RetrievedChunk(Base):
    __tablename__ = "retrieved_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"), index=True)
    step: Mapped[str] = mapped_column(String(16))  # retrieve | rerank | context
    rank: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    doc_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    doc_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace: Mapped[Trace] = relationship(back_populates="chunks")


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (Index("ix_eval_trace_metric", "trace_id", "metric"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"))
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    metric: Mapped[str] = mapped_column(String(64))
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge: Mapped[str] = mapped_column(String(64))
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    trace: Mapped[Trace] = relationship(back_populates="evaluations")


class Diagnosis(Base):
    __tablename__ = "diagnoses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"), unique=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    trace: Mapped[Trace] = relationship(back_populates="diagnosis")


class HumanLabel(Base):
    """Human judgements used to calibrate the LLM judge."""

    __tablename__ = "human_labels"
    __table_args__ = (UniqueConstraint("trace_id", "metric"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    trace_id: Mapped[str] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(64))
    passed: Mapped[bool] = mapped_column(Boolean)
    labeler: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GoldenSet(Base):
    __tablename__ = "golden_sets"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cases: Mapped[list["GoldenCase"]] = relationship(back_populates="golden_set", cascade="all, delete-orphan")


class GoldenCase(Base):
    __tablename__ = "golden_cases"
    __table_args__ = (UniqueConstraint("golden_set_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    golden_set_id: Mapped[str] = mapped_column(ForeignKey("golden_sets.id", ondelete="CASCADE"))
    query: Mapped[str] = mapped_column(Text)
    expected_doc_ids: Mapped[list] = mapped_column(JSON, default=list)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    added_in_version: Mapped[int] = mapped_column(Integer, default=1)
    # where expected_answer came from: "human" | "extracted" (draft, from the gold chunk) | None
    expected_answer_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    golden_set: Mapped[GoldenSet] = relationship(back_populates="cases")


class RagPipeline(Base):
    """A user's RAG system registered for testing (HTTP endpoint, any language)."""

    __tablename__ = "rag_pipelines"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16), default="http")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)  # url, headers, field paths, timeout
    default_config: Mapped[dict] = mapped_column(JSON, default=dict)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EvalRun(Base):
    """One test run of a pipeline + config over a dataset (golden set or uploaded cases)."""

    __tablename__ = "eval_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    pipeline: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    dataset: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="server")  # server | cli
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cases: Mapped[list["EvalRunCase"]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                      order_by="EvalRunCase.idx")


class EvalRunCase(Base):
    __tablename__ = "eval_run_cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    query: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_doc_ids: Mapped[list] = mapped_column(JSON, default=list)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    contexts: Mapped[list] = mapped_column(JSON, default=list)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failed: Mapped[bool] = mapped_column(Boolean, default=False)
    hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    run: Mapped[EvalRun] = relationship(back_populates="cases")


class SLO(Base):
    __tablename__ = "slos"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    metric: Mapped[str] = mapped_column(String(64))
    threshold: Mapped[float] = mapped_column(Float)  # a sample is "good" if score >= threshold
    target: Mapped[float] = mapped_column(Float)  # e.g. 0.95 of samples must be good
    window_days: Mapped[int] = mapped_column(Integer, default=7)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ok | at_risk | breached


class AlertChannel(Base):
    __tablename__ = "alert_channels"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # slack | webhook | email | pagerduty
    target: Mapped[str] = mapped_column(String(500))  # URL, address or routing key
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    slo_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    root_causes: Mapped[dict] = mapped_column(JSON, default=dict)
    delivered_to: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Job(Base):
    """Durable job queue row. Survives API/worker restarts (unlike in-process background tasks)."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run", "status", "run_after"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
