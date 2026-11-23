# RAG doctor walkthrough

`acme_rag.py` is a small but complete RAG pipeline (BM25 retrieval over `docs/*.md`, extractive answers) for a
fictional "Acme Cloud" help centre. Its defaults are deliberately weak: 12-word chunks, one chunk per answer, and it
never says "I don't know". `tests.jsonl` holds 20 questions with expected answers, two of which the docs do not cover.

```bash
pip install "ragops-observatory[server]"
ragops doctor --write-config fixed.yaml      # diagnose, test fixes, write the verified config
ragops doctor --config fixed.yaml            # check what is left after the fix
```

The first run finds that most mistakes are *right document, wrong passage*, tests six changes and recommends
`chunk_size=48, abstain_when_unsure=true` (13 → 5 mistakes, nothing newly broken). The second run shows that the
remaining mistakes come from the answer generator, which picks the wrong sentence: the next fix is a better reader
or an LLM, not more retrieval tuning.

To serve it over HTTP and try **Test these fixes** in the web UI, wrap `answer(query, config)` in any small web
server that accepts `POST {"query": ..., "config": {...}}` and returns `{"answer": ..., "contexts": [...]}`, register
it on the *Test your RAG* page with `default_config: {"chunk_size": 12, "top_k": 1, "abstain_when_unsure": false}`,
run `tests.jsonl`, and open the run.

Swap in your own pipeline by editing `ragops.yaml`: `pipeline.callable` (or `pipeline.kind: http` and `url`),
`config` (the knobs your pipeline reads), `dataset` and `corpus`. Guide: [docs/doctor.md](../../docs/doctor.md).
