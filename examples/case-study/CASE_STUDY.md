# Case study: RAGOps Observatory on WikiHelp, a real RAG service

**System under observation.** WikiHelp ([`squad_rag.py`](squad_rag.py)) answers questions about 25 Wikipedia articles (the SQuAD 2.0 dev set): BM25 + embedding hybrid retrieval, an optional reranker and an extractive reader for generation. It is a separate service that RAGOps observes through the Python SDK and tests through its HTTP endpoint.

**What happened.** Ten days of traffic with three deploys: v1 (60-word chunks), v2 (a 25-word-chunk "cost cut") and v3 (60-word chunks plus a reranker). On day 6 the *Black_Death* page was edited but the serving index was only rebuilt on day 8, and users started asking about topics the knowledge base does not cover. Nothing was injected: every failure below is the app's real behaviour. SQuAD's answer labels give ground truth for whether each answer was actually right.

Reproduce: `python examples/case-study/run_case_study.py` (raw numbers in [`results.json`](results.json)).

## 1. Did RAGOps catch the bad answers?

Of **1,200** requests, **640** answers were actually wrong. RAGOps flagged **46%** of the wrong answers and **8%** of the correct ones; **88%** of flagged requests were really wrong. Only 30% of wrong answers got a thumbs-down from users, so most of these were found without any user complaint.

| Request type | Requests | Answered correctly | What RAGOps said about the flagged ones |
|---|---|---|---|
| answerable | 921 | 60% | generation failure 91, knowledge gap 35, stale knowledge 30 |
| unanswerable | 133 | 3% | generation failure 30, chunking defect 7, knowledge gap 6 |
| not in kb | 146 | 1% | knowledge gap 77, generation failure 29, stale knowledge 2 |

## 2. Which change hurt, and which stage broke

| Config | Requests | Correct (all) | Correct (answerable questions only) | Flagged by RAGOps |
|---|---|---|---|---|
| `v1-bm25-60w` | 480 | 55% | 64% | 20% |
| `v2-25w-chunks` | 360 | 38% | 50% | 33% |
| `v3-60w-rerank` | 360 | 44% | 66% | 34% |

Overall accuracy also falls after day 6 because more questions are about topics the knowledge base does not cover; the answerable-only column isolates the effect of each config.

Root causes across the 10 days:

| Stage | Failures | Share |
|---|---|---|
| generation failure | 150 | 45% |
| knowledge gap | 118 | 35% |
| stale knowledge | 34 | 10% |
| chunking defect | 23 | 7% |
| ranking failure | 7 | 2% |
| retrieval miss | 4 | 1% |

Quality change points RAGOps detected, and what it linked them to:

- answer_relevance degraded on 2026-11-08: 0.742 -> 0.644 (p=1.6e-09) (likely cause: config 'v2-25w-chunks' deployed 2026-11-07 15:12)
- context_relevance degraded on 2026-11-08: 0.836 -> 0.709 (p=5.2e-08) (likely cause: config 'v2-25w-chunks' deployed 2026-11-07 15:12)

## 3. The edit nobody re-indexed

RAGOps flagged **34** answers as *stale knowledge*: built from the old version of a page after the CMS changed it. No user complained about these and the answers looked fluent; the only signal was the version mismatch between the serving index and the corpus registry.
Corpus drift listed 131 recent answers that depended on changed documents, so they could be re-checked.

## 4. What users started asking

New uncovered topic detected (domain AUC 0.55, MMD p=0.020). Emerging topics: 'many / form / civil' (40%); 'plague / high / pharmacy' (9%); 'steam / engine / another' (12%). 19% of current questions have no matching document in your corpus.

- Coverage gap **rhine / prime / name** (118 questions): Write or ingest a document covering: rhine, prime, name, numbers

## 5. Testing the fix before shipping it

Every confidently diagnosed failure was promoted to the `production-failures` golden set (**195** cases). Registering WikiHelp's HTTP endpoint as a pipeline and running the set against v2 and v3 from the API gave:

| Run | Cases | Pass rate | Faithfulness | Answer relevance | Context relevance |
|---|---|---|---|---|---|
| `v2-25w-chunks` | 80 | 66% | 1.000 | 0.560 | 0.612 |
| `v3-60w-rerank` | 80 | 60% | 1.000 | 0.567 | 0.800 |

Paired comparison v2 → v3 on the same 80 cases: **no significant regression**. faithfulness +0.000 (95% CI +0.000..+0.000, unchanged); answer relevance +0.007 (95% CI -0.026..+0.038, unchanged); context relevance +0.189 (95% CI +0.098..+0.283, improved); context recall +0.025 (95% CI -0.025..+0.075, unchanged); abstention quality +0.000 (95% CI -0.037..+0.037, unchanged); citation correctness +0.000 (95% CI -0.021..+0.021, unchanged).

## 6. Alerts that fired

- [RAGOps] wikihelp: SLO 'Answers address the question' breached

## What the case study found in RAGOps itself

Running against a real system exposed three bugs that unit tests and the synthetic demo had missed. All three are fixed in v0.5 and covered by regression tests:

1. **Citations with punctuation were scored as hallucinations.** Document ids like `[Sky_(United_Kingdom)]` did not match the citation pattern, so the citation itself counted as an unsupported claim. It made v3 look less faithful and the release gate wrongly blocked it.
2. **Re-chunking looked like a content change.** Moving from 60- to 25-word chunks re-registered every document as a new version, so corpus drift reported hundreds of "outdated" answers. Versions now follow document content; re-chunking only replaces chunks.
3. **A perfectly constant metric crashed the drift endpoint.** Welch's t-test returns NaN when both sides have zero variance, and NaN is not valid JSON.

## Takeaways and limits

- The stage breakdown and the change timeline pointed at the responsible deploy without reading traces.
- Stale-knowledge detection caught silent errors that neither users nor quality metrics would surface.
- Coverage gaps turned unanswered questions into a concrete writing list.
- The release gate agreed with ground truth: v3 improved context relevance significantly and its answerable accuracy was higher in production, and the gate let it through.
- Recall is the weak spot: with the lexical judge RAGOps flagged under half of the wrong answers. Most misses are confident answers that quote a real sentence from the right article that just doesn't contain the answer. An LLM judge is the fix, and the calibration page shows how far to trust it.
- Limits: the default heuristic judge is lexical, so it misses some paraphrased answers and flags some correct ones (see the detection numbers above). Run the study with `--openai` and `RAGOPS_JUDGE=openai` for an LLM generator and judge. Traffic is replayed from SQuAD questions, not live users.
