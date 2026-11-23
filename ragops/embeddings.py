"""Pluggable embedders used for drift, clustering and corpus search.

* ``hashing`` (default): local, deterministic, no model download. Lexical, but good enough for drift
  statistics and coverage search. Fully reproducible, which matters for CI gates.
* ``openai``: any OpenAI-compatible embeddings endpoint.
* ``sentence-transformers``: local neural embeddings if the package is installed.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache

import numpy as np

from ragops.config import get_settings
from ragops.textutil import STOPWORDS, _stem, tokens

DIM = 384


class HashingEmbedder:
    name = "hashing-384"

    def __init__(self, dim: int = DIM):
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        toks = [_stem(t) for t in tokens(text) if t not in STOPWORDS]
        feats = list(toks)
        feats += [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for f in self._features(text or ""):
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                idx, sign = h % self.dim, 1.0 if (h >> 63) & 1 else -1.0
                weight = 0.5 if "_" in f else 1.0
                out[i, idx] += sign * weight
            n = np.linalg.norm(out[i])
            if n > 0:
                out[i] /= n
        return out


class OpenAIEmbedder:  # pragma: no cover - network
    def __init__(self, model: str, base_url: str | None = None):
        import os

        import httpx

        self.name = f"openai:{model}"
        self.model = model
        self.client = httpx.Client(
            base_url=base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"},
            timeout=60,
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        r = self.client.post("/embeddings", json={"model": self.model, "input": texts})
        r.raise_for_status()
        vecs = np.array([d["embedding"] for d in r.json()["data"]], dtype=np.float32)
        return vecs / np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None)


class STEmbedder:  # pragma: no cover - optional dependency
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer

        self.name = f"st:{model}"
        self.m = SentenceTransformer(model)

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.m.encode(texts, normalize_embeddings=True), dtype=np.float32)


@lru_cache(maxsize=1)
def get_embedder():
    s = get_settings()
    if s.embedder == "openai":
        return OpenAIEmbedder(s.embedding_model)
    if s.embedder == "sentence-transformers":
        return STEmbedder(s.embedding_model)
    return HashingEmbedder(s.embedding_dim)


def embed(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, get_settings().embedding_dim), dtype=np.float32)
    return get_embedder().embed(texts)


def embed_one(text: str) -> list[float]:
    return [round(float(x), 5) for x in embed([text])[0]]


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if a.size == 0 or b.size == 0:
        return np.zeros((len(a), len(b)))
    return a @ b.T
