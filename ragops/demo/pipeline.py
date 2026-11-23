"""A small, deterministic RAG pipeline used by the demo, the benchmark and the CI-gate example.

It needs no API key: retrieval uses the local hashing embedder and "generation" is extractive (it quotes the best
supporting sentence and cites the document). Config switches let us reproduce real failure modes on purpose.

config keys:
    chunk_words      chunk size used to build the index (default 45)
    top_k            retriever candidates (default 6)
    rerank           "none" | "good" | "bad"   ("bad" prefers long chunks: a misconfigured reranker)
    context_k        chunks placed in the prompt (default 3)
    shuffle_context  bury the best chunk in the middle of a long context ("lost in the middle")
    hallucinate      probability of corrupting a figure in the answer
    exclude_docs     doc_ids missing from the serving index (a partially built index)
    stale_docs       {doc_id: old_text} served instead of the current text (index not refreshed)
"""

from __future__ import annotations

import hashlib
import random
import re
from functools import lru_cache

import numpy as np

from ragops.demo.corpus import DOCS
from ragops.embeddings import HashingEmbedder
from ragops.textutil import content_tokens, coverage, sentences

_EMB = HashingEmbedder()
ABSTAIN = "I couldn't find information about that in the Nimbus documentation."


def chunk(doc_id: str, text: str, words: int, version: int = 1) -> list[dict]:
    from ragops.services_chunking import chunk_text

    return [{"chunk_id": f"{doc_id}#{i}", "doc_id": doc_id, "doc_version": version, "text": t}
            for i, t in enumerate(chunk_text(text, words))]


class Index:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.E = _EMB.embed([c["text"] for c in chunks])

    def search(self, query: str, k: int) -> list[dict]:
        q = _EMB.embed([query])[0]
        lex = np.array([coverage(content_tokens(query), content_tokens(c["text"])) for c in self.chunks])
        s = 0.6 * (self.E @ q) + 0.4 * lex
        order = np.argsort(-s)[:k]
        return [{**self.chunks[i], "score": round(float(s[i]), 4)} for i in order]


@lru_cache(maxsize=32)
def _index(key: str, chunk_words: int, exclude: tuple, stale: tuple) -> Index:
    stale_map = dict(stale)
    chunks = []
    for d in DOCS:
        if d["doc_id"] in exclude:
            continue
        if d["doc_id"] in stale_map:
            text, ver = stale_map[d["doc_id"]]
            chunks += chunk(d["doc_id"], text, chunk_words, ver)
        else:
            chunks += chunk(d["doc_id"], d["text"], chunk_words, d.get("version", 1))
    return Index(chunks)


def get_index(config: dict, docs_override: dict | None = None) -> Index:
    stale = tuple(sorted((k, (v["text"], v["version"])) for k, v in (config.get("serve_docs") or {}).items()))
    return _index("x", config.get("chunk_words", 45), tuple(sorted(config.get("exclude_docs", []))), stale)


def _rng(query: str, salt: str) -> random.Random:
    return random.Random(int(hashlib.md5((query + salt).encode()).hexdigest()[:8], 16))


def generate(query: str, contexts: list[dict], cfg: dict, salt: str = "") -> str:
    q = content_tokens(query)
    best, best_s, best_c = None, 0.0, None
    for pos, c in enumerate(contexts[:2] if cfg.get("shuffle_context") else contexts):
        for s in sentences(c["text"]):
            sc = coverage(q, content_tokens(s)) - 0.01 * pos
            if sc > best_s:
                best, best_s, best_c = s, sc, c
    if best is None or best_s < 0.34:
        # real bots sometimes guess instead of saying "I don't know"
        guess = cfg.get("guess_when_unsure", 0.0)
        if best is not None and _rng(query, salt + "g").random() < guess:
            return f"{best} [{best_c['doc_id']}]"
        return ABSTAIN
    answer = best
    if _rng(query, salt + "h").random() < cfg.get("hallucinate", 0.0):
        nums = re.findall(r"\d+", answer)
        if nums:
            answer = answer.replace(nums[0], str(int(nums[0]) + 7), 1)
        else:
            answer += " This takes effect within 45 days."
    return f"{answer} [{best_c['doc_id']}]"


def run(query: str, config: dict | None = None, salt: str = "") -> dict:
    cfg = {"chunk_words": 45, "top_k": 6, "rerank": "none", "context_k": 3, **(config or {})}
    idx = get_index(cfg)
    cands = idx.search(query, cfg["top_k"])
    ranked = list(cands)
    if cfg["rerank"] == "bad":
        ranked = sorted(cands, key=lambda c: -len(c["text"]))
    elif cfg["rerank"] == "good":
        q = content_tokens(query)
        ranked = sorted(cands, key=lambda c: -coverage(q, content_tokens(c["text"])))
    ctx = ranked[: cfg["context_k"]]
    if cfg.get("shuffle_context") and len(ctx) > 3:
        best = ctx[0]
        rest = ctx[1:]
        _rng(query, salt + "s").shuffle(rest)
        mid = len(ctx) // 2 + 1
        ctx = rest[:mid] + [best] + rest[mid:]
    answer = generate(query, ctx, cfg, salt)
    return {"answer": answer, "retrieved": cands, "contexts": ctx}


def pipeline(query: str, config: dict) -> dict:
    """Entry point for `ragops gate --pipeline ragops.demo.pipeline:pipeline`."""
    return run(query, config)
