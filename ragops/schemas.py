"""Public API schemas (shared by server and SDK)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ChunkIn(BaseModel):
    text: str | None = None
    chunk_id: str | None = None
    doc_id: str | None = None
    doc_version: int | None = None
    score: float | None = None
    rank: int | None = None


class TraceIn(BaseModel):
    id: str | None = Field(None, description="Client-generated trace id (idempotency key)")
    query: str
    answer: str | None = None
    retrieved: list[ChunkIn] = Field(default_factory=list, description="Candidates from the retriever, before rerank")
    reranked: list[ChunkIn] = Field(default_factory=list, description="Output of the reranker, if any")
    contexts: list[ChunkIn] = Field(default_factory=list, description="Chunks actually placed in the LLM prompt")
    model: str | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    session_id: str | None = None
    environment: str | None = Field(None, description="dev | staging | production (or your own names)")
    config_label: str | None = Field(None, description="Pipeline config version label, e.g. 'chunk512-bge-v3'")
    config: dict[str, Any] | None = Field(None, description="Config contents (stored once per label)")
    expected_answer: str | None = None
    expected_doc_ids: list[str] = Field(default_factory=list)
    user_feedback: float | None = Field(None, ge=-1, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime | None = None
    force_eval: bool = False

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data: Any) -> Any:
        """Accept the field names other RAG stacks use, so plugging in an existing pipeline just works:
        question/input -> query, response/output -> answer, chunks/documents/context -> contexts,
        ground_truth/reference -> expected_answer. Plain strings are accepted as chunks."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for canon, alts in {"query": ("question", "input", "user_input"), "answer": ("response", "output"),
                            "contexts": ("chunks", "documents", "context", "retrieved_contexts", "sources"),
                            "expected_answer": ("ground_truth", "reference", "expected")}.items():
            if not d.get(canon):
                for a in alts:
                    if d.get(a):
                        d[canon] = d.pop(a)
                        break
        for k in ("retrieved", "reranked", "contexts"):
            if isinstance(d.get(k), str):
                d[k] = [d[k]]
            if isinstance(d.get(k), list):
                d[k] = [{"text": c} if isinstance(c, str) else
                        ({**c, "text": c.get("text") or c.get("page_content") or c.get("content")} if isinstance(c, dict) else c)
                        for c in d[k]]
        return d


class TraceBatchIn(BaseModel):
    traces: list[TraceIn]


class FeedbackIn(BaseModel):
    score: float = Field(..., ge=-1, le=1)
    comment: str | None = None


class DocumentIn(BaseModel):
    doc_id: str
    title: str | None = None
    text: str | None = None
    chunks: list[ChunkIn] | None = Field(None, description="Pre-chunked content; otherwise text is chunked for you")
    metadata: dict[str, Any] = Field(default_factory=dict)


class CorpusIn(BaseModel):
    documents: list[DocumentIn]
    chunk_words: int = 120
    delete_missing: bool = Field(False, description="Treat documents absent from this batch as deleted")


class ProjectIn(BaseModel):
    name: str
    settings: dict[str, Any] = Field(default_factory=dict)
    org: str | None = Field(None, description="Organisation name or id (instance admin only; org keys use their own)")


class OrgIn(BaseModel):
    name: str
    settings: dict[str, Any] = Field(default_factory=dict)


class ApiKeyIn(BaseModel):
    name: str
    role: Literal["admin", "editor", "viewer", "ingest"] = "editor"


class SLOIn(BaseModel):
    name: str
    metric: str
    threshold: float = Field(..., ge=0, le=1)
    target: float = Field(..., gt=0, lt=1)
    window_days: int = 7


class AlertChannelIn(BaseModel):
    kind: Literal["slack", "teams", "webhook", "email", "pagerduty"]
    target: str


class HumanLabelIn(BaseModel):
    trace_id: str
    metric: str
    passed: bool
    labeler: str | None = None


class GoldenPromoteIn(BaseModel):
    trace_id: str
    golden_set: str = "production-failures"
    expected_answer: str | None = None
    expected_doc_ids: list[str] | None = None


class GoldenCaseIn(BaseModel):
    query: str
    expected_doc_ids: list[str] = Field(default_factory=list)
    expected_answer: str | None = None


class AutoGoldenRule(BaseModel):
    enabled: bool = True
    labels: list[str] = Field(default_factory=lambda: ["retrieval_miss", "ranking_failure", "generation_failure",
                                                       "chunking_defect", "context_overload"])
    min_confidence: float = 0.75
    golden_set: str = "production-failures"


class PipelineIn(BaseModel):
    """Register your RAG system's HTTP endpoint. RAGOps POSTs {"<query_field>": query, "config": {...}}."""

    name: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    answer_path: str = Field("answer", description="Dotted path to the answer in the JSON response")
    contexts_path: str = Field("contexts", description="Dotted path to the list of context chunks/sources")
    retrieved_path: str | None = Field("retrieved", description="Optional dotted path to pre-rerank candidates")
    query_field: str = "query"
    timeout: float = 60
    extra_body: dict[str, Any] = Field(default_factory=dict)
    default_config: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None


class PipelineCheckIn(BaseModel):
    query: str
    config: dict[str, Any] = Field(default_factory=dict)


class DatasetCase(BaseModel):
    query: str
    expected_answer: str | None = None
    expected_doc_ids: list[str] = Field(default_factory=list)


class RunIn(BaseModel):
    pipeline: str
    golden_set: str | None = None
    cases: list[DatasetCase] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None


class RunUploadIn(BaseModel):
    name: str
    pipeline: str = "local"
    config: dict[str, Any] = Field(default_factory=dict)
    dataset: str = "uploaded"
    rows: list[dict[str, Any]]


class GuardIn(BaseModel):
    """A grounding check before an answer is shown. `contexts` = passage texts or chunk objects with a `text`."""
    answer: str
    contexts: list = Field(default_factory=list)
    question: str = ""
    policy: Literal["block", "rewrite", "flag"] = "rewrite"
    min_supported: float = Field(1.0, ge=0.0, le=1.0)
    use_llm: bool = False
    fallback: str | None = None
