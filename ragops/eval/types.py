from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContextChunk:
    text: str
    doc_id: str | None = None
    chunk_id: str | None = None
    doc_version: int | None = None
    score: float | None = None
    rank: int = 0


@dataclass
class RagSample:
    """Everything the evaluators and the root-cause classifier need about one RAG call."""

    query: str
    answer: str | None
    contexts: list[ContextChunk] = field(default_factory=list)  # what the LLM actually saw
    retrieved: list[ContextChunk] = field(default_factory=list)  # pre-rerank candidates (optional)
    expected_answer: str | None = None
    expected_doc_ids: list[str] = field(default_factory=list)
    corpus_max_similarity: float | None = None  # best match for the query anywhere in the corpus


@dataclass
class MetricResult:
    metric: str
    score: float | None
    reasoning: str = ""
    cost_usd: float = 0.0
    judge: str = "heuristic"


METRICS = [
    "faithfulness",
    "answer_relevance",
    "context_relevance",
    "context_recall",
    "citation_correctness",
    "abstention_quality",
    "context_consistency",
    "answer_correctness",  # only scored when the case has an expected_answer (test sets, golden sets)
]
# Metrics that need a language-model judgement (others are deterministic and free).
# answer_correctness is judged by meaning when an LLM judge is set (paraphrases of the reference are correct);
# the heuristic judge compares key terms and numbers.
LLM_METRICS = {"faithfulness", "answer_relevance", "context_relevance", "context_recall", "answer_correctness"}
