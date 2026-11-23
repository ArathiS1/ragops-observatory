"""Shared pieces for the framework examples: the same documents, LLM, embeddings and prompt for every framework,
so differences in the results come from the framework's retrieval and orchestration, not from the setup.

LLM: the same providers as my_rag.py, chosen with RAG_LLM (sarvam | groq | gemini | openai | ollama).
Embeddings: fastembed (BAAI/bge-small-en-v1.5, runs locally on the CPU, ~70 MB download on first use).
If the model cannot be downloaded, a word-hashing embedding is used instead and a warning is printed.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = Path(os.environ.get("RAG_DOCS", HERE.parent / "doctor" / "docs"))

PROMPT = ("Answer the question using ONLY the context below. If the context does not contain the answer, "
          "reply exactly: I don't know.\n\nContext:\n{context}\n\nQuestion: {question}")
ABSTAIN = "I don't know."

PROVIDERS = {  # name: (base_url, environment variable holding the key, default model)
    "sarvam": ("https://api.sarvam.ai/v1", "SARVAM_API_KEY", "sarvam-105b"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-20b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-3.5-flash-lite"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "ollama": ("http://localhost:11434/v1", None, "llama3.1"),
}


# ----------------------------------------------------------------------------------------------- LLM settings
def llm_settings(config: dict | None = None) -> dict:
    """base_url, api_key and model for the chosen provider (RAG_LLM, default sarvam)."""
    config = config or {}
    provider = os.environ.get("RAG_LLM", "sarvam").lower()
    if provider not in PROVIDERS:
        raise RuntimeError(f"unknown RAG_LLM={provider!r}; use one of {', '.join(PROVIDERS)}")
    base_url, key_var, model = PROVIDERS[provider]
    key = os.environ.get(key_var, "") if key_var else "ollama"
    if not key:
        raise RuntimeError(f"RAG_LLM={provider} needs the {key_var} environment variable")
    return {"provider": provider, "base_url": os.environ.get("LLM_BASE_URL", base_url), "api_key": key,
            "model": config.get("model", model), "max_tokens": int(config.get("max_tokens", 4096)),
            "temperature": float(config.get("temperature", 0.0))}


def clean_answer(text: str | None) -> str:
    """Drop the <think> block reasoning models (Sarvam 105B, gpt-oss, Qwen) put before the answer."""
    text = re.sub(r"<think>.*?(</think>|$)", "", text or "", flags=re.S).strip()
    if not text:
        raise RuntimeError("the LLM returned an empty answer; raise max_tokens in ragops.yaml config")
    return text


def generate_with_retry(call, attempts: int = 2) -> str:
    """Run call() (returns the raw LLM text) and clean it; ask again once if the answer comes back empty.
    Reasoning models sometimes spend the whole token budget thinking and reply with nothing; a second try
    usually answers. RAGOps found this as intermittent "pipeline error" cases before this retry existed."""
    for i in range(attempts):
        try:
            return clean_answer(call())
        except RuntimeError:
            if i == attempts - 1:
                raise
    raise RuntimeError("unreachable")


# ----------------------------------------------------------------------------------------------- documents
def load_docs() -> list[tuple[str, str]]:
    """(file name, text) for every .md/.txt file in DOCS."""
    files = sorted(p for p in DOCS.glob("*") if p.suffix in (".md", ".txt"))
    if not files:
        raise RuntimeError(f"no .md/.txt documents in {DOCS} (set RAG_DOCS to your folder)")
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


# ----------------------------------------------------------------------------------------------- embeddings
_DIM = 384


def _hash_embed(text: str) -> list[float]:
    v = [0.0] * _DIM
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % _DIM] += 1.0 if (h >> 9) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@lru_cache(maxsize=1)
def _model():
    if os.environ.get("RAG_EMBEDDINGS", "fastembed") == "hash":
        return None
    try:
        from fastembed import TextEmbedding

        return TextEmbedding(os.environ.get("RAG_EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
    except Exception as e:  # no internet for the first download, or fastembed not installed
        print(f"warning: fastembed unavailable ({type(e).__name__}: {str(e)[:120]}); "
              "using word-hashing embeddings instead", file=sys.stderr)
        return None


def embed(texts: list[str]) -> list[list[float]]:
    m = _model()
    if m is None:
        return [_hash_embed(t) for t in texts]
    return [list(map(float, v)) for v in m.embed(texts)]


def embedding_name() -> str:
    return "hash" if _model() is None else os.environ.get("RAG_EMBED_MODEL", "BAAI/bge-small-en-v1.5")


# ----------------------------------------------------------------------------------------------- output
def chunk(text: str, doc_id: str, chunk_id: str, score: float | None = None) -> dict:
    """One context chunk in the shape RAGOps reads; doc_id = file name so expected_doc_ids match."""
    out = {"text": text, "doc_id": doc_id, "chunk_id": chunk_id}
    if score is not None:
        out["score"] = round(float(score), 4)
    return out
