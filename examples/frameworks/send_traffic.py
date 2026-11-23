"""Send live traffic from one framework's RAG to the RAGOps dashboard, like a deployed app would.

    python send_traffic.py langchain          # project frameworks-langchain, 20 questions
    $env:N = "40"; python send_traffic.py haystack

Each framework goes to its own project (frameworks-<name>), so the Organisation page compares them side by side.
Keep `ragops serve` and `ragops worker` running.
"""
from __future__ import annotations

import importlib
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

name = (sys.argv[1] if len(sys.argv) > 1 else "langchain").lower()
spec = yaml.safe_load((HERE / f"{name}.yaml").read_text(encoding="utf-8"))
answer = importlib.import_module(f"{name}_rag").answer
CONFIG = spec.get("config") or {}
PROJECT = os.environ.get("RAGOPS_PROJECT_OVERRIDE") or spec["project"]
SERVER = os.environ.get("RAGOPS_URL", "http://localhost:8000")
KEY = os.environ.get("RAGOPS_API_KEY", "ragops-admin-dev-key")
LABEL = f"{name}-chunk{CONFIG.get('chunk_size')}-k{CONFIG.get('top_k')}"

def register_documents(client: httpx.Client, project: str, folder: str) -> None:
    """Register the documents once per run (same as `ragops corpus push`), so RAGOps can tell "no document
    covers this" from "the search missed it". Unchanged documents are skipped by the server."""
    from ragops.cli import read_docs

    docs = read_docs(str(folder))
    r = client.post(f"/v1/projects/{project}/corpus", json={"documents": docs, "chunk_words": 120})
    if r.status_code >= 400:
        sys.exit(f"could not register documents from {folder}: HTTP {r.status_code} {r.text[:200]}")
    print(f"documents registered from {folder}: {r.json()}")


QUESTIONS = [json.loads(line)["query"] for line in (HERE / spec["dataset"]).read_text(encoding="utf-8").splitlines() if line.strip()]

client = httpx.Client(base_url=SERVER, headers={"X-API-Key": KEY}, timeout=60)
if client.get(f"/v1/projects/{PROJECT}/settings").status_code == 404:
    sys.exit(f"project '{PROJECT}' does not exist: run `ragops create-project {PROJECT}` in the main folder first")
client.patch(f"/v1/projects/{PROJECT}/settings", json={"sample_rate": 1.0}).raise_for_status()
register_documents(client, PROJECT, HERE / spec["corpus"])

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
             "latency_ms": round(ms, 1), "config_label": LABEL}
    for attempt in range(5):  # a real app retries telemetry briefly instead of crashing
        r = client.post(f"/v1/projects/{PROJECT}/traces", json=trace)
        if r.status_code < 500:
            break
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()
    print(f"{i + 1:>3}/{n}  sent  {q[:60]}")
print(f"\nDone: {n - errors} traces sent to project '{PROJECT}'" + (f", {errors} questions failed in the RAG itself "
      "(see ERROR lines; usually the LLM returned an empty answer)" if errors else "") +
      ". Keep `ragops worker` running and refresh the dashboard.")
