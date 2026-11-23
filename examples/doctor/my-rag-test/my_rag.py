"""my_rag.py - a complete, working RAG pipeline for RAGOps.

  1. Loads every .md / .txt file in the docs/ folder
  2. Splits them into chunks of `chunk_size` words
  3. Retrieves the `top_k` best chunks for the question (keyword search, BM25)
  4. Writes the answer with the LLM chosen by the RAG_LLM environment variable:

       RAG_LLM      key variable       default model               cost
       groq         GROQ_API_KEY       openai/gpt-oss-20b          free tier  (console.groq.com/keys)
       gemini       GEMINI_API_KEY     gemini-3.5-flash-lite       free tier  (aistudio.google.com/apikey)
       openai       OPENAI_API_KEY     gpt-4o-mini                 paid
       sarvam       SARVAM_API_KEY     sarvam-105b                 paid in rupees  (dashboard.sarvam.ai)
       ollama       (none)             llama3.1                    free, runs on your PC
       (not set)    -                  -                           picks the best sentence, no LLM

     Needs `pip install openai` for every option except "not set" (the library talks to all of them).
     Rate limits (HTTP 429) are retried automatically with back-off.

Every setting is read from `config`, so `ragops doctor` can test better values for you:
  chunk_size, top_k, abstain_when_unsure, temperature, model, max_tokens
"""

import math
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

DOCS_DIR = Path(__file__).parent / "docs"
STOP = set("a an the of to and or in on for is are be can i my you your do does how what which when where who "
           "with at by from it this that as if".split())


def words(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


# ------------------------------------------------------------------ 1-2. load and chunk the documents
@lru_cache(maxsize=8)
def build_index(chunk_size):
    chunks = []
    for f in sorted(DOCS_DIR.glob("*")):
        if f.suffix.lower() not in (".md", ".txt"):
            continue
        tokens = f.read_text(encoding="utf-8", errors="ignore").split()
        for i in range(0, len(tokens), chunk_size):
            chunks.append({"doc_id": f.name, "chunk_id": f"{f.name}#{i // chunk_size}",
                           "text": " ".join(tokens[i:i + chunk_size])})
    docs = [Counter(words(c["text"])) for c in chunks]
    df = Counter(w for d in docs for w in d)
    n = max(len(docs), 1)
    idf = {w: math.log(1 + (n - k + 0.5) / (k + 0.5)) for w, k in df.items()}
    avg = sum(sum(d.values()) for d in docs) / n if docs else 1
    return chunks, docs, idf, avg


# ------------------------------------------------------------------ 3. retrieve
def retrieve(question, chunk_size, top_k):
    chunks, docs, idf, avg = build_index(chunk_size)
    q = words(question)
    scored = []
    for c, d in zip(chunks, docs):
        length = sum(d.values()) or 1
        s = sum(idf.get(w, 0) * d[w] * 2.2 / (d[w] + 1.2 * (0.25 + 0.75 * length / avg)) for w in q if w in d)
        scored.append({**c, "score": round(s, 3)})
    return sorted(scored, key=lambda c: -c["score"])[:top_k]


# ------------------------------------------------------------------ 4. generate
PROMPT = ("Answer the question using ONLY the context below.\n"
          "- Answer in one or two complete sentences that repeat the key facts (numbers, names, steps).\n"
          "- If the context does not state the answer, reply exactly: " + "{fallback}" + "\n"
          "- Never say that something does not exist, is not planned or has no date unless the context says so.\n"
          "\nContext:\n{context}\n\nQuestion: {question}")
FALLBACK = "I don't know: the documents do not cover that."


PROVIDERS = {  # name: (base_url, environment variable holding the key, default model)
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-20b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-3.5-flash-lite"),
    "openai": (None, "OPENAI_API_KEY", "gpt-4o-mini"),
    "ollama": ("http://localhost:11434/v1", None, "llama3.1"),
    "sarvam": ("https://api.sarvam.ai/v1", "SARVAM_API_KEY", "sarvam-105b"),
}


@lru_cache(maxsize=4)
def llm_client(provider):
    from openai import OpenAI

    base_url, key_var, _ = PROVIDERS[provider]
    base_url = os.environ.get("LLM_BASE_URL", base_url)
    key = os.environ.get(key_var, "") if key_var else "ollama"
    if not key:
        raise RuntimeError(f"RAG_LLM={provider} needs the {key_var} environment variable")
    # Sarvam's own docs authenticate with an api-subscription-key header; send it as well as the Bearer key
    headers = {"api-subscription-key": key} if provider == "sarvam" else None
    # max_retries: the client waits and retries on rate limits (429) and temporary server errors
    return OpenAI(base_url=base_url, api_key=key, max_retries=6, timeout=120, default_headers=headers)


def clean(text):
    """Remove the model's <think>...</think> notes and surrounding spaces."""
    text = re.sub(r"<think>.*?(</think>|$)", "", text or "", flags=re.S)
    return text.strip()


def generate(question, chunks, config):
    provider = os.environ.get("RAG_LLM", "").lower()
    if provider:
        if provider not in PROVIDERS:
            raise RuntimeError(f"unknown RAG_LLM={provider!r}; use one of {', '.join(PROVIDERS)}")
        context = "\n\n".join(f"[{c['doc_id']}] {c['text']}" for c in chunks)
        prompt = PROMPT.format(context=context, question=question, fallback=FALLBACK)
        # Sarvam-105b thinks before it answers; with too few tokens it runs out while thinking and returns
        # nothing. Give it room, and try again once if the reply is still empty.
        for _ in range(2):
            reply = llm_client(provider).chat.completions.create(
                model=config.get("model", PROVIDERS[provider][2]),
                temperature=config.get("temperature", 0.0),
                max_tokens=int(config.get("max_tokens", 8192)),
                messages=[{"role": "user", "content": prompt}])
            text = clean(reply.choices[0].message.content)
            if text:
                return text
        return FALLBACK  # never show the user a blank message
    # no LLM chosen: answer with the sentence that best matches the question
    q = set(words(question))
    sentences = [s.strip() for c in chunks for s in re.split(r"(?<=[.!?])\s+", c["text"]) if s.strip()]
    return max(sentences, key=lambda s: len(q & set(words(s))), default="I don't know.")


# ------------------------------------------------------------------ the function RAGOps calls
def answer(query: str, config: dict) -> dict:
    chunk_size = int(config.get("chunk_size", 60))
    top_k = int(config.get("top_k", 3))
    chunks = retrieve(query, chunk_size, top_k)
    if not chunks or (config.get("abstain_when_unsure", True) and chunks[0]["score"] < 1.0):
        return {"answer": FALLBACK, "contexts": chunks}
    return {"answer": generate(query, chunks, config), "contexts": chunks}
