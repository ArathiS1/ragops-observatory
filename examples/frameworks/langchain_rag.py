"""RAG built with LangChain (LCEL): RecursiveCharacterTextSplitter -> InMemoryVectorStore -> retriever ->
prompt | ChatOpenAI | parser.

    pip install langchain-core langchain-openai langchain-text-splitters fastembed
    ragops test -f langchain.yaml
"""
from __future__ import annotations

from functools import lru_cache

from common import ABSTAIN, PROMPT, chunk, embed, generate_with_retry, llm_settings, load_docs
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter


class SharedEmbeddings(Embeddings):
    """LangChain wrapper around the embeddings every framework example uses."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return embed([text])[0]


@lru_cache(maxsize=8)
def build_store(chunk_size: int, chunk_overlap: int) -> InMemoryVectorStore:
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap, add_start_index=True)
    docs = splitter.split_documents([Document(page_content=t, metadata={"source": n}) for n, t in load_docs()])
    return InMemoryVectorStore.from_documents(docs, SharedEmbeddings())


def make_llm(config: dict):
    from langchain_openai import ChatOpenAI

    s = llm_settings(config)
    return ChatOpenAI(model=s["model"], base_url=s["base_url"], api_key=s["api_key"], max_tokens=s["max_tokens"],
                      temperature=s["temperature"], max_retries=6,
                      default_headers={"api-subscription-key": s["api_key"]} if s["provider"] == "sarvam" else None)


def retrieve(question: str, config: dict) -> list[tuple[Document, float]]:
    store = build_store(int(config.get("chunk_size", 1000)), int(config.get("chunk_overlap", 100)))
    return store.similarity_search_with_score(question, k=int(config.get("top_k", 3)))


def to_contexts(hits: list[tuple[Document, float]]) -> list[dict]:
    return [chunk(d.page_content, d.metadata["source"], f"{d.metadata['source']}@{d.metadata.get('start_index', 0)}", s)
            for d, s in hits]


def generate(question: str, hits: list[tuple[Document, float]], config: dict) -> str:
    chain = ChatPromptTemplate.from_messages([("user", PROMPT)]) | make_llm(config) | StrOutputParser()
    context = "\n\n".join(f"[{d.metadata['source']}] {d.page_content}" for d, _ in hits)
    return generate_with_retry(lambda: chain.invoke({"context": context, "question": question}))


def answer(question: str, config: dict | None = None) -> dict:
    config = config or {}
    hits = retrieve(question, config)
    min_score = config.get("min_score")
    if min_score is not None and (not hits or hits[0][1] < float(min_score)):
        return {"answer": ABSTAIN, "contexts": to_contexts(hits)}
    return {"answer": generate(question, hits, config), "contexts": to_contexts(hits)}


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(answer(" ".join(sys.argv[1:]) or "How long is the password reset link valid?"), indent=2))
