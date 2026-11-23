"""RAG built with Haystack 2: an indexing pipeline (DocumentSplitter -> embedder -> InMemoryDocumentStore) and a
query pipeline (embedder -> InMemoryEmbeddingRetriever -> ChatPromptBuilder -> OpenAIChatGenerator).

    pip install haystack-ai fastembed
    ragops test -f haystack.yaml
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache

from common import ABSTAIN, PROMPT, chunk, embed, generate_with_retry, llm_settings, load_docs
from haystack import Document, Pipeline, component
from haystack.components.builders import ChatPromptBuilder
from haystack.components.generators.chat import OpenAIChatGenerator
from haystack.components.preprocessors import DocumentSplitter
from haystack.components.retrievers.in_memory import InMemoryEmbeddingRetriever
from haystack.dataclasses import ChatMessage
from haystack.document_stores.in_memory import InMemoryDocumentStore
from haystack.utils import Secret


@component
class SharedDocumentEmbedder:
    """Haystack component around the embeddings every framework example uses."""

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]):
        vectors = embed([d.content or "" for d in documents])
        return {"documents": [replace(d, embedding=v) for d, v in zip(documents, vectors)]}


@component
class SharedTextEmbedder:
    @component.output_types(embedding=list[float])
    def run(self, text: str):
        return {"embedding": embed([text])[0]}


@lru_cache(maxsize=8)
def build_store(split_length: int, split_overlap: int) -> InMemoryDocumentStore:
    store = InMemoryDocumentStore(embedding_similarity_function="cosine")
    indexing = Pipeline()
    indexing.add_component("splitter", DocumentSplitter(split_by="word", split_length=split_length,
                                                        split_overlap=split_overlap))
    indexing.add_component("embedder", SharedDocumentEmbedder())
    indexing.connect("splitter.documents", "embedder.documents")
    docs = [Document(content=t, meta={"file_name": n}) for n, t in load_docs()]
    store.write_documents(indexing.run({"splitter": {"documents": docs}})["embedder"]["documents"])
    return store


def build_query_pipeline(store: InMemoryDocumentStore, config: dict) -> Pipeline:
    s = llm_settings(config)
    template = [ChatMessage.from_user(PROMPT.replace("{context}", "{% for d in documents %}[{{ d.meta.file_name }}] "
                                                     "{{ d.content }}\n\n{% endfor %}").replace("{question}", "{{ question }}"))]
    p = Pipeline()
    p.add_component("embedder", SharedTextEmbedder())
    p.add_component("retriever", InMemoryEmbeddingRetriever(store, top_k=int(config.get("top_k", 3))))
    p.add_component("prompt", ChatPromptBuilder(template=template, required_variables=["documents", "question"]))
    p.add_component("llm", OpenAIChatGenerator(
        api_key=Secret.from_token(s["api_key"]), model=s["model"], api_base_url=s["base_url"], max_retries=6,
        generation_kwargs={"max_tokens": s["max_tokens"], "temperature": s["temperature"]}))
    p.connect("embedder.embedding", "retriever.query_embedding")
    p.connect("retriever.documents", "prompt.documents")
    p.connect("prompt.prompt", "llm.messages")
    return p


def answer(question: str, config: dict | None = None) -> dict:
    config = config or {}
    store = build_store(int(config.get("chunk_size", 120)), int(config.get("chunk_overlap", 20)))
    pipe, results = build_query_pipeline(store, config), []

    def ask() -> str:
        results.append(pipe.run({"embedder": {"text": question}, "prompt": {"question": question}},
                                include_outputs_from={"retriever"}))
        return results[-1]["llm"]["replies"][0].text

    text = generate_with_retry(ask)
    docs = results[-1]["retriever"]["documents"]
    contexts = [chunk(d.content or "", d.meta.get("file_name", "?"),
                      f"{d.meta.get('file_name', '?')}#{d.meta.get('split_id', 0)}", d.score) for d in docs]
    min_score = config.get("min_score")
    if min_score is not None and (not docs or (docs[0].score or 0) < float(min_score)):
        return {"answer": ABSTAIN, "contexts": contexts}
    return {"answer": text, "contexts": contexts}


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(answer(" ".join(sys.argv[1:]) or "How long is the password reset link valid?"), indent=2))
