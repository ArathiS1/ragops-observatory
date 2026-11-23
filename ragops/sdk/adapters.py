"""Framework adapters. Each one is optional and imports its framework lazily."""

from __future__ import annotations

from typing import Any

from ragops.sdk.client import RagopsClient, TraceBuilder


def langchain_handler(client: RagopsClient, config_label: str | None = None):
    """LangChain callback handler: captures the query, retrieved documents and the final LLM output.

        chain.invoke(question, config={"callbacks": [langchain_handler(rg, "v3")]})
    """
    from langchain_core.callbacks import BaseCallbackHandler

    class RagopsCallbackHandler(BaseCallbackHandler):
        def __init__(self):
            self._trace: TraceBuilder | None = None
            self._root = None

        def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kw):
            if parent_run_id is None:
                q = inputs if isinstance(inputs, str) else (inputs.get("question") or inputs.get("query")
                                                            or inputs.get("input") or str(inputs))
                self._trace = client.trace(str(q), config_label=config_label)
                self._root = run_id

        def on_retriever_end(self, documents, **kw):
            if self._trace is not None:
                self._trace.retrieved(documents).context(documents)

        def on_llm_end(self, response, **kw):
            if self._trace is None:
                return
            try:
                gen = response.generations[0][0]
                text = getattr(gen, "text", None) or gen.message.content
            except Exception:
                return
            usage = (response.llm_output or {}).get("token_usage", {}) if response.llm_output else {}
            self._trace.generation(text, (response.llm_output or {}).get("model_name"),
                                   usage.get("prompt_tokens"), usage.get("completion_tokens"))

        def on_chain_end(self, outputs, *, run_id, **kw):
            if run_id == self._root and self._trace is not None:
                if isinstance(outputs, dict) and "answer" in outputs:
                    self._trace.data["answer"] = outputs["answer"]
                elif isinstance(outputs, str):
                    self._trace.data["answer"] = outputs
                self._trace.end()
                self._trace = None

    return RagopsCallbackHandler()


def log_llamaindex(client: RagopsClient, query: str, response: Any, config_label: str | None = None) -> str:
    """Log a LlamaIndex query-engine Response (uses response.source_nodes)."""
    t = client.trace(query, config_label=config_label)
    nodes = getattr(response, "source_nodes", []) or []
    t.retrieved(nodes).context(nodes)
    t.generation(str(response))
    t.end()
    return t.id


def log_haystack(client: RagopsClient, query: str, result: dict, config_label: str | None = None) -> str:
    """Log a Haystack 2.x ``pipeline.run(...)`` result. Run the pipeline with
    ``include_outputs_from={"retriever", "ranker"}`` (your component names) so retrieved documents are included."""
    from ragops.pipelines import _hs_doc, haystack_answer, haystack_documents

    retrieved, ctx = haystack_documents(result)
    with client.trace(query, config_label=config_label, framework="haystack") as t:
        t.retrieved([_hs_doc(d) for d in retrieved])
        t.context([_hs_doc(d) for d in ctx])
        t.generation(haystack_answer(result))
    return t.id
