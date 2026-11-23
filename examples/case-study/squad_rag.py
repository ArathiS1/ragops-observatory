"""A real, self-contained RAG service used for the case study: "WikiHelp", a Q&A bot over Wikipedia articles.

Retrieval: BM25 + hashing-embedding hybrid over chunked articles, optional overlap reranker.
Generation: extractive reader (default, no API key) or any OpenAI-compatible chat model (``generator: openai``).

Run it as an HTTP service (any RAGOps user can register an endpoint like this one):

    python examples/case-study/squad_rag.py serve --port 9100
    curl -X POST localhost:9100/ask -H 'content-type: application/json' -d '{"query": "In what country is Normandy?"}'

or import ``answer(query, config)`` as a Python pipeline: ``ragops test --pipeline examples/case-study/squad_rag.py:answer``.
"""


import json
import math
import os
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np

from ragops.benchmark_squad import download
from ragops.embeddings import HashingEmbedder
from ragops.textutil import STOPWORDS, _stem, content_tokens, coverage, sentences, tokens

DEFAULTS = {"chunk_words": 60, "top_k": 8, "context_k": 3, "rerank": "none", "generator": "extractive",
            "model": "gpt-4o-mini", "articles": 25, "edits": {}}
ABSTAIN = "I don't know: the knowledge base does not cover that."


def articles(n: int) -> list[dict]:
    data = json.loads(Path(os.environ.get("SQUAD_PATH", download())).read_text(encoding="utf-8"))["data"]
    return data[:n]


def held_out_articles(n: int, k: int = 5) -> list[dict]:
    data = json.loads(Path(os.environ.get("SQUAD_PATH", download())).read_text(encoding="utf-8"))["data"]
    return data[n:n + k]


def chunk_article(a: dict, words: int, edits: dict | None = None) -> list[dict]:
    """Paragraph-aware chunks with stable ids '<title>#<n>'. ``edits`` maps title -> [(old, new)] text replacements,
    which is how the case study simulates the CMS changing a page."""
    out, pos = [], 0
    for p in a["paragraphs"]:
        text = p["context"]
        rule = (edits or {}).get(a["title"], [])
        if rule == "renumber":  # the page was corrected: every year / figure on it changed
            text = re.sub(r"\b(\d{2,4})\b", lambda m: str(int(m.group(1)) + 1), text)
        else:
            for old, new in rule:
                text = text.replace(old, new)
        toks = text.split()
        for i in range(0, len(toks), words):
            out.append({"chunk_id": f"{a['title']}#{pos}", "doc_id": a["title"], "text": " ".join(toks[i:i + words])})
            pos += 1
    return out


def _terms(text: str) -> list[str]:
    return [_stem(t) for t in tokens(text) if t not in STOPWORDS and len(t) > 1]


class Index:
    def __init__(self, chunks: list[dict], k1: float = 1.4, b: float = 0.75):
        self.chunks = chunks
        self.docs = [_terms(c["text"]) for c in chunks]
        self.avgdl = sum(map(len, self.docs)) / max(1, len(self.docs))
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]
        self.k1, self.b = k1, b
        self.E = HashingEmbedder().embed([c["text"] for c in chunks])

    def bm25(self, q: list[str]) -> np.ndarray:
        s = np.zeros(len(self.docs))
        for i, (tf, d) in enumerate(zip(self.tf, self.docs)):
            norm = self.k1 * (1 - self.b + self.b * len(d) / self.avgdl)
            s[i] = sum(self.idf.get(t, 0) * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in q if t in tf)
        return s

    def search(self, query: str, k: int) -> list[dict]:
        bm = self.bm25(_terms(query))
        bm = bm / bm.max() if bm.max() > 0 else bm
        dense = self.E @ HashingEmbedder().embed([query])[0]
        score = 0.65 * bm + 0.35 * dense
        order = np.argsort(-score)[:k]
        return [{**self.chunks[i], "score": round(float(score[i]), 4)} for i in order]


@lru_cache(maxsize=8)
def _index(chunk_words: int, n_articles: int, edits_key: str) -> Index:
    edits = json.loads(edits_key)
    return Index([c for a in articles(n_articles) for c in chunk_article(a, chunk_words, edits)])


def corpus_documents(cfg: dict | None = None) -> list[dict]:
    """The documents exactly as this service indexes them (pre-chunked), for the RAGOps corpus registry."""
    c = {**DEFAULTS, **(cfg or {})}
    return [{"doc_id": a["title"], "title": a["title"], "chunks": chunk_article(a, c["chunk_words"], c["edits"])}
            for a in articles(c["articles"])]


def _extractive(query: str, ctx: list[dict]) -> str:
    q = content_tokens(query)
    best, score, src = None, 0.0, None
    for pos, c in enumerate(ctx):
        for s in sentences(c["text"]):
            sc = coverage(q, content_tokens(s)) - 0.02 * pos
            if sc > score:
                best, score, src = s, sc, c
    return ABSTAIN if best is None or score < 0.3 else f"{best} [{src['doc_id']}]"


def _openai(query: str, ctx: list[dict], model: str) -> str:  # pragma: no cover - needs a key
    import httpx

    sources = "\n\n".join(f"[{c['doc_id']}] {c['text']}" for c in ctx)
    r = httpx.post(f"{os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')}/chat/completions", timeout=60,
                   headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                   json={"model": model, "temperature": 0, "messages": [
                       {"role": "system", "content": "Answer only from the sources. Cite as [doc]. If the sources do "
                                                     "not contain the answer, say you don't know."},
                       {"role": "user", "content": f"Sources:\n{sources}\n\nQuestion: {query}"}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def answer(query: str, config: dict | None = None) -> dict:
    c = {**DEFAULTS, **(config or {})}
    idx = _index(c["chunk_words"], c["articles"], json.dumps(c["edits"], sort_keys=True))
    cands = idx.search(query, c["top_k"])
    ranked = cands
    if c["rerank"] == "overlap":
        q = content_tokens(query)
        ranked = sorted(cands, key=lambda x: -(coverage(q, content_tokens(x["text"])) + 0.3 * x["score"]))
    ctx = ranked[: c["context_k"]]
    text = _openai(query, ctx, c["model"]) if c["generator"] == "openai" else _extractive(query, ctx)
    return {"answer": text, "contexts": ctx, "retrieved": cands}


def serve(port: int = 9100) -> None:  # pragma: no cover - long running
    import uvicorn
    from fastapi import FastAPI, Request

    app = FastAPI(title="WikiHelp RAG (case-study app)")

    @app.post("/ask")
    async def ask(request: Request):
        body = await request.json()
        return answer(body["query"], body.get("config") or {})

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve(int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 9100)
    else:
        print(json.dumps(answer(" ".join(sys.argv[1:]) or "In what country is Normandy located?"), indent=2)[:1500])
