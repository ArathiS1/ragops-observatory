# Test real RAG frameworks with RAGOps

The same help-center documents (`../doctor/docs`), the same 20 test questions (`../doctor/tests.jsonl`), the same
LLM and the same embeddings, built four ways, with the framework each team in industry would actually use:

| File | Framework | How it is built |
|---|---|---|
| `llamaindex_rag.py` | LlamaIndex | `SentenceSplitter` → `VectorStoreIndex` → query engine |
| `langchain_rag.py` | LangChain (LCEL) | `RecursiveCharacterTextSplitter` → `InMemoryVectorStore` → `prompt \| ChatOpenAI \| parser` |
| `langgraph_rag.py` | LangGraph | a `StateGraph`: retrieve → grade → generate, or decline when the context is weak |
| `haystack_rag.py` | Haystack 2 | indexing pipeline + query pipeline with `InMemoryEmbeddingRetriever` and `OpenAIChatGenerator` |

Because only the framework changes, the differences in the scores come from how each framework splits, retrieves
and prompts, which is the comparison a team makes before choosing one.

## Setup (once)

```powershell
cd examples\frameworks
pip install -r requirements.txt
```

- **LLM:** the same as `my_rag.py`: `RAG_LLM=sarvam` (default) with `SARVAM_API_KEY`, or `groq`, `gemini`,
  `openai`, `ollama`.
- **Embeddings:** `BAAI/bge-small-en-v1.5` via fastembed, on your CPU, free. The first run downloads about 70 MB.
- **Judge:** your `RAGOPS_JUDGE*` settings, as for `ragops test`.

Check one framework answers: `python langchain_rag.py "How long is the password reset link valid?"`

## 1. Test each framework

```powershell
ragops test -f llamaindex.yaml
ragops test -f langchain.yaml
ragops test -f langgraph.yaml
ragops test -f haystack.yaml
```

Or all four with one comparison table: `python compare.py`

## 2. Fix the weakest one

```powershell
ragops doctor -f haystack.yaml --max-trials 3
```

## 3. Gate a change

```powershell
ragops gate --pipeline langchain_rag.py:answer --baseline base.yaml --candidate cand.yaml --golden ..\doctor\tests.jsonl
```

## 4. Watch them in production

In the main folder, create one project per framework, then send traffic from here:

```powershell
ragops create-project frameworks-llamaindex   # likewise langchain, langgraph, haystack
python send_traffic.py llamaindex             # likewise for the others
```

The **Organisation** page then shows the four side by side: traffic, failure rate, main cause, SLO status.

## Your own documents

Set `RAG_DOCS` to your folder and point `dataset:` / `corpus:` in the YAML files at your test cases and folder.

## RAGFlow, Microsoft GraphRAG, NVIDIA NeMo Retriever

These run as separate services, so RAGOps tests them the same way as any deployed RAG: through a small
`answer(question, config)` function that calls the service and returns `{"answer": ..., "contexts": [...]}`,
or with `pipeline: {kind: http, url: ..., answer_path: ..., contexts_path: ...}` when the service already
returns the answer and its sources as JSON. What each one needs:

| System | What you need to run it |
|---|---|
| RAGFlow | Docker with about 16 GB RAM; its HTTP API returns the answer with references |
| Microsoft GraphRAG | `pip install graphrag`; indexing calls the LLM many times per document, so it costs far more than the others |
| NVIDIA NeMo Retriever | NVIDIA NIM microservices: an NVIDIA GPU, or an API key from build.nvidia.com; it does retrieval, an LLM writes the answer |

These three are not wired up in this folder.
