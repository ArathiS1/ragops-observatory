# Root-cause attribution on SQuAD 2.0 (public benchmark)

Dataset: **SQuAD 2.0 dev (Rajpurkar et al., 2018; CC BY-SA 4.0)**. Corpus: 30 Wikipedia articles split into 2,728 chunks of ~60 words; 5 more articles held out to create real knowledge gaps. Retriever: hybrid hashing-embedding + lexical. Judge: deterministic heuristic judge (no LLM calls), seed 0. Reproduce with `ragops benchmark --dataset squad`.

- **Cases:** 2,000 (1,700 injected failures from 300 sampled questions + healthy controls)
- **Attribution accuracy:** 73.8%
- **Macro recall across the 7 stages:** 69.8%
- **False-alarm rate on healthy calls:** 22.3%

| Injected failure | Cases | Recall | Precision |
|---|---|---|---|
| knowledge_gap | 100 | 64% | 53% |
| stale_knowledge | 300 | 100% | 100% |
| chunking_defect | 100 | 42% | 22% |
| retrieval_miss | 300 | 80% | 70% |
| ranking_failure | 300 | 66% | 81% |
| context_overload | 300 | 65% | 100% |
| generation_failure | 300 | 72% | 57% |
| ok | 300 | 78% | 100% |

## Confusion matrix (rows = injected, columns = predicted)

| injected \ predicted | knowledge gap | stale knowledge | chunking defect | retrieval miss | ranking failure | context overload | generation failure | ok |
|---|---|---|---|---|---|---|---|---|
| **knowledge_gap** | 64 | · | 2 | · | · | · | 34 | · |
| **stale_knowledge** | · | 300 | · | · | · | · | · | · |
| **chunking_defect** | 7 | · | 42 | 4 | · | · | 47 | · |
| **retrieval_miss** | 10 | · | 26 | 241 | · | · | 23 | · |
| **ranking_failure** | 10 | · | 26 | 44 | 197 | · | 23 | · |
| **context_overload** | 10 | · | 41 | 54 | · | 195 | · | · |
| **generation_failure** | 10 | · | 41 | · | 34 | · | 215 | · |
| **ok** | 10 | · | 10 | · | 13 | · | 34 | 233 |

## Reading the results

**False alarms come from failure detection, not attribution.** The classifier only runs after the judge flags a failure, and the deterministic heuristic judge scores answer relevance by word overlap. SQuAD questions paraphrase their source ("In what country is Normandy?" vs "a region in France"), so correct answers sometimes look irrelevant to it. Use an LLM judge (`RAGOPS_JUDGE=openai`) in production and check it on the Judge calibration page.

**What this benchmark changed.** Running on real text exposed two rules that were too narrow or too eager, and v0.5 fixed both: (1) chunking defects are now recognised by their real signature, a chunk boundary that cuts through the best-matching sentence (chunking-defect recall 15% → 42%); (2) an outdated chunk only makes an answer *stale* when the answer cites or uses it, otherwise it is a warning. Overall attribution went from 71.4% to 73.8% here; the synthetic benchmark moved from 90.3% to 89.6%.

Limitations: failures are injected one at a time; SQuAD answers are short spans, unlike long-form RAG answers; the unanswerable SQuAD 2.0 questions are not used because they are written against a paragraph that *is* in the corpus (they test reading comprehension, not retrieval).
