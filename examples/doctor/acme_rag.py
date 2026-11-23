"""Acme Cloud help bot: a small but complete RAG pipeline used by the RAG doctor walkthrough.

  retrieval   BM25 over word-window chunks of docs/*.md
  generation  extractive: answers with the context sentence that best matches the question
              (swap `generate` for an LLM call; everything else stays the same)

Every knob is read from the config dict, so `ragops doctor` can test fixes without code changes:
  chunk_size  words per chunk               (default 12: too small, answers get cut in half)
  top_k       chunks put in the prompt      (default 1: too few, the right chunk is often missed)
  abstain_when_unsure  say "I don't know" when nothing matches well (default false: it always guesses)
  temperature, grounded_prompt  accepted for parity with LLM pipelines (no effect in the extractive generator)
"""

from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

DOCS = Path(__file__).parent / "docs"
STOP = set("a an the of to and or in on for is are be can i my you your do does how what which when where who with "
           "at by from it this that as if per after before".split())
DEFAULTS = {"chunk_size": 12, "top_k": 1, "abstain_when_unsure": False}
ABSTAIN_BELOW = 1.0  # BM25 score under which nothing in the help center really matches


def _terms(text: str) -> list[str]:
    return [t.rstrip("s") for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


@lru_cache(maxsize=16)
def _index(chunk_size: int):
    chunks = []
    for f in sorted(DOCS.glob("*.md")):
        words = re.sub(r"^#.*$", "", f.read_text(encoding="utf-8"), flags=re.M).split()
        for i in range(0, len(words), chunk_size):
            chunks.append({"doc_id": f.name, "chunk_id": f"{f.name}#{i // chunk_size}",
                           "text": " ".join(words[i:i + chunk_size])})
    docs = [_terms(c["text"]) for c in chunks]
    df = Counter(t for d in docs for t in set(d))
    idf = {t: math.log(1 + (len(docs) - n + 0.5) / (n + 0.5)) for t, n in df.items()}
    avg = sum(map(len, docs)) / len(docs)
    return chunks, [Counter(d) for d in docs], [len(d) for d in docs], idf, avg


def retrieve(query: str, chunk_size: int, k: int) -> list[dict]:
    chunks, tfs, lens, idf, avg = _index(chunk_size)
    q = _terms(query)
    scores = []
    for tf, n in zip(tfs, lens):
        s = sum(idf.get(t, 0) * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * n / avg)) for t in q if t in tf)
        scores.append(s)
    order = sorted(range(len(chunks)), key=lambda i: -scores[i])[:k]
    return [{**chunks[i], "score": round(scores[i], 3), "rank": r + 1} for r, i in enumerate(order)]


def generate(query: str, contexts: list[dict]) -> str:
    q = set(_terms(query))
    sents = [s.strip() for c in contexts for s in re.split(r"(?<=[.!?])\s+", c["text"]) if s.strip()]
    if not sents:
        return "I don't know."
    best = max(sents, key=lambda s: len(q & set(_terms(s))))
    return best


def answer(query: str, config: dict) -> dict:
    cfg = {**DEFAULTS, **(config or {})}
    ctx = retrieve(query, int(cfg["chunk_size"]), int(cfg["top_k"]))
    if not ctx or (cfg["abstain_when_unsure"] and ctx[0]["score"] < ABSTAIN_BELOW):
        return {"answer": "I don't know: the help center does not cover that.", "contexts": ctx}
    return {"answer": generate(query, ctx), "contexts": ctx}
