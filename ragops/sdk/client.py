"""Python SDK.

    from ragops import RagopsClient
    rg = RagopsClient("http://localhost:8000", api_key="rgo_...", project="support-bot")

    with rg.trace(query, config_label="chunk512-bge") as t:
        docs = retriever.search(query)
        t.retrieved(docs)                  # dicts, strings, LangChain Documents or LlamaIndex nodes
        top = reranker(query, docs)[:4]
        t.context(top)                     # what the LLM actually sees
        answer = llm(query, top)
        t.generation(answer, model="gpt-4o-mini", prompt_tokens=812, completion_tokens=96)

Design rules: the SDK never raises into your application, never blocks the request path (a background thread
batches and retries), and drops data rather than growing memory without bound if the server is unreachable.
"""

from __future__ import annotations

import atexit
import contextvars
import functools
import logging
import queue
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

log = logging.getLogger("ragops.sdk")
_current: contextvars.ContextVar["TraceBuilder | None"] = contextvars.ContextVar("ragops_trace", default=None)


def current_trace() -> "TraceBuilder | None":
    return _current.get()


def to_chunk(d: Any, rank: int) -> dict:
    """Normalise retriever outputs from common frameworks."""
    if isinstance(d, str):
        return {"text": d, "rank": rank}
    if isinstance(d, tuple) and len(d) == 2:  # (doc, score)
        c = to_chunk(d[0], rank)
        c["score"] = float(d[1])
        return c
    if isinstance(d, dict):
        meta = d.get("metadata") or {}
        return {"text": d.get("text") or d.get("content") or d.get("page_content"),
                "chunk_id": d.get("chunk_id") or d.get("id") or meta.get("chunk_id"),
                "doc_id": d.get("doc_id") or meta.get("doc_id") or meta.get("source"),
                "doc_version": d.get("doc_version") or meta.get("doc_version"),
                "score": d.get("score"), "rank": rank}
    if hasattr(d, "page_content"):  # LangChain Document
        meta = getattr(d, "metadata", {}) or {}
        return {"text": d.page_content, "chunk_id": meta.get("chunk_id") or getattr(d, "id", None),
                "doc_id": meta.get("doc_id") or meta.get("source"), "doc_version": meta.get("doc_version"),
                "score": meta.get("score"), "rank": rank}
    if hasattr(d, "node"):  # LlamaIndex NodeWithScore
        node = d.node
        meta = getattr(node, "metadata", {}) or {}
        return {"text": node.get_content() if hasattr(node, "get_content") else getattr(node, "text", ""),
                "chunk_id": getattr(node, "node_id", None),
                "doc_id": meta.get("doc_id") or getattr(node, "ref_doc_id", None) or meta.get("file_name"),
                "doc_version": meta.get("doc_version"), "score": getattr(d, "score", None), "rank": rank}
    return {"text": str(d), "rank": rank}


