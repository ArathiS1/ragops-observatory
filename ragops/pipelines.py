"""Bring your own RAG: adapters that turn any RAG system into ``fn(query, config) -> dict``.

Every adapter returns the same shape::

    {"answer": str,
     "contexts":  [{"text", "doc_id"?, "chunk_id"?, "score"?, "doc_version"?}, ...],   # what the LLM saw
     "retrieved": [...same shape...]}                                                # optional, pre-rerank

Ways to plug a system in:

* **HTTP endpoint** (any language/framework): ``HttpPipeline("https://my-rag/ask")``. RAGOps POSTs
  ``{"query": ..., "config": {...}}`` and reads ``answer`` / ``contexts`` from the JSON response. Field names are
  configurable with dotted paths, e.g. ``answer_path="data.output.text"``, ``contexts_path="data.sources"``.
* **Python callable**: ``"my_app.rag:answer"`` or ``"path/to/file.py:answer"``.
* **LangChain** runnable, **LlamaIndex** query engine, **Haystack 2.x** pipeline: wrap the object directly.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from ragops.sdk.client import to_chunk


def _get(obj: Any, path: str | None, default=None):
    if not path:
        return default
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return default
        if cur is None:
            return default
    return cur


def normalise(out: Any) -> dict:
    """Coerce whatever a pipeline returned into the standard shape."""
    if isinstance(out, str):
        return {"answer": out, "contexts": [], "retrieved": []}
    if not isinstance(out, dict):
        out = {"answer": str(out)}
    ctx = out.get("contexts") or out.get("context") or out.get("sources") or out.get("documents") or []
    ret = out.get("retrieved") or []
    return {"answer": out.get("answer") or out.get("output") or out.get("response") or "",
            "contexts": [to_chunk(c, i + 1) for i, c in enumerate(ctx)],
            "retrieved": [to_chunk(c, i + 1) for i, c in enumerate(ret)]}


class PipelineError(RuntimeError):
    pass


def check_url_allowed(url: str) -> None:
    """Guard for server-side calls to user-registered endpoints (SSRF protection).

    RAGOPS_PIPELINE_ALLOWED_HOSTS: comma-separated host allowlist ("*" allows any host). Unless a host is explicitly
    allowed, private, loopback and link-local addresses are refused so a registered endpoint cannot probe the
    internal network or cloud metadata services.
    """
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise PipelineError("endpoint must be an http(s) URL")
    allowed = [h.strip() for h in os.environ.get("RAGOPS_PIPELINE_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if "*" in allowed or u.hostname in allowed:
        return
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(u.hostname, None)}
    except socket.gaierror as e:
        raise PipelineError(f"cannot resolve {u.hostname}") from e
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise PipelineError(f"{u.hostname} resolves to a private address; add it to RAGOPS_PIPELINE_ALLOWED_HOSTS "
                                "to allow it")


class HttpPipeline:
    def __init__(self, url: str, headers: dict | None = None, timeout: float = 60, answer_path: str = "answer",
                 contexts_path: str = "contexts", retrieved_path: str | None = "retrieved", query_field: str = "query",
                 extra_body: dict | None = None, guard: bool = False, transport: httpx.BaseTransport | None = None):
        if guard:
            check_url_allowed(url)
        self.url, self.answer_path, self.contexts_path = url, answer_path, contexts_path
        self.retrieved_path, self.query_field, self.extra_body = retrieved_path, query_field, extra_body or {}
        self.http = httpx.Client(timeout=timeout, headers=headers or {}, transport=transport)

    def __call__(self, query: str, config: dict | None = None) -> dict:
        body = {**self.extra_body, self.query_field: query}
        if config:
            body["config"] = config
        try:
            r = self.http.post(self.url, json=body)
        except httpx.ConnectError as e:
            raise PipelineError(f"could not connect to {self.url}: is your RAG server running on that address? "
                                f"Start it first, or use `kind: python` in ragops.yaml for a Python function ({e})") from e
        except httpx.TimeoutException as e:
            raise PipelineError(f"{self.url} did not answer in time; raise `timeout:` in ragops.yaml ({e})") from e
        if r.status_code >= 400:
            raise PipelineError(f"endpoint returned HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        return normalise({"answer": _get(data, self.answer_path, ""),
                          "contexts": _get(data, self.contexts_path, []) or [],
                          "retrieved": _get(data, self.retrieved_path, []) or []})


class PythonPipeline:
    def __init__(self, spec_or_fn: str | Callable):
        if isinstance(spec_or_fn, str):
            from ragops.gate import load_callable

            spec_or_fn = load_callable(spec_or_fn)
        self.fn = spec_or_fn

    def __call__(self, query: str, config: dict | None = None) -> dict:
        try:
            out = self.fn(query, config or {})
        except TypeError:
            out = self.fn(query)
        return normalise(out)


class LangChainPipeline:
    """Wrap a LangChain runnable, e.g. ``create_retrieval_chain(...)``. Reads ``answer`` and ``context`` keys."""

    def __init__(self, chain, input_key: str = "input"):
        self.chain, self.input_key = chain, input_key

    def __call__(self, query: str, config: dict | None = None) -> dict:
        out = self.chain.invoke({self.input_key: query})
        if isinstance(out, dict):
            return normalise({"answer": out.get("answer") or out.get("result") or out.get("output"),
                              "contexts": out.get("context") or out.get("source_documents") or []})
        return normalise(getattr(out, "content", out))


class LlamaIndexPipeline:
    """Wrap a LlamaIndex query engine (``index.as_query_engine()``)."""

    def __init__(self, query_engine):
        self.qe = query_engine

    def __call__(self, query: str, config: dict | None = None) -> dict:
        resp = self.qe.query(query)
        return normalise({"answer": str(getattr(resp, "response", resp)),
                          "contexts": list(getattr(resp, "source_nodes", []) or [])})


def haystack_documents(result: dict) -> tuple[list, list]:
    """Find (retrieved, context) Haystack Documents in a Haystack 2.x pipeline result."""
    retrieved, ranked = [], []
    for name, comp in (result or {}).items():
        docs = comp.get("documents") if isinstance(comp, dict) else None
        if docs:
            (ranked if ("rank" in name.lower() or "join" in name.lower()) else retrieved).extend(docs)
    return retrieved, ranked or retrieved


def haystack_answer(result: dict) -> str:
    for comp in (result or {}).values():
        if not isinstance(comp, dict):
            continue
        if comp.get("answers"):
            a = comp["answers"][0]
            return getattr(a, "data", None) or getattr(a, "answer", None) or str(a)
        if comp.get("replies"):
            r = comp["replies"][0]
            return getattr(r, "text", None) or getattr(r, "content", None) or str(r)
    return ""


def _hs_doc(d) -> dict:
    if isinstance(d, dict):
        return d
    meta = getattr(d, "meta", {}) or {}
    return {"text": getattr(d, "content", "") or "", "chunk_id": getattr(d, "id", None),
            "doc_id": meta.get("doc_id") or meta.get("file_path") or meta.get("source"),
            "doc_version": meta.get("doc_version"), "score": getattr(d, "score", None)}


class HaystackPipeline:
    """Wrap a Haystack 2.x ``Pipeline``. ``build_inputs(query)`` returns the ``pipeline.run`` input dict."""

    def __init__(self, pipeline, build_inputs: Callable[[str], dict]):
        self.p, self.build_inputs = pipeline, build_inputs

    def __call__(self, query: str, config: dict | None = None) -> dict:
        names = set(getattr(getattr(self.p, "graph", None), "nodes", {}) or {})
        try:  # include intermediate outputs so retriever/ranker documents are visible
            result = self.p.run(self.build_inputs(query), include_outputs_from=names) if names \
                else self.p.run(self.build_inputs(query))
        except TypeError:  # older Haystack without include_outputs_from
            result = self.p.run(self.build_inputs(query))
        retrieved, ctx = haystack_documents(result)
        return normalise({"answer": haystack_answer(result), "contexts": [_hs_doc(d) for d in ctx],
                          "retrieved": [_hs_doc(d) for d in retrieved]})


def spec_label(spec: dict) -> str:
    """What will actually be called, so reports never name the wrong pipeline."""
    kind = spec.get("kind", "http")
    return spec.get("url", "?") if kind == "http" else spec.get("callable", "?")


def from_spec(spec: dict) -> Callable[[str, dict], dict]:
    """Build a pipeline from a registry/config dict: {"kind": "http"|"python", ...}."""
    kind = spec.get("kind", "http")
    if kind == "http":
        return HttpPipeline(spec["url"], spec.get("headers"), spec.get("timeout", 60), spec.get("answer_path", "answer"),
                            spec.get("contexts_path", "contexts"), spec.get("retrieved_path", "retrieved"),
                            spec.get("query_field", "query"), spec.get("extra_body"), guard=spec.get("guard", False))
    if kind == "python":
        return PythonPipeline(spec["callable"])
    raise PipelineError(f"unknown pipeline kind {kind!r}")


def timed(pipeline: Callable) -> Callable[[str, dict], tuple[dict, float]]:
    def run(q: str, cfg: dict):
        t0 = time.perf_counter()
        out = pipeline(q, cfg)
        return out, (time.perf_counter() - t0) * 1000

    return run
