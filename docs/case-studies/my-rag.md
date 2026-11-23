# Case study: from "Not ready" to "Ready" in one afternoon

**System:** a help-centre RAG assistant (BM25 retrieval over Markdown documents, answers by Sarvam 105B, an Indian
reasoning model with an OpenAI-compatible API). **Judge:** the same model through RAGOps' LLM judge.
**Date:** 20–23 November 2026, on a Windows laptop. Every number below comes from those runs.

## The question

"Is this assistant ready to put in front of customers, and if not, what exactly is wrong?"

## 1. Generate a test set from the documents

```bash
ragops generate examples/doctor/my-rag-test/docs --out my-gen.jsonl --n 20
```

RAGOps wrote 23 questions with expected answers and source documents, including 3 marked as questions the documents
do not cover (the assistant should decline those). Generated questions are a first draft, and this case study shows
why a person should read them.

## 2. First verdicts: mostly the measurement was wrong

| Run | Verdict | Answers correct | Finds the right document |
|---|---|---|---|
| Test set accidentally generated from the wrong folder | Not ready | 14.3% of 21 | **0%** |
| Right folder | Not ready | 69.6% of 23 | 100% |

The first result was alarming until the diagnosis showed **0% of expected documents found**: the questions came
from RAGOps' own manual, not the assistant's documents. With the right folder, retrieval was perfect but 7 questions
still failed. Reading them (RAGOps lists each failing question with its stage and reason) showed that 5 were
correct answers marked wrong by two scoring rules in RAGOps itself:

| Problem found by reading failures | Fix |
|---|---|
| Correct answers to *reworded* questions failed on answer relevance ("initial email reply timeframe" vs "first response") | A correct answer that matches the reference is relevant by definition (0.7.2) |
| "Should it answer or decline?" was decided by word overlap, which misread reworded questions | Use the test case's own label; word overlap only for unlabelled traffic (0.7.3) |

## 3. Real problems in the assistant, and in the test set

| Issue | Where | Fix |
|---|---|---|
| A blank reply: the model spent its token budget thinking and wrote nothing | Assistant (generation) | Room to think (`max_tokens` 8192), one retry, then a safe "I don't know" |
| A one-word answer ("RAGOps Observatory") to a "which component…" question | Assistant (generation) | Prompt: answer in one or two complete sentences with the key facts; answer only from the context |
| "When will the iOS app be released?" marked unanswerable, but the documents say it is planned with no date, and the assistant said exactly that | Test set | Question removed |
| "2FA, SSO *or* customer-managed keys?" marked unanswerable, but the documents cover 2FA and SSO | Test set | Question removed |

After these changes the assistant answered every remaining question correctly.

## 4. Release decisions

| Baseline → candidate | Guardian | Why |
|---|---|---|
| v2 (top_k 5) → v1 (top_k 3) | **Block** | Pass rate fell 95% → 90% on 21 questions (one question = 4.8 points) |
| v1 → v3 (clean test set, fixed prompt) | **Pass** | 90% → 100%, nothing newly failing, p95 latency 6.0 s → 3.8 s |

A bigger `top_k` was not better: it made one question fail on one run and raised p95 latency. The benchmark lab,
scored on the 20 questions every run shared, picked v3.

## 5. Final assurance report

| Check | Result |
|---|---|
| Answers correct and grounded | 100% of 20 questions |
| No made-up content | mean faithfulness 1.00 |
| Declines what the documents do not cover | 1 of 1 |
| No crashes or empty answers | 0 errors |
| Finds the right document | 100% hit rate |
| Trustworthy judge | LLM judge, 0 fallbacks |
| **Verdict** | **Ready** (SHA-256 fingerprint, verifiable with `ragops assure --verify`) |

In production-style traffic over the same days (279 questions), 95.3% were answered correctly; the remaining failures
were mostly **knowledge gaps** (questions about student discounts and office locations that no document covers),
which the Drift page listed as new, uncovered topics.

## What this shows, and what it does not

- **Measure the measurement.** The first verdicts were wrong because of a wrongly generated test set and two scoring
  rules. Reading the failing cases, which RAGOps lists with the stage and reason, found all three.
- **Every failure had a named cause and a concrete fix**, and the release guardian confirmed the fix with numbers.
- **Limits:** 20 questions, 7 documents and one model. LLM answers vary between runs (the same version scored 100%
  once and 90% another time), so run each version more than once and use larger test sets before a real launch.
