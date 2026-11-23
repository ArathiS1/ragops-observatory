"""Four-layer drift detection for RAG.

1. Query drift     - are users asking different things? (embedding distribution shift + new topic clusters
                     + whether the corpus covers those new topics)
2. Corpus drift    - which documents changed, and which past answers are now outdated?
3. Retrieval drift - are retrieval scores, empty-result rates or top-k sets shifting?
4. Quality drift   - did eval quality change, and *when*? (CUSUM + best-split change-point detection)

Statistical tests come from scipy/scikit-learn. If Evidently is installed, `evidently_report` can also
export Evidently's own HTML report for the embedding drift.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np
from scipy import stats

from ragops.textutil import top_terms


# ---------------------------------------------------------------------------------------------
# 1. Query drift
# ---------------------------------------------------------------------------------------------
@dataclass
class TopicCluster:
    label: str
    share: float  # share of current-window queries
    novelty: float  # 0 = same as reference topics, 1 = brand new
    corpus_coverage: float | None  # mean best-corpus relevance for its queries
    covered: bool | None
    examples: list[str]


@dataclass
class QueryDriftResult:
    drift_detected: bool
    domain_classifier_auc: float
    centroid_shift: float
    mmd: float
    mmd_p_value: float
    n_reference: int
    n_current: int
    new_topics: list[TopicCluster] = field(default_factory=list)
    uncovered_share: float = 0.0
    summary: str = ""

    def to_dict(self):
        return asdict(self)


def _mmd_rbf(x: np.ndarray, y: np.ndarray, gamma: float) -> float:
    def k(a, b):
        d = (a**2).sum(1)[:, None] + (b**2).sum(1)[None, :] - 2 * a @ b.T
        return np.exp(-gamma * np.clip(d, 0, None))

    return float(k(x, x).mean() + k(y, y).mean() - 2 * k(x, y).mean())


def query_drift(
    ref: np.ndarray,
    cur: np.ndarray,
    cur_texts: list[str],
    cur_coverage: list[float] | None = None,
    gap_relevance: float = 0.30,
    seed: int = 0,
    max_n: int = 400,
) -> QueryDriftResult:
    from sklearn.cluster import KMeans
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import cross_val_predict

    rng = np.random.default_rng(seed)
    if len(ref) < 10 or len(cur) < 10:
        return QueryDriftResult(False, 0.5, 0.0, 0.0, 1.0, len(ref), len(cur), summary="not enough queries (need 10+ per window)")
    ri = rng.choice(len(ref), min(len(ref), max_n), replace=False)
    ci = rng.choice(len(cur), min(len(cur), max_n), replace=False)
    r, c = ref[ri], cur[ci]

    # domain classifier: can a model tell the windows apart?
    X = np.vstack([r, c])
    y = np.r_[np.zeros(len(r)), np.ones(len(c))]
    proba = cross_val_predict(LogisticRegression(max_iter=500, C=0.5), X, y, cv=5, method="predict_proba")[:, 1]
    auc = float(roc_auc_score(y, proba))

    cr, cc = r.mean(0), c.mean(0)
    shift = float(1 - (cr @ cc) / (np.linalg.norm(cr) * np.linalg.norm(cc) + 1e-9))

    # MMD with permutation test
    gamma = 1.0
    mmd = _mmd_rbf(r, c, gamma)
    perms, n_r = 100, len(r)
    count = 0
    for _ in range(perms):
        idx = rng.permutation(len(X))
        if _mmd_rbf(X[idx[:n_r]], X[idx[n_r:]], gamma) >= mmd:
            count += 1
    p = (count + 1) / (perms + 1)
    detected = auc >= 0.65 and p < 0.05

    # topic clusters in the current window and their novelty vs the reference window
    k_cur = int(np.clip(round(np.sqrt(len(cur) / 4)), 2, 10))
    k_ref = int(np.clip(round(np.sqrt(len(ref) / 4)), 2, 10))
    km_c = KMeans(n_clusters=k_cur, n_init=5, random_state=seed).fit(cur)
    km_r = KMeans(n_clusters=k_ref, n_init=5, random_state=seed).fit(ref)
    rc = km_r.cluster_centers_ / np.linalg.norm(km_r.cluster_centers_, axis=1, keepdims=True)
    topics: list[TopicCluster] = []
    uncovered = 0
    for k in range(k_cur):
        members = np.where(km_c.labels_ == k)[0]
        cen = km_c.cluster_centers_[k] / (np.linalg.norm(km_c.cluster_centers_[k]) + 1e-9)
        # novelty: how far members are from the nearest reference topic, vs reference's own spread
        member_sim = (cur[members] @ rc.T).max(1).mean()
        novelty = float(np.clip(1 - member_sim / max(1e-6, float((ref @ rc.T).max(1).mean())), 0, 1))
        cov = None
        covered = None
        texts = [cur_texts[i] for i in members]
        if cur_coverage is not None:
            # judge coverage per question, not on the cluster mean: one well-covered question that landed in the
            # cluster must not hide that most of the topic is unanswerable
            member_cov = np.array([cur_coverage[i] for i in members])
            cov = float(np.median(member_cov))
            unanswerable = member_cov < gap_relevance
            covered = bool(unanswerable.mean() < 0.5)
            uncovered += int(unanswerable.sum())
            if not covered:  # lead with the questions the corpus cannot answer
                texts = [t for t, u in zip(texts, unanswerable) if u] + [t for t, u in zip(texts, unanswerable) if not u]
        share = len(members) / len(cur)
        terms = top_terms(texts, 3)
        # show questions that actually contain the topic's words, so the example matches the label
        lead = [t for t in texts if sum(w in t.lower() for w in terms) >= 2] or \
               [t for t in texts if any(w in t.lower() for w in terms)]
        examples = lead + [t for t in texts if t not in lead]
        topics.append(TopicCluster(" / ".join(terms) or f"topic {k}", round(share, 3),
                                   round(novelty, 3), None if cov is None else round(cov, 3), covered, examples[:3]))
        _ = cen
    topics.sort(key=lambda t: (-(t.novelty * t.share), -t.share))
    uncovered_share = uncovered / len(cur)
    new = [t for t in topics if t.novelty >= 0.25]
    # RAG-specific trigger: a new, sizeable topic that the corpus cannot answer is drift that matters, even when
    # the global distribution test is not yet significant.
    uncovered_new = [t for t in new if t.covered is False and t.share >= 0.08]
    parts = []
    if detected:
        parts.append(f"Query distribution shifted (domain AUC {auc:.2f}, MMD p={p:.3f}).")
    elif uncovered_new:
        detected = True
        parts.append(f"New uncovered topic detected (domain AUC {auc:.2f}, MMD p={p:.3f}).")
    else:
        parts.append(f"No significant query drift (domain AUC {auc:.2f}, MMD p={p:.3f}).")
    if new:
        parts.append("Emerging topics: " + "; ".join(f"'{t.label}' ({t.share:.0%})" for t in new[:3]) + ".")
    if uncovered_share > 0:
        parts.append(f"{uncovered_share:.0%} of current questions have no matching document in your corpus.")
    return QueryDriftResult(detected, round(auc, 3), round(shift, 4), round(mmd, 5), round(p, 4),
                            len(ref), len(cur), topics, round(uncovered_share, 3), " ".join(parts))


# ---------------------------------------------------------------------------------------------
# 2. Corpus drift
# ---------------------------------------------------------------------------------------------
@dataclass
class CorpusDriftResult:
    added: list[str]
    changed: list[str]
    deleted: list[str]
    outdated_trace_count: int
    outdated_trace_ids: list[str]
    summary: str

    def to_dict(self):
        return asdict(self)


def corpus_drift(doc_events: list[dict], context_usage: list[dict], current_version: dict[str, int],
                 deleted: set[str]) -> CorpusDriftResult:
    """doc_events: [{doc_id, version}] created inside the window.
    context_usage: [{trace_id, doc_id, doc_version}] for recent traces."""
    added = sorted({e["doc_id"] for e in doc_events if e["version"] == 1})
    changed = sorted({e["doc_id"] for e in doc_events if e["version"] > 1})
    outdated = sorted({u["trace_id"] for u in context_usage if u["doc_id"] in deleted or (
        u.get("doc_version") is not None and u["doc_version"] < current_version.get(u["doc_id"], u["doc_version"]))})
    summary = (f"{len(added)} documents added, {len(changed)} changed, {len(deleted)} deleted. "
               f"{len(outdated)} recent answers were built on content that has since changed.")
    return CorpusDriftResult(added, changed, sorted(deleted), len(outdated), outdated[:50], summary)


# ---------------------------------------------------------------------------------------------
# 3. Retrieval drift
# ---------------------------------------------------------------------------------------------
@dataclass
class RetrievalDriftResult:
    drift_detected: bool
    top1_score_ref: float | None
    top1_score_cur: float | None
    top1_ks_p_value: float | None
    empty_rate_ref: float
    empty_rate_cur: float
    empty_rate_p_value: float | None
    topk_overlap: float | None  # mean Jaccard of top-k sets for repeated queries across windows
    summary: str

    def to_dict(self):
        return asdict(self)


def retrieval_drift(ref: list[dict], cur: list[dict]) -> RetrievalDriftResult:
    """Each item: {query, top1: float|None, n: int, chunk_ids: list[str]}."""
    r1 = [x["top1"] for x in ref if x["top1"] is not None]
    c1 = [x["top1"] for x in cur if x["top1"] is not None]
    ks_p = float(stats.ks_2samp(r1, c1).pvalue) if len(r1) >= 5 and len(c1) >= 5 else None
    er = sum(x["n"] == 0 for x in ref) / max(1, len(ref))
    ec = sum(x["n"] == 0 for x in cur) / max(1, len(cur))
    ep = None
    if ref and cur:
        table = [[sum(x["n"] == 0 for x in ref), sum(x["n"] > 0 for x in ref)],
                 [sum(x["n"] == 0 for x in cur), sum(x["n"] > 0 for x in cur)]]
        if min(sum(row) for row in table) > 0 and (table[0][0] + table[1][0]) > 0:
            ep = float(stats.fisher_exact(table)[1])
    norm = lambda q: " ".join(q.lower().split())  # noqa: E731
    ref_sets = {norm(x["query"]): set(x["chunk_ids"]) for x in ref}
    overlaps = [len(ref_sets[norm(x["query"])] & set(x["chunk_ids"])) /
                max(1, len(ref_sets[norm(x["query"])] | set(x["chunk_ids"])))
                for x in cur if norm(x["query"]) in ref_sets]
    ov = float(np.mean(overlaps)) if overlaps else None
    m_r = float(np.mean(r1)) if r1 else None
    m_c = float(np.mean(c1)) if c1 else None
    detected = bool((ks_p is not None and ks_p < 0.01 and m_r is not None and m_c is not None and abs(m_r - m_c) > 0.03)
                    or (ep is not None and ep < 0.01) or (ov is not None and ov < 0.5))
    parts = []
    if m_r is not None and m_c is not None:
        parts.append(f"Mean top-1 retrieval score {m_r:.3f} -> {m_c:.3f}" + (f" (KS p={ks_p:.3g})" if ks_p is not None else "") + ".")
    parts.append(f"Empty retrievals {er:.1%} -> {ec:.1%}.")
    if ov is not None:
        parts.append(f"Repeated queries share {ov:.0%} of their top-k chunks with the reference window.")
    return RetrievalDriftResult(detected, None if m_r is None else round(m_r, 4), None if m_c is None else round(m_c, 4),
                                None if ks_p is None else round(ks_p, 5), round(er, 4), round(ec, 4),
                                None if ep is None else round(ep, 5), None if ov is None else round(ov, 3), " ".join(parts))


# ---------------------------------------------------------------------------------------------
# 4. Quality drift: change-point detection
# ---------------------------------------------------------------------------------------------
@dataclass
class ChangePoint:
    metric: str
    detected: bool
    change_date: str | None
    before_mean: float | None
    after_mean: float | None
    p_value: float | None
    direction: str | None
    cusum_alarm_date: str | None
    summary: str

    def to_dict(self):
        return asdict(self)


def cusum(values: np.ndarray, k: float = 0.5, h: float = 4.0, baseline_n: int | None = None) -> int | None:
    """Two-sided standardised CUSUM. Returns index of first alarm or None."""
    if len(values) < 6:
        return None
    n0 = baseline_n or max(3, len(values) // 3)
    mu, sd = values[:n0].mean(), values[:n0].std(ddof=1) or 1e-6
    z = (values - mu) / sd
    sp = sn = 0.0
    for i, v in enumerate(z):
        sp = max(0.0, sp + v - k)
        sn = max(0.0, sn - v - k)
        if sp > h or sn > h:
            return i
    return None


def change_point(metric: str, days: list[date], daily_means: list[float], daily_values: list[list[float]] | None = None,
                 min_segment: int = 3, alpha: float = 0.01, min_effect: float = 0.03) -> ChangePoint:
    v = np.asarray(daily_means, dtype=float)
    if len(v) < 2 * min_segment:
        return ChangePoint(metric, False, None, None, None, None, None, None, "not enough days of data")
    best_i, best_cost = None, np.inf
    for i in range(min_segment, len(v) - min_segment + 1):
        cost = ((v[:i] - v[:i].mean()) ** 2).sum() + ((v[i:] - v[i:].mean()) ** 2).sum()
        if cost < best_cost:
            best_i, best_cost = i, cost
    assert best_i is not None
    if daily_values:
        before = [x for d in daily_values[:best_i] for x in d]
        after = [x for d in daily_values[best_i:] for x in d]
    else:
        before, after = list(v[:best_i]), list(v[best_i:])
    p = float(stats.ttest_ind(before, after, equal_var=False).pvalue) if len(before) > 1 and len(after) > 1 else 1.0
    if math.isnan(p):  # zero variance on both sides: identical means -> no evidence, different means -> certain
        p = 1.0 if abs(float(np.mean(before)) - float(np.mean(after))) < 1e-12 else 0.0
    bm, am = float(np.mean(before)), float(np.mean(after))
    alarm = cusum(v)
    detected = p < alpha and abs(am - bm) >= min_effect
    direction = None if not detected else ("degraded" if am < bm else "improved")
    summary = (f"{metric} {direction} on {days[best_i]}: {bm:.3f} -> {am:.3f} (p={p:.2g})" if detected
               else f"{metric} stable (best split {bm:.3f} vs {am:.3f}, p={p:.2g})")
    return ChangePoint(metric, detected, str(days[best_i]) if detected else None, round(bm, 4), round(am, 4),
                       round(p, 6), direction, str(days[alarm]) if alarm is not None else None, summary)


def evidently_report(ref_rows: list[dict], cur_rows: list[dict], path: str | None = None,
                     ref_emb: np.ndarray | None = None, cur_emb: np.ndarray | None = None) -> str:
    """Evidently (0.7+) drift report for a RAG project, as self-contained HTML.

    Rows are per-trace numbers (retrieval top score, context size, latency, quality metrics). Each column gets
    Evidently's statistical drift test; query embeddings, when given, get Evidently's embedding-drift test.
    Requires `pip install "ragops-observatory[evidently]"`.
    """
    import pandas as pd
    from evidently import DataDefinition, Dataset, Report
    from evidently.presets import DataDriftPreset

    num_cols = sorted({k for r in ref_rows + cur_rows for k, v in r.items() if isinstance(v, (int, float))})
    rdf, cdf = pd.DataFrame(ref_rows, columns=num_cols), pd.DataFrame(cur_rows, columns=num_cols)
    num_cols = [c for c in num_cols if rdf[c].notna().sum() >= 5 and cdf[c].notna().sum() >= 5]
    if not num_cols and (ref_emb is None or cur_emb is None):
        raise ValueError("not enough data in both windows for an Evidently report (need 5+ rows per column)")
    metrics: list = [DataDriftPreset(columns=num_cols)] if num_cols else []
    embeddings = None
    if ref_emb is not None and cur_emb is not None and len(ref_emb) >= 5 and len(cur_emb) >= 5:
        from evidently.metrics import EmbeddingsDrift

        dims = min(ref_emb.shape[1], 64)  # project to the leading dimensions to keep the report light
        ecols = [f"q{i}" for i in range(dims)]
        rdf = pd.concat([rdf.reset_index(drop=True), pd.DataFrame(ref_emb[:, :dims], columns=ecols)], axis=1)
        cdf = pd.concat([cdf.reset_index(drop=True), pd.DataFrame(cur_emb[:, :dims], columns=ecols)], axis=1)
        embeddings = {"query_embedding": ecols}
        metrics.append(EmbeddingsDrift(embeddings_name="query_embedding"))
    dd = DataDefinition(numerical_columns=num_cols, embeddings=embeddings)
    snap = Report(metrics).run(current_data=Dataset.from_pandas(cdf, data_definition=dd),
                               reference_data=Dataset.from_pandas(rdf, data_definition=dd))
    import os
    import tempfile

    out = path or os.path.join(tempfile.mkdtemp(), "evidently.html")
    snap.save_html(out)
    if path is None:
        with open(out, encoding="utf-8") as f:
            return f.read()
    return out
