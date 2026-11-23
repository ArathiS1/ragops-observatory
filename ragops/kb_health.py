"""Knowledge-base health: what should the docs team write, fix or delete?"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from ragops.diagnosis import CorpusView
from ragops.textutil import content_tokens, jaccard, numbers, top_terms


@dataclass
class CoverageGap:
    topic: str
    query_count: int
    examples: list[str]
    suggestion: str


@dataclass
class KBHealthReport:
    total_chunks: int
    coverage_gaps: list[CoverageGap] = field(default_factory=list)
    never_retrieved: list[str] = field(default_factory=list)
    never_retrieved_share: float = 0.0
    hot_chunks: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    health_score: float = 1.0

    def to_dict(self):
        return asdict(self)


def coverage_gaps(gap_queries: list[str], embeddings: np.ndarray | None = None, max_topics: int = 8) -> list[CoverageGap]:
    if not gap_queries:
        return []
    from ragops.embeddings import embed

    E = embeddings if embeddings is not None else embed(gap_queries)
    if len(gap_queries) < 4:
        labels = np.zeros(len(gap_queries), dtype=int)
    else:
        from sklearn.cluster import AgglomerativeClustering

        labels = AgglomerativeClustering(n_clusters=None, distance_threshold=1.1, metric="cosine",
                                         linkage="average").fit_predict(E)
    out = []
    for lab in set(labels.tolist()):
        texts = [q for q, lb in zip(gap_queries, labels) if lb == lab]
        terms = top_terms(texts, 4)
        topic = " / ".join(terms[:3]) or texts[0][:40]
        out.append(CoverageGap(topic, len(texts), texts[:4],
                               f"Write or ingest a document covering: {', '.join(terms) or texts[0]}"))
    out.sort(key=lambda g: -g.query_count)
    return out[:max_topics]


def kb_health(
    corpus: CorpusView,
    retrieved_counts: dict[str, int],
    failure_counts: dict[str, int],
    gap_queries: list[str],
    min_traffic_for_dead: int = 50,
    max_pairs_chunks: int = 4000,
) -> KBHealthReport:
    rep = KBHealthReport(total_chunks=len(corpus.chunks))
    rep.coverage_gaps = coverage_gaps(gap_queries)

    total_retrievals = sum(retrieved_counts.values())
    if total_retrievals >= min_traffic_for_dead and corpus.chunks:
        dead = [c.chunk_id for c in corpus.chunks if retrieved_counts.get(c.chunk_id, 0) == 0]
        rep.never_retrieved = dead[:200]
        rep.never_retrieved_share = round(len(dead) / len(corpus.chunks), 3)

    hot = []
    for cid, fails in failure_counts.items():
        seen = retrieved_counts.get(cid, 0)
        if fails >= 3 and seen and fails / seen >= 0.5:
            hot.append({"chunk_id": cid, "failures": fails, "appearances": seen, "failure_rate": round(fails / seen, 2)})
    rep.hot_chunks = sorted(hot, key=lambda h: (-h["failure_rate"], -h["failures"]))[:25]

    chunks = corpus.chunks[:max_pairs_chunks]
    if len(chunks) >= 2 and corpus.embeddings is not None:
        E = corpus.embeddings[: len(chunks)]
        sims = E @ E.T
        iu = np.triu_indices(len(chunks), 1)
        pairs = np.where(sims[iu] >= 0.6)[0]
        toks = [content_tokens(c.text) for c in chunks]
        nums = [numbers(c.text) for c in chunks]
        for p in pairs[:20000]:
            i, j = int(iu[0][p]), int(iu[1][p])
            a, b = chunks[i], chunks[j]
            jac = jaccard(toks[i], toks[j])
            if sims[i, j] >= 0.95 or jac >= 0.9:
                rep.duplicates.append({"a": a.chunk_id, "b": b.chunk_id, "similarity": round(float(sims[i, j]), 3)})
            elif a.doc_id != b.doc_id and jac >= 0.45 and nums[i] and nums[j] and nums[i] != nums[j] and not (nums[i] & nums[j]):
                rep.contradictions.append({"a": a.chunk_id, "b": b.chunk_id, "a_figures": sorted(nums[i])[:5],
                                           "b_figures": sorted(nums[j])[:5]})
        rep.duplicates = rep.duplicates[:50]
        rep.contradictions = rep.contradictions[:50]

    penalty = (0.25 * min(1, len(rep.coverage_gaps) / 5) + 0.2 * rep.never_retrieved_share
               + 0.25 * min(1, len(rep.hot_chunks) / 10) + 0.15 * min(1, len(rep.duplicates) / 20)
               + 0.15 * min(1, len(rep.contradictions) / 5))
    rep.health_score = round(max(0.0, 1 - penalty), 3)
    return rep