class TraceBuilder:
    def __init__(self, client: "RagopsClient", query: str, config_label: str | None = None,
                 config: dict | None = None, session_id: str | None = None, metadata: dict | None = None,
                 trace_id: str | None = None):
        self.client = client
        self.id = trace_id or uuid.uuid4().hex
        self.data: dict[str, Any] = {"id": self.id, "query": query, "config_label": config_label, "config": config,
                                     "session_id": session_id, "metadata": metadata or {}, "retrieved": [],
                                     "reranked": [], "contexts": [],
                                     "environment": getattr(client, "environment", None),
                                     "timestamp": datetime.now(timezone.utc).isoformat()}
        self._t0 = time.perf_counter()
        self._token = None

    def retrieved(self, docs) -> "TraceBuilder":
        self.data["retrieved"] = [to_chunk(d, i + 1) for i, d in enumerate(docs or [])]
        return self

    def reranked(self, docs) -> "TraceBuilder":
        self.data["reranked"] = [to_chunk(d, i + 1) for i, d in enumerate(docs or [])]
        return self

    def context(self, docs) -> "TraceBuilder":
        self.data["contexts"] = [to_chunk(d, i + 1) for i, d in enumerate(docs or [])]
        return self

    def generation(self, answer: str, model: str | None = None, prompt_tokens: int | None = None,
                   completion_tokens: int | None = None, cost_usd: float | None = None) -> "TraceBuilder":
        self.data.update(answer=answer, model=model, prompt_tokens=prompt_tokens,
                         completion_tokens=completion_tokens, cost_usd=cost_usd)
        return self

    def expected(self, answer: str | None = None, doc_ids: list[str] | None = None) -> "TraceBuilder":
        if answer:
            self.data["expected_answer"] = answer
        if doc_ids:
            self.data["expected_doc_ids"] = doc_ids
        return self

    # ---- per-step context managers: time each stage and record its output -------------------------------------
    def retrieve(self) -> "_Step":
        """``with t.retrieve() as s: s.output(docs)`` records candidates and the retrieval latency."""
        return _Step(self, "retrieve", self.retrieved)

    def rerank(self) -> "_Step":
        return _Step(self, "rerank", self.reranked)

    def build_context(self) -> "_Step":
        return _Step(self, "context", self.context)

    def generate(self, model: str | None = None) -> "_Step":
        """``with t.generate(model="gpt-4o") as s: s.output(answer, prompt_tokens=..., completion_tokens=...)``"""
        return _Step(self, "generate", lambda answer, **kw: self.generation(answer, model=model, **kw))

    def set(self, **meta) -> "TraceBuilder":
        self.data["metadata"].update(meta)
        return self

    def end(self) -> None:
        self.data.setdefault("latency_ms", round((time.perf_counter() - self._t0) * 1000, 2))
        if not self.data["contexts"]:
            self.data["contexts"] = self.data["reranked"] or self.data["retrieved"]
        self.client._submit({k: v for k, v in self.data.items() if v is not None})

    def __enter__(self):
        self._token = _current.set(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.data["metadata"]["error"] = f"{exc_type.__name__}: {exc}"
        try:
            self.end()
        finally:
            if self._token is not None:
                _current.reset(self._token)
        return False


class _Step:
    def __init__(self, trace: "TraceBuilder", name: str, record: Callable):
        self.trace, self.name, self._record = trace, name, record
        self._t0 = 0.0

    def output(self, value, **kw) -> None:
        self._record(value, **kw)

    def __enter__(self) -> "_Step":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = round((time.perf_counter() - self._t0) * 1000, 2)
        self.trace.data["metadata"].setdefault("step_latency_ms", {})[self.name] = ms
        if exc is not None:
            self.trace.data["metadata"]["failed_step"] = self.name
        return False


class RagopsClient:
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str | None = None, project: str | None = None,
                 flush_interval: float = 2.0, max_batch: int = 50, max_queue: int = 10_000, timeout: float = 10.0,
                 sample_rate: float = 1.0, enabled: bool = True, sync: bool = False, environment: str | None = None):
        import os

        # dev | staging | production: keeps test traffic out of production dashboards and SLOs
        self.environment = environment or os.environ.get("RAGOPS_ENVIRONMENT") or None
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("RAGOPS_API_KEY", "")
        self.project = project or os.environ.get("RAGOPS_PROJECT", "default")
        self.enabled = enabled
        self.sample_rate = sample_rate
        self.sync = sync
        self.max_batch = max_batch
        self.flush_interval = flush_interval
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout,
                                  headers={"Authorization": f"Bearer {self.api_key}"})
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.dropped = 0
        if enabled and not sync:
            self._thread = threading.Thread(target=self._loop, name="ragops-flusher", daemon=True)
            self._thread.start()
            atexit.register(self.close)

    # ---------------------------------------------------------------- tracing
    def trace(self, query: str, config_label: str | None = None, config: dict | None = None,
              session_id: str | None = None, **metadata) -> TraceBuilder:
        return TraceBuilder(self, query, config_label, config, session_id, metadata)

    def log(self, query: str, answer: str, contexts: list, **kw) -> str:
        """One-shot logging for code that already has everything."""
        t = self.trace(query, kw.pop("config_label", None), kw.pop("config", None), kw.pop("session_id", None))
        t.retrieved(kw.pop("retrieved", None) or [])
        t.context(contexts)
        t.generation(answer, kw.pop("model", None), kw.pop("prompt_tokens", None), kw.pop("completion_tokens", None),
                     kw.pop("cost_usd", None))
        if "latency_ms" in kw:
            t.data["latency_ms"] = kw.pop("latency_ms")
        t.data["metadata"].update(kw)
        t.end()
        return t.id

    def _submit(self, payload: dict) -> None:
        if not self.enabled or (self.sample_rate < 1 and random.random() > self.sample_rate):
            return
        if self.sync:
            self._send([payload])
            return
        try:
            self._q.put_nowait(payload)
        except queue.Full:
            self.dropped += 1

    # ---------------------------------------------------------------- other APIs (synchronous)
    def feedback(self, trace_id: str, score: float, comment: str | None = None) -> bool:
        return self._post(f"/v1/projects/{self.project}/traces/{trace_id}/feedback", {"score": score, "comment": comment})

    def upsert_corpus(self, documents: list[dict], chunk_words: int = 120, delete_missing: bool = False) -> dict | None:
        r = self._request("POST", f"/v1/projects/{self.project}/corpus",
                          {"documents": documents, "chunk_words": chunk_words, "delete_missing": delete_missing})
        return r.json() if r is not None else None

    def register_pipeline(self, name: str, url: str, **spec) -> dict | None:
        """Register your RAG's HTTP endpoint so it can be tested from the UI (see ragops.schemas.PipelineIn)."""
        r = self._request("POST", f"/v1/projects/{self.project}/pipelines", {"name": name, "url": url, **spec})
        return r.json() if r is not None else None

    def start_run(self, pipeline: str, golden_set: str | None = None, cases: list[dict] | None = None,
                  config: dict | None = None, name: str | None = None) -> dict | None:
        r = self._request("POST", f"/v1/projects/{self.project}/runs", {
            "pipeline": pipeline, "golden_set": golden_set, "cases": cases or [], "config": config or {}, "name": name})
        return r.json() if r is not None else None

    def get_run(self, run_id: str) -> dict | None:
        r = self._request("GET", f"/v1/projects/{self.project}/runs/{run_id}")
        return r.json() if r is not None else None

    def golden_set(self, name: str) -> dict | None:
        r = self._request("GET", f"/v1/projects/{self.project}/golden-sets/{name}/export")
        return r.json() if r is not None else None

    def guard(self, answer: str, contexts: list, question: str = "", policy: str = "rewrite",
              min_supported: float = 1.0, use_llm: bool = False, fail_open: bool = True) -> dict:
        """Check an answer against its passages before showing it. Returns the server's decision
        (`allow | block | rewrite | flag`) and the answer to show. If the server cannot be reached, the answer is
        allowed (`fail_open=True`, the default for availability) or replaced by a refusal (`fail_open=False`)."""
        texts = [c if isinstance(c, str) else (to_chunk(c, i)["text"] or "") for i, c in enumerate(contexts, 1)]
        r = self._request("POST", f"/v1/projects/{self.project}/guard",
                          {"answer": answer, "contexts": texts, "question": question, "policy": policy,
                           "min_supported": min_supported, "use_llm": use_llm})
        if r is not None:
            return r.json()
        if fail_open:
            return {"decision": "allow", "answer": answer, "supported_ratio": None, "statements": [],
                    "checked_by": "unavailable"}
        from ragops.guard import FALLBACK

        return {"decision": "rewrite", "answer": FALLBACK, "supported_ratio": None, "statements": [],
                "checked_by": "unavailable"}

    # ---------------------------------------------------------------- transport
    def _request(self, method: str, path: str, body: dict | None = None):
        for attempt in range(3):
            try:
                r = self._http.request(method, path, json=body)
                if r.status_code < 500:
                    if r.status_code >= 400:
                        log.warning("ragops %s %s -> %s %s", method, path, r.status_code, r.text[:200])
                        return None
                    return r
            except httpx.HTTPError as e:
                log.debug("ragops transport error: %s", e)
            time.sleep(0.5 * 2**attempt)
        return None

    def _post(self, path: str, body: dict) -> bool:
        return self._request("POST", path, body) is not None

    def _send(self, batch: list[dict]) -> bool:
        ok = self._post(f"/v1/projects/{self.project}/traces/batch", {"traces": batch})
        if not ok:
            self.dropped += len(batch)
        return ok

    def flush(self) -> None:
        batch = []
        while True:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
            if len(batch) >= self.max_batch:
                self._send(batch)
                batch = []
        if batch:
            self._send(batch)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.flush_interval)
            try:
                self.flush()
            except Exception as e:  # pragma: no cover
                log.debug("flush failed: %s", e)

    def close(self) -> None:
        self._stop.set()
        try:
            self.flush()
        finally:
            self._http.close()


def observe_rag(client: RagopsClient, config_label: str | None = None, query_arg: str = "query"):
    """Decorator. The wrapped function receives the query (positional arg 0 or `query=`) and returns either
    a dict ``{"answer", "contexts", "retrieved"?, "model"?}`` or a string; in the latter case call
    ``ragops.sdk.client.current_trace().context(...)`` inside the function to record contexts."""

    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            q = kwargs.get(query_arg, args[0] if args else "")
            with client.trace(str(q), config_label=config_label) as t:
                out = fn(*args, **kwargs)
                if isinstance(out, dict):
                    if out.get("retrieved"):
                        t.retrieved(out["retrieved"])
                    if out.get("contexts"):
                        t.context(out["contexts"])
                    t.generation(out.get("answer", ""), out.get("model"))
                else:
                    t.generation(str(out))
                return out

        return wrapper

    return deco
