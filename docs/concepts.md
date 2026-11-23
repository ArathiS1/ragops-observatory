# Concepts

## Data model

`Trace` (one RAG call: query, answer, model, latency, tokens, cost, feedback, config version) has
`RetrievedChunk`s at three steps: **retrieve** (candidates), **rerank** and **context** (what the LLM saw). Every
trace is pinned to a `ConfigVersion` label so quality changes can be traced to the deploy that caused them.
The corpus registry stores `Document` versions and their `Chunk`s.

## Evaluation

| Metric | Judge | Meaning |
|---|---|---|
| faithfulness | LLM or heuristic | Share of answer claims supported by the context |
| answer_relevance | LLM or heuristic | Does the answer address the question? |
| context_relevance | LLM or heuristic | Rank-weighted precision of the context |
| context_recall | LLM or heuristic | Needs `expected_answer` or `expected_doc_ids` |
| citation_correctness | deterministic | Do citations point at documents in the context? |
| abstention_quality | deterministic | Abstained when (and only when) the corpus can't answer |
| context_consistency | deterministic | Two versions of a doc, or conflicting figures, in one context |

**Sampling.** Each trace gets a stratum (config × retrieval-score bucket × feedback). Traffic is sampled at
`sample_rate`, boosted up to 5× for strata that are under-represented today, under a hard **daily judge budget**.
Negative feedback is always evaluated. Decisions are deterministic per trace id.

**Calibration.** Human 👍/👎 labels are compared with judge pass/fail at the project threshold; Cohen's κ says whether
the judge can gate releases (> 0.8), drive alerts (0.6–0.8), show trends (0.4–0.6) or should not be trusted.

## Root-cause diagnosis

A trace **fails** when faithfulness or answer relevance is below threshold, abstention was wrong, the context
contradicts itself, or the user gave negative feedback. Stale context is a failure even without a complaint.
The classifier then walks the pipeline in order:

1. **stale_knowledge**: a relevant context chunk came from an old or deleted document version.
2. **knowledge_gap**: the best chunk in the whole corpus is below the relevance floor.
3. **chunking_defect**: no single chunk covers the question, but two adjacent chunks of one document do.
4. **retrieval_miss**: the gold chunk (best corpus match, or `expected_doc_ids`) is not among the candidates.
5. **ranking_failure**: it is among the candidates but not in the prompt.
6. **context_overload**: it is in the prompt at position ≥ 4 of ≥ 8 chunks, mostly irrelevant.
7. **generation_failure**: it is in the prompt and the answer is still wrong.

Every diagnosis stores its evidence (gold chunks, ranks, versions, coverage) and is shown on the Traces page.

## Drift

| Layer | Method | Output |
|---|---|---|
| Query | Domain-classifier AUC, RBF-MMD permutation test, k-means topics with novelty vs the reference window, corpus coverage per topic | Emerging topics and the share your docs can't answer |
| Retrieval | KS test on top-1 scores, empty-retrieval rate, top-k overlap for repeated queries | Is retrieval degrading? |
| Corpus | Document version events vs chunks used in recent answers | Answers that relied on now-changed docs |
| Quality | Best two-sample split (Welch t-test) + CUSUM on daily means | Change date, size, and the config/doc change on that day |

## Knowledge-base health

Coverage gaps (clustered knowledge-gap queries with a suggested document), hot chunks (frequent in failures), chunks
never retrieved, near-duplicates and contradictions (same topic, different figures). Rolled into a health score.

## SLOs

`metric ≥ threshold` on `target` share of evaluated requests over `window_days`. Error budget = `(1 − target) × n`
bad requests; burn rate = 24 h bad rate ÷ `(1 − target)`. Status changes alert every channel with the current
root-cause breakdown. SLOs need at least 20 samples in the window.
