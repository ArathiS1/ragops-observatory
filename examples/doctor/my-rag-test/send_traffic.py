"""send_traffic.py - send live traffic from your RAG to RAGOps, like a real app would.

Every question is answered by my_rag.py and the result (question, answer, retrieved chunks, latency) is sent to
the RAGOps server as a trace. The worker then evaluates it and the Overview, RAG doctor, Traces and Drift pages fill.

Run (with `ragops serve` and `ragops worker` running):   python send_traffic.py
"""

import json
import os
import random
import sys
import time
import uuid

import httpx
from my_rag import answer

SERVER = os.environ.get("RAGOPS_URL", "http://localhost:8000")
KEY = os.environ.get("RAGOPS_API_KEY", "ragops-admin-dev-key")
PROJECT = os.environ.get("RAGOPS_PROJECT", "my-rag")
CONFIG = {"chunk_size": 60, "top_k": 3, "abstain_when_unsure": True}

# questions "users" ask: your test questions plus a few more, some the documents do not cover
QUESTIONS = [json.loads(line)["query"] for line in open("rag_tests.jsonl", encoding="utf-8") if line.strip()] + [
    "How long is the password reset link valid?",
    "Which payment methods do you accept?",
    "How many projects does the Free plan include?",
    "What happens when I exceed the rate limit?",
    "Can I change my workspace region later?",
    "How fast does support respond for Enterprise customers?",
    "Do you offer a student discount?",
    "Is there a mobile app?",
]

def register_documents(client: httpx.Client, project: str, folder: str) -> None:
    """Register the documents once per run (same as `ragops corpus push`), so RAGOps can tell "no document
    covers this" from "the search missed it". Unchanged documents are skipped by the server."""
    from ragops.cli import read_docs

    docs = read_docs(str(folder))
    r = client.post(f"/v1/projects/{project}/corpus", json={"documents": docs, "chunk_words": 120})
    if r.status_code >= 400:
        sys.exit(f"could not register documents from {folder}: HTTP {r.status_code} {r.text[:200]}")
    print(f"documents registered from {folder}: {r.json()}")


client = httpx.Client(base_url=SERVER, headers={"X-API-Key": KEY}, timeout=60)
# evaluate every trace (production projects usually sample a fraction to save judge cost)
client.patch(f"/v1/projects/{PROJECT}/settings", json={"sample_rate": 1.0}).raise_for_status()
if os.path.isdir("docs"):
    register_documents(client, PROJECT, "docs")

n = int(os.environ.get("N", "20"))
errors = 0
for i in range(n):
    q = random.choice(QUESTIONS)
    t0 = time.perf_counter()
    try:
        out = answer(q, CONFIG)
    except Exception as e:  # a real app would show an error and keep serving other users, not stop
        print(f"{i + 1:>3}/{n}  ERROR {q[:50]}: {type(e).__name__}: {str(e)[:100]}")
        errors += 1
        continue
    ms = (time.perf_counter() - t0) * 1000
    trace = {"id": uuid.uuid4().hex, "query": q, "answer": out["answer"], "contexts": out["contexts"],
             "latency_ms": round(ms, 1), "config_label": f"chunk{CONFIG['chunk_size']}-k{CONFIG['top_k']}"}
    for attempt in range(5):  # a real app retries telemetry briefly instead of crashing
        r = client.post(f"/v1/projects/{PROJECT}/traces", json=trace)
        if r.status_code < 500:
            break
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()
    print(f"{i + 1:>3}/{n}  sent  {q[:60]}")
print(f"\nDone: {n - errors} sent" + (f", {errors} failed in the RAG itself (see ERROR lines)" if errors else "") +
      ". Keep `ragops worker` running; refresh the Overview in a few seconds.")
