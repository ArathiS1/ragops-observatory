"""Stage-level root-cause classifier.

For every failed RAG call it answers "which stage broke?":

    knowledge_gap      no document in the corpus covers the query
    stale_knowledge    the context came from an outdated (or deleted) document version
    chunking_defect    the answer exists but is split across chunk boundaries, so no single chunk carries it
    retrieval_miss     the relevant chunk exists in the corpus but was not retrieved
    ranking_failure    the relevant chunk was retrieved but cut by the reranker / top-k before generation
    context_overload   the relevant chunk was in context but buried among many irrelevant chunks
    generation_failure the relevant chunk was in context, yet the answer is unfaithful, off-target or refuses

The classifier works from evidence the observatory already has (the trace, eval scores and the corpus
registry). It needs no extra LLM calls, so it is cheap enough to run on every evaluated trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ragops.embeddings import embed
from ragops.eval.types import RagSample
from ragops.textutil import content_tokens, coverage, is_abstention, sentences

LABELS = [
    "knowledge_gap",
    "stale_knowledge",
    "chunking_defect",
    "retrieval_miss",
    "ranking_failure",
    "context_overload",
    "generation_failure",
]
LABEL_HELP = {
    "knowledge_gap": "No document covers this question. Fix: write or ingest the missing content.",
    "stale_knowledge": "Answer was built from an outdated document version. Fix: re-index changed documents.",
    "chunking_defect": "The answer is split across chunk boundaries. Fix: larger chunks, overlap, or semantic chunking.",
    "retrieval_miss": "The right chunk exists but was not retrieved. Fix: embedding model, hybrid search, query rewriting.",
    "ranking_failure": "The right chunk was retrieved but dropped before generation. Fix: reranker, top-k, score cutoff.",
    "context_overload": "The right chunk was in context but buried. Fix: fewer chunks, better ordering, compression.",
    "generation_failure": "Good context, bad answer. Fix: prompt, model, or answer-grounding instructions.",
    "incomplete_context": "Right document, but the passage with the answer was not in the prompt. Fix: larger chunks, "
                          "overlap, or more chunks (top_k).",
    "unknown": "Failure could not be attributed to a single stage.",
}


@dataclass
class CorpusChunk:
    chunk_id: str
    doc_id: str
    doc_version: int
    position: int
    text: str


@dataclass
class CorpusView:
    """In-memory view of the *current* corpus plus version history."""

    chunks: list[CorpusChunk] = field(default_factory=list)
    embeddings: np.ndarray | None = None
    current_version: dict[str, int] = field(default_factory=dict)
    deleted_docs: set[str] = field(default_factory=set)

    def __post_init__(self):
        if self.embeddings is None and self.chunks:
            self.embeddings = embed([c.text for c in self.chunks])
        self._tokens = [content_tokens(c.text) for c in self.chunks]
        self._by_doc: dict[str, list[int]] = {}
        for i, c in enumerate(self.chunks):
            self._by_doc.setdefault(c.doc_id, []).append(i)

    @property
    def empty(self) -> bool:
        return not self.chunks

    def relevance(self, query: str, q_emb: np.ndarray | None = None) -> np.ndarray:
        """Hybrid relevance in [0,1]: half semantic (embedding cosine), half lexical (query-term coverage)."""
        if self.empty:
            return np.zeros(0)
        if q_emb is None:
            q_emb = embed([query])[0]
        cos = np.clip(self.embeddings @ q_emb, 0, 1)
        q = content_tokens(query)
        lex = np.array([coverage(q, t) for t in self._tokens])
        return 0.5 * cos + 0.5 * lex

    def adjacent_pair_coverage(self, query: str, idx: int) -> tuple[float, int | None]:
        q = content_tokens(query)
        c = self.chunks[idx]
        best, partner = 0.0, None
        for j in self._by_doc.get(c.doc_id, []):
            if abs(self.chunks[j].position - c.position) == 1:
                cov = coverage(q, self._tokens[idx] | self._tokens[j])
                if cov > best:
                    best, partner = cov, j
        return best, partner


@dataclass
class DiagnosisResult:
    label: str  # one of LABELS, "ok" or "unknown"
    confidence: float
    is_failure: bool
    evidence: dict

    @property
    def explanation(self) -> str:
        return LABEL_HELP.get(self.label, "")


@dataclass
class Thresholds:
    faithfulness: float = 0.7
    answer_relevance: float = 0.5
    answer_correctness: float = 0.5
    gap_relevance: float = 0.30  # best corpus relevance below this => knowledge gap
    gold_relevance: float = 0.45  # chunk relevance needed to count as "the right chunk"
    single_chunk_coverage: float = 0.75
    overload_chunks: int = 8


def is_failure(scores: dict[str, float | None], feedback: float | None, th: Thresholds,
               abstained: bool = False) -> tuple[bool, list[str]]:
    reasons = []
    f = scores.get("faithfulness")
    a = scores.get("answer_relevance")
    if f is not None and f < th.faithfulness:
        reasons.append(f"faithfulness {f:.2f} < {th.faithfulness}")
    # "I don't know" on a question the corpus does not cover is the right behaviour, not an irrelevant answer
    correct_refusal = abstained and scores.get("abstention_quality") == 1 and scores.get("answer_correctness") in (None, 1.0)
    # A test case whose answer matches the reference answer is relevant by definition: a paraphrased question
    # ("initial email reply timeframe" vs "first response") must not fail a correct answer on wording.
    c0 = scores.get("answer_correctness")
    matches_reference = c0 is not None and c0 >= 0.8
    if a is not None and a < th.answer_relevance and not correct_refusal and not matches_reference:
        reasons.append(f"answer_relevance {a:.2f} < {th.answer_relevance}")
    c = scores.get("answer_correctness")
    if c is not None and c < th.answer_correctness:
        reasons.append(f"answer_correctness {c:.2f} < {th.answer_correctness}")
    if scores.get("abstention_quality") == 0:
        reasons.append("wrong abstention behaviour")
    if scores.get("context_consistency") == 0:
        reasons.append("contradictory context")
    if feedback is not None and feedback < 0:
        reasons.append("negative user feedback")
    return bool(reasons), reasons


def diagnose(
    sample: RagSample,
    scores: dict[str, float | None],
    corpus: CorpusView | None = None,
    feedback: float | None = None,
    th: Thresholds | None = None,
) -> DiagnosisResult:
    th = th or Thresholds()
    failed, reasons = is_failure(scores, feedback, th, is_abstention(sample.answer))
    ev: dict = {"failure_reasons": reasons}
    ctx = sample.contexts

    # 1. stale knowledge is checked first: an answer built from an outdated or deleted document version is a
    #    failure even when nobody has complained yet, because the user was served information that is no longer true.
    stale = _stale_context(sample, corpus)
    if stale:
        ev["failure_reasons"] = reasons + ["context built from an outdated document version"]
        ev["stale_documents"] = stale
        return DiagnosisResult("stale_knowledge", 0.9, True, ev)

    if not failed and is_abstention(sample.answer) and scores.get("abstention_quality") == 1:
        # The bot correctly said it does not know: not a pipeline failure, but the corpus is missing content that
        # users ask for. Recorded as a (non-failing) knowledge gap so it lands on the knowledge-base to-do list.
        return DiagnosisResult("knowledge_gap", 0.8, False, {
            "reasons": [], "note": "correctly refused: no document covers this question; write one if users need it"})

    if not failed:
        unused = [{k: v for k, v in f.items() if not k.startswith("_")} for f in _outdated_chunks(sample, corpus)]
        return DiagnosisResult("ok", 1.0, False, {"reasons": [], **(
            {"warnings": ["outdated document version in context (not used by the answer)"], "outdated_unused": unused}
            if unused else {})})

    candidates = sample.retrieved or ctx
    ctx_ids = {c.chunk_id for c in ctx if c.chunk_id}
    cand_ids = {c.chunk_id for c in candidates if c.chunk_id}
    ctx_docs = {c.doc_id for c in ctx if c.doc_id}
    cand_docs = {c.doc_id for c in candidates if c.doc_id}

    # Without a registered corpus we can only reason about what was retrieved.
    if corpus is None or corpus.empty:
        return _diagnose_without_corpus(sample, scores, th, ev)

    rel = corpus.relevance(sample.query)
    order = np.argsort(-rel)
    best_i = int(order[0])
    best = float(rel[best_i])
    ev["best_corpus_match"] = {"chunk_id": corpus.chunks[best_i].chunk_id, "doc_id": corpus.chunks[best_i].doc_id,
                               "relevance": round(best, 3)}

    # 2. knowledge gap (not when the expected document reached the prompt: then the content exists)
    expected_found = bool(sample.expected_doc_ids) and bool(set(sample.expected_doc_ids) & ctx_docs)
    if best < th.gap_relevance and not expected_found:
        conf = 0.6 + 0.4 * (1 - best / th.gap_relevance)
        ev["note"] = "no chunk in the corpus is relevant to this query"
        return DiagnosisResult("knowledge_gap", round(min(conf, 0.99), 2), True, ev)

    gold_idx = [int(i) for i in order[:10] if rel[i] >= max(th.gold_relevance, best * 0.85)] or [best_i]
    if sample.expected_doc_ids:
        exp = set(sample.expected_doc_ids)
        gold_idx = [i for i, c in enumerate(corpus.chunks) if c.doc_id in exp] or gold_idx
    gold_chunks = [corpus.chunks[i] for i in gold_idx]
    gold_ids = {c.chunk_id for c in gold_chunks}
    gold_docs = {c.doc_id for c in gold_chunks}
    ev["gold_chunks"] = sorted(gold_ids)[:5]

    # 3. chunking defect: no single chunk carries the answer, but two adjacent chunks together do
    q = content_tokens(sample.query)
    best_single = max(coverage(q, corpus._tokens[i]) for i in order[:10])
    pair_cov, partner = corpus.adjacent_pair_coverage(sample.query, best_i)
    if best_single < th.single_chunk_coverage and pair_cov >= th.single_chunk_coverage and partner is not None:
        ev["split_across"] = [corpus.chunks[best_i].chunk_id, corpus.chunks[partner].chunk_id]
        ev["single_chunk_coverage"] = round(best_single, 2)
        ev["adjacent_pair_coverage"] = round(pair_cov, 2)
        return DiagnosisResult("chunking_defect", 0.75, True, ev)
    split = _boundary_split(sample.query, corpus, [i for i in order[:5]], ctx_ids)
    if split is not None:
        ev.update(split)
        return DiagnosisResult("chunking_defect", 0.7, True, ev)

    corpus_ids = {c.chunk_id for c in corpus.chunks}
    gold_tok = [content_tokens(c.text) for c in gold_chunks]

    def text_hit(chunks) -> bool:
        """The app chunks its documents its own way (LlamaIndex, LangChain...), so its chunk ids never match the
        registered corpus: count a hit when a retrieved chunk of the same document shares most of its content
        with a gold chunk, whichever of the two is larger."""
        for c in chunks:
            if c.chunk_id in corpus_ids or (c.doc_id and c.doc_id not in gold_docs):
                continue
            ct = content_tokens(c.text)
            for gt in gold_tok:
                if ct and gt and len(ct & gt) / min(len(ct), len(gt)) >= 0.7:
                    return True
        return False

    def hit(ids: set, docs: set, chunks) -> bool:
        if ids & gold_ids:
            return True
        if sample.expected_doc_ids and docs & gold_docs:
            return True
        return text_hit(chunks)

    in_ctx = hit(ctx_ids, ctx_docs, ctx)
    in_cand = hit(cand_ids, cand_docs, candidates)

    # 4. retrieval miss
    if not in_cand:
        ev["retrieved_chunks"] = sorted(cand_ids)[:10]
        margin = best - max([float(rel[i]) for i, c in enumerate(corpus.chunks) if c.chunk_id in cand_ids] or [0])
        return DiagnosisResult("retrieval_miss", round(min(0.95, 0.6 + margin), 2), True, ev)

    # 5. ranking failure: retrieved but dropped before generation
    if not in_ctx:
        pos = [c.rank for c in candidates if c.chunk_id in gold_ids]
        ev["gold_rank_in_candidates"] = min(pos) if pos else None
        ev["context_size"] = len(ctx)
        return DiagnosisResult("ranking_failure", 0.85, True, ev)

    # gold chunk is in context from here on
    gold_pos = min((i for i, c in enumerate(ctx) if c.chunk_id in gold_ids or
                    (sample.expected_doc_ids and c.doc_id in gold_docs)), default=0)
    ev["gold_position_in_context"] = gold_pos + 1
    ev["context_size"] = len(ctx)

    # 6. context overload
    n_relevant = sum(coverage(q, content_tokens(c.text)) >= 0.5 for c in ctx)
    if len(ctx) >= th.overload_chunks and gold_pos >= 3 and n_relevant / len(ctx) < 0.4:
        ev["relevant_fraction"] = round(n_relevant / len(ctx), 2)
        return DiagnosisResult("context_overload", 0.7, True, ev)

    # 7. generation failure
    if is_abstention(sample.answer):
        ev["note"] = "model refused although the answer was in context"
    return DiagnosisResult("generation_failure", 0.8, True, ev)


_SENT_END = ("." , "!", "?", '"', "'", ")", ":")


def _boundary_split(query: str, corpus: CorpusView, top_idx: list, ctx_ids: set) -> dict | None:
    """Signature of a chunking defect: a chunk boundary cuts through the sentence that best matches the query, and
    only one side of that sentence reached the prompt."""
    q = content_tokens(query)
    for i in top_idx:
        c = corpus.chunks[int(i)]
        for j in corpus._by_doc.get(c.doc_id, []):
            n = corpus.chunks[j]
            if n.position != c.position + 1 and n.position != c.position - 1:
                continue
            first, second = (c, n) if n.position == c.position + 1 else (n, c)
            if first.text.rstrip().endswith(_SENT_END):
                continue  # boundary falls between sentences: nothing was cut
            head = sentences(first.text)
            tail = sentences(second.text)
            if not head or not tail:
                continue
            spanning = head[-1] + " " + tail[0]
            cov_span = coverage(q, content_tokens(spanning))
            inside = [x for x in head[:-1] + tail[1:]]
            cov_inside = max([coverage(q, content_tokens(x)) for x in inside] or [0.0])
            in_ctx = {first.chunk_id in ctx_ids, second.chunk_id in ctx_ids}
            if in_ctx != {True, False}:
                continue
            seen, missing = (head[-1], tail[0]) if first.chunk_id in ctx_ids else (tail[0], head[-1])
            only_missing = (q & content_tokens(missing)) - content_tokens(seen)
            if cov_span >= 0.5 and cov_span >= cov_inside + 0.2 and len(only_missing) >= 1 \
                    and coverage(q, content_tokens(seen)) < cov_span:
                return {"split_across": [first.chunk_id, second.chunk_id], "spanning_sentence": spanning[:300],
                        "spanning_sentence_coverage": round(cov_span, 2), "best_full_sentence_coverage": round(cov_inside, 2)}
    return None


def _stale_context(sample: RagSample, corpus: CorpusView | None) -> list[dict]:
    """Outdated (old-version or deleted) context chunks that the answer actually relied on.

    "Relied on" means the answer cites the document, or the answer's wording overlaps that chunk at least as much as
    any current chunk in the prompt. Without an answer, a stale chunk counts when it is the most query-relevant chunk.
    """
    found = _outdated_chunks(sample, corpus)
    if not found:
        return []
    import re

    ans = sample.answer or ""
    cited = set(re.findall(r"\[(?:source:\s*)?([^\[\]\n]{1,200}?)\]", ans))
    a_tok = content_tokens(re.sub(r"\[[^\]]*\]", " ", ans))
    q_tok = content_tokens(sample.query)

    def cov(c) -> float:
        return coverage(a_tok, content_tokens(c.text)) if a_tok else coverage(q_tok, content_tokens(c.text))

    fresh_best = max([cov(c) for c in sample.contexts if (c.doc_id, c.chunk_id) not in
                      {(f["doc_id"], f["_chunk"]) for f in found}] or [0.0])
    used = []
    for f in found:
        if f["doc_id"] in cited or (f["_cov"] >= 0.5 and f["_cov"] >= fresh_best):
            used.append({k: v for k, v in f.items() if not k.startswith("_")})
    return used


def _outdated_chunks(sample: RagSample, corpus: CorpusView | None) -> list[dict]:
    if corpus is None or corpus.empty:
        return []
    import re

    a_tok = content_tokens(re.sub(r"\[[^\]]*\]", " ", sample.answer or ""))
    q_tok = content_tokens(sample.query)
    out, seen = [], set()
    for c in sample.contexts:
        if not c.doc_id or c.doc_id in seen:
            continue
        if c.doc_id in corpus.deleted_docs:
            cur = "deleted"
        elif c.doc_version is not None and c.doc_version < corpus.current_version.get(c.doc_id, c.doc_version):
            cur = corpus.current_version[c.doc_id]
        else:
            continue
        seen.add(c.doc_id)
        tok = content_tokens(c.text)
        out.append({"doc_id": c.doc_id, "used_version": c.doc_version, "current_version": cur, "_chunk": c.chunk_id,
                    "_cov": coverage(a_tok, tok) if a_tok else coverage(q_tok, tok)})
    return out


def _diagnose_without_corpus(sample: RagSample, scores, th: Thresholds, ev: dict) -> DiagnosisResult:
    ev["note"] = "corpus not registered; register documents to separate knowledge gaps from retrieval misses"
    q = content_tokens(sample.query)
    ctx = sample.contexts
    cands = sample.retrieved or ctx
    rel_ctx = [c for c in ctx if coverage(q, content_tokens(c.text)) >= 0.5]
    rel_cand = [c for c in cands if coverage(q, content_tokens(c.text)) >= 0.5]
    if not rel_cand:
        return DiagnosisResult("retrieval_miss", 0.5, True, ev)
    if not rel_ctx:
        return DiagnosisResult("ranking_failure", 0.7, True, ev)
    if len(ctx) >= th.overload_chunks and len(rel_ctx) / len(ctx) < 0.4:
        return DiagnosisResult("context_overload", 0.55, True, ev)
    return DiagnosisResult("generation_failure", 0.65, True, ev)
