"""Instrument an existing RAG function with the RAGOps SDK.

    ragops serve --inline-jobs &          # or: docker compose up
    ragops create-project my-bot          # prints an admin key
    RAGOPS_API_KEY=rgo_... RAGOPS_PROJECT=my-bot python examples/sdk/quickstart.py
"""

from ragops import RagopsClient
from ragops.demo import pipeline as my_rag  # stand-in for your retriever + LLM

rg = RagopsClient("http://localhost:8000")  # reads RAGOPS_API_KEY / RAGOPS_PROJECT

# 1. Register the knowledge base once (and on every re-index). This is what lets the observatory tell a
#    knowledge gap from a retrieval miss, and spot answers built from outdated document versions.
from ragops.demo.corpus import DOCS  # noqa: E402

rg.upsert_corpus([{"doc_id": d["doc_id"], "title": d["title"], "text": d["text"]} for d in DOCS], chunk_words=45)

# 2. Trace each request. The SDK never raises into your app and never blocks the request path.
CONFIG = {"chunk_words": 45, "top_k": 6, "rerank": "good", "context_k": 3}
for question in ["How much does the Pro plan cost per user?", "Does the mobile app work offline on Android?"]:
    with rg.trace(question, config_label="prod-2026-11", config=CONFIG, channel="web") as t:
        out = my_rag.run(question, CONFIG)
        t.retrieved(out["retrieved"])      # candidates from the retriever
        t.context(out["contexts"])         # what the LLM actually saw
        t.generation(out["answer"], model="gpt-4o-mini", prompt_tokens=640, completion_tokens=48)
    print(question, "->", t.id)

# 3. Forward user feedback whenever it arrives; negative feedback is always evaluated.
rg.feedback(t.id, -1.0, "the bot made this up")
rg.close()
