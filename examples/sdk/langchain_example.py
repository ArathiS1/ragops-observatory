"""LangChain: add one callback handler. (pip install ragops-observatory langchain-core)"""

from ragops import RagopsClient
from ragops.sdk.adapters import langchain_handler

rg = RagopsClient("http://localhost:8000")
handler = langchain_handler(rg, config_label="lc-prod-v4")

# chain = create_retrieval_chain(retriever, combine_docs_chain)
# chain.invoke({"input": "How do I rotate API keys?"}, config={"callbacks": [handler]})
