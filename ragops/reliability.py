"""Reliability analytics added in 0.8: cost estimation from a versioned price table, ranking metrics,
claim-level evidence and failure patterns. Pure functions, no database access, so they are easy to test and reuse
from the CLI, the worker and the API.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from ragops.textutil import (
    _negations,
    content_tokens,
    coverage,
    extractive_support,
    is_abstention,
    numbers,
    sentences,
    top_terms,
)

# ---------------------------------------------------------------------------------------------- cost
# USD per 1M tokens (input, output). Prices change: this table is versioned, and every estimate records the
# version it used, so last month's costs stay reproducible after you update it. Override or extend it with
# RAGOPS_PRICING_FILE (JSON: {"version": "...", "models": {"model-name": [input, output]}}) or the project
# setting "pricing". Models not in the table are never guessed: their cost stays empty.
PRICING_VERSION = "builtin-2026-11"
BUILTIN_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}


def price_table(project_pricing: dict | None = None) -> tuple[str, dict[str, tuple[float, float]]]:
    version, prices = PRICING_VERSION, dict(BUILTIN_PRICES)
    path = os.environ.get("RAGOPS_PRICING_FILE")
    for extra in ([json.loads(Path(path).read_text(encoding="utf-8"))] if path and Path(path).exists() else []) + (
            [project_pricing] if project_pricing else []):
        version = str(extra.get("version") or version)
        for m, p in (extra.get("models") or {}).items():
            prices[m.lower()] = (float(p[0]), float(p[1]))
    return version, prices


def estimate_cost(model: str | None, prompt_tokens: int | None, completion_tokens: int | None,
                  project_pricing: dict | None = None) -> tuple[float | None, str | None]:
    """(cost in USD, price-table version), or (None, None) when the model or the token counts are unknown."""
    if not model or prompt_tokens is None and completion_tokens is None:
        return None, None
    version, prices = price_table(project_pricing)
    m = model.lower()
    p = prices.get(m) or next((v for k, v in sorted(prices.items(), key=lambda kv: -len(kv[0])) if m.startswith(k)), None)
    if p is None:
        return None, None
    return round(((prompt_tokens or 0) * p[0] + (completion_tokens or 0) * p[1]) / 1e6, 8), version


# ---------------------------------------------------------------------------------------------- ranking metrics
def ranking_metrics(ranked_doc_ids: list[str | None], expected: list[str], k: int | None = None) -> dict[str, float]:
    """Precision@K, Recall@K, MRR and nDCG@K of a ranked list against the expected (relevant) documents.
    Only meaningful when the expected documents are labelled; callers skip it otherwise."""
    rel = {e for e in expected if e}
    if not rel:
        return {}
    seen, ranked = set(), []
    for d in ranked_doc_ids:  # several chunks of one document count once, at their best rank
        if d and d not in seen:
            seen.add(d)
            ranked.append(d)
    k = k or max(len(ranked), 1)
    top = ranked[:k]
    hits = [1 if d in rel else 0 for d in top]
    first = next((i for i, h in enumerate(hits) if h), None)
    dcg = sum(h / math.log2(i + 2) for i, h in enumerate(hits))
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(rel), k)))
    return {"precision_at_k": round(sum(hits) / len(top), 4) if top else 0.0,
            "recall_at_k": round(sum(hits) / len(rel), 4),
            "mrr": round(1 / (first + 1), 4) if first is not None else 0.0,
            "ndcg": round(dcg / idcg, 4) if idcg else 0.0}


# ---------------------------------------------------------------------------------------------- claims
def claim_evidence(answer: str | None, chunks: list[dict]) -> list[dict]:
    """Split an answer into claims and link each one to the passage that supports or contradicts it.

    verdict: supported (the passage says it), contradicted (a passage about the same thing gives a different
    number or the opposite polarity), unsupported (no passage says it). Rule-based and explainable; the LLM judge's
    faithfulness score is stored separately. An "unsupported" claim is a lead to check, not proof of a
    hallucination: the evidence may simply not have been retrieved."""
    if not answer or is_abstention(answer):
        return []
    texts = [(c.get("text") or "") for c in chunks]
    out = []
    for claim in sentences(answer) or [answer]:
        ct, cn = content_tokens(claim), numbers(claim)
        if len(ct) < 2:
            continue
        best = (0.0, -1, "")
        for i, t in enumerate(texts):
            for s in sentences(t) or [t]:
                cov = coverage(ct, content_tokens(s))
                if cov > best[0]:
                    best = (cov, i, s)
        cov, idx, sent = best
        src = chunks[idx] if idx >= 0 else {}
        if extractive_support(claim, texts) or (cov >= 0.7 and (not cn or cn <= numbers(sent))
                                                 and _negations(claim) == _negations(sent)):
            verdict = "supported"
        elif cov >= 0.5 and ((cn and numbers(sent) and not cn <= numbers(sent))
                             or _negations(claim) != _negations(sent)):
            verdict = "contradicted"
        else:
            verdict = "unsupported"
        out.append({"claim": claim, "verdict": verdict, "overlap": round(cov, 2),
                    "chunk_id": src.get("chunk_id"), "doc_id": src.get("doc_id"),
                    "evidence": sent[:300] if verdict != "unsupported" else None})
    return out


def claim_summary(claims: list[dict]) -> dict:
    n = len(claims)
    c = {v: sum(1 for x in claims if x["verdict"] == v) for v in ("supported", "contradicted", "unsupported")}
    return {"claims": n, **c, "supported_share": round(c["supported"] / n, 4) if n else None}


# ---------------------------------------------------------------------------------------------- failure patterns
def failure_patterns(rows: list[dict], threshold: float = 0.6, max_patterns: int = 20) -> list[dict]:
    """Group failures that share a stage and ask about the same thing, so a team fixes a pattern once instead of
    reading traces one by one. rows: {id, query, label, doc_ids?}. Explainable: grouping is by stage, then by
    question similarity (embeddings), and each pattern is named by its most frequent terms."""
    if not rows:
        return []
    from ragops.embeddings import embed

    by_label: dict[str, list[dict]] = {}
    for r in rows:
        by_label.setdefault(r.get("label") or "unknown", []).append(r)
    patterns = []
    for label, items in by_label.items():
        vecs = embed([r["query"] for r in items])
        groups: list[list[int]] = []
        centroids = []
        for i, v in enumerate(vecs):
            sims = [float(v @ c) / ((float((c @ c) ** 0.5) or 1) * (float((v @ v) ** 0.5) or 1)) for c in centroids]
            j = max(range(len(sims)), key=sims.__getitem__) if sims else -1
            if j >= 0 and sims[j] >= threshold:
                groups[j].append(i)
                centroids[j] = centroids[j] + v
            else:
                groups.append([i])
                centroids.append(v.copy())
        for g in groups:
            members = [items[i] for i in g]
            docs: dict[str, int] = {}
            for m in members:
                for d in m.get("doc_ids") or []:
                    docs[d] = docs.get(d, 0) + 1
            patterns.append({
                "stage": label, "count": len(members),
                "topic": ", ".join(top_terms([m["query"] for m in members], 3)) or members[0]["query"][:60],
                "examples": [m["query"] for m in members[:3]], "trace_ids": [m.get("id") for m in members][:500],
                "documents": [d for d, _ in sorted(docs.items(), key=lambda kv: -kv[1])[:3]],
            })
    patterns.sort(key=lambda p: -p["count"])
    return patterns[:max_patterns]
