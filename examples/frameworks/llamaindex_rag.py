"""RAG built with LlamaIndex: SentenceSplitter -> VectorStoreIndex -> retriever -> query engine.

    pip install llama-index-core llama-index-llms-openai-like fastembed
    ragops test -f llamaindex.yaml
"""
from __future__ import annotations

from functools import lru_cache

from common import ABSTAIN, PROMPT, chunk, embed, generate_with_retry, llm_settings, load_docs
from llama_index.core import Document, PromptTemplate, VectorStoreIndex
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.node_parser import SentenceSplitter


class SharedEmbedding(BaseEmbedding):
    """LlamaIndex wrapper around the embeddings every framework example uses."""

    def _get_query_embedding(self, query: str) -> list[float]:
        return embed([query])[0]

    def _get_text_embedding(self, text: str) -> list[float]:
        return embed([text])[0]

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return embed(texts)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)


@lru_cache(maxsize=8)
def build_index(chunk_size: int, chunk_overlap: int) -> VectorStoreIndex:
    docs = [Document(text=text, metadata={"file_name": name}, id_=name) for name, text in load_docs()]
    splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return VectorStoreIndex.from_documents(docs, transformations=[splitter], embed_model=SharedEmbedding())


def make_llm(config: dict):
    from llama_index.llms.openai_like import OpenAILike

    s = llm_settings(config)
    return OpenAILike(model=s["model"], api_base=s["base_url"], api_key=s["api_key"], is_chat_model=True,
                      context_window=32000, max_tokens=s["max_tokens"], temperature=s["temperature"], max_retries=6,
                      default_headers={"api-subscription-key": s["api_key"]} if s["provider"] == "sarvam" else None)


def answer(question: str, config: dict | None = None) -> dict:
    config = config or {}
    index = build_index(int(config.get("chunk_size", 256)), int(config.get("chunk_overlap", 20)))
    engine = index.as_query_engine(
        llm=make_llm(config), similarity_top_k=int(config.get("top_k", 3)),
        text_qa_template=PromptTemplate(PROMPT.replace("{context}", "{context_str}").replace("{question}", "{query_str}")))
    responses = []

    def ask() -> str:
        responses.append(engine.query(question))
        return str(responses[-1])

    text = generate_with_retry(ask)
    nodes = responses[-1].source_nodes
    contexts = [chunk(n.node.get_content(), n.node.metadata.get("file_name", "?"), f"{n.node.metadata.get('file_name', '?')}@{n.node.start_char_idx}", n.score) for n in nodes]
    min_score = config.get("min_score")
    if min_score is not None and (not nodes or (nodes[0].score or 0) < float(min_score)):
        return {"answer": ABSTAIN, "contexts": contexts}
    return {"answer": text, "contexts": contexts}


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(answer(" ".join(sys.argv[1:]) or "How long is the password reset link valid?"), indent=2))
