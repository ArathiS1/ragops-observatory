# Field report: testing real RAG systems with RAGOps

One day (19 November 2026) of using RAGOps on real pipelines: my own RAG, and the same RAG rebuilt with LlamaIndex,
LangChain, LangGraph and Haystack. Everything ran on a Windows laptop. Sarvam 105B wrote the answers and was the
LLM judge; judging 40 production requests cost about $0.03. All numbers below come from those runs.

The short version: the RAG systems were mostly fine. **The biggest problems I found were in the measurement**: the
LLM judge was wrong in specific, repeatable ways. Fixing the judge changed the framework pass rates from 45–55%
to 95–100%. The real failures that remained were caught and named correctly.


## 1. Setup

| Part | Choice |
|---|---|
| Documents | A small help center: 5–7 Markdown files (billing, security, limits, data, support, mobile) |
| Tests | 19–20 questions with expected answers, including 2 the documents do not cover |
| RAG answers | Sarvam 105B (an Indian reasoning model, OpenAI-compatible API, paid with UPI) |
| Judge | The same Sarvam 105B, through RAGOps' LLM judge |
| Embeddings (frameworks) | `BAAI/bge-small-en-v1.5`, local CPU, via fastembed |

## 2. The judge was the first thing that broke

Each row is a judge failure I found by reading traces where a correct answer was marked as failing.

| What the judge did | Example | Fix (version) |
|---|---|---|
| Faithfulness compared the answer with the **reference**, not the context | "Every 6 hours." scored unfaithful because the reference also said "kept for 35 days" | The reference goes only to correctness and recall (0.6.10) |
| Scores contradicted the judge's own explanation | Reason: "directly supported by the context"; score: 0.00 | Per-statement verdicts counted by RAGOps; reason before score (0.6.11) |
| The judge split the **context** into claims instead of the answer | "30 days" scored 0 because the answer "left out" other facts | RAGOps numbers the answer's statements itself (0.6.12) |
| The release gate silently used word matching instead of the LLM judge | Same 19 cases: 1 failure in `ragops test`, 7 in `ragops gate` | Gate uses the configured judge and prints it (0.6.14) |
| "I don’t know" with a typographic apostrophe was not seen as declining | Correct refusals (Tokyo office) failed correctness, relevance and faithfulness | Abstention detection accepts ’ (0.6.16) |
| Answers copied from the context scored 0 for faithfulness | "The password reset link is valid for 15 minutes." scored 0.00 | Near-verbatim statements are supported without a model call, with numbers and negations checked (0.6.16) |
| Correct answers with extra true detail scored 0 for correctness | Invoices answer contained the reference word for word, plus more | An answer containing the whole reference is correct (0.6.17) |

**Lesson.** Every RAG evaluation tool depends on an LLM judge. Without checking the judge, I would have concluded
that all four frameworks fail half the time. `ragops judge check` and the Judge calibration page exist for this.

## 3. Four frameworks, same documents, same model

`examples/frameworks/compare.py`, 20 questions each:

| Framework | Pass rate, before judge fixes | Pass rate, after (0.6.17) | Answer correctness | Faithfulness |
|---|---|---|---|---|
| LlamaIndex | 55% | **100%** | 0.975 | 1.000 |
| LangChain | 55% | **95%** | 0.974 | 1.000 |
| LangGraph | 55% | **100%** | 0.963 | 1.000 |
| Haystack | 45% | **100%** | 0.930 | 1.000 |

**Honest reading.** On 5 documents and 20 questions, the frameworks perform about the same; the small differences
are not statistically meaningful. Real differences need a larger, messier corpus.

**A real failure RAGOps caught.** LangChain returned **empty answers** on some runs: Sarvam 105B spent its whole
token budget reasoning and never wrote an answer, intermittently, on questions that other runs answered fine.
RAGOps labelled these `pipeline error` with the fix ("raise max_tokens"). Raising the budget to 8192 tokens
reduced them; a retry on an empty answer (0.6.18) is the production fix.

## 4. Production monitoring: an SLO incident and its recovery

My RAG in the dashboard (project `my-rag-v4`, 40 requests), with two SLOs: *Grounded answers* (faithfulness ≥ 0.7
on 90% of requests) and *Relevant answers* (relevance ≥ 0.5 on 85%).

| Time | Event |
|---|---|
| 11:47 | **Grounded answers: at risk.** 92.5% vs 90% target, 25% error budget left, burn rate 0.8×. Root cause named: generation failure, 100% of failures |
| 11:50–12:10 | Investigated the 3 failing traces: all three answers were correct; the judge was wrong (section 2) |
| 12:15 | Fixed the judge, re-scored only the failed traces with `ragops rejudge` |
| 12:20 | **Grounded answers: recovered.** Both SLOs at 100%, full error budget |

> Screenshot: SLOs and alerts page with the "at risk" and "recovered" alerts.

## 5. Release gate: blocks real regressions, not noise

`ragops gate` replays the test set through the current config and a candidate, and blocks only when a drop is
statistically real (paired bootstrap, 95% interval below zero).

| Candidate change | Test cases | Result | Why |
|---|---|---|---|
| 8-word chunks, 1 chunk retrieved | 7 | ✅ Passed, 3 newly failing listed | Drops were real but not significant on 7 cases |
| 8-word chunks, 1 chunk retrieved | 19 | ✅ Passed, 2 newly failing listed | ~10% drop, every interval crosses zero |
| 3-word chunks, 1 chunk retrieved | 19 | ❌ **Blocked** | 18 of 19 broke; answer correctness 0.93 → 0.11; 14 named *incomplete context* |

A threshold-based CI check would have blocked the first two on noise or passed the third by luck; the gate
reported the small regressions without blocking, and blocked the real one with the stage that broke.

## 6. Register your documents

The four framework projects first showed 5–25% failures, mostly "retrieval miss". The cause: their documents had
not been registered (`ragops corpus push`), so RAGOps could not tell that "I don't know" was the right answer to
questions the documents do not cover. After registering the documents and re-judging, all four were at 0%, and
the uncovered questions moved to the Knowledge base page as documents to write. Since 0.6.18, `send_traffic.py`
registers the documents itself.

## 7. Limitations

- Small test set (20 questions, 5–7 documents): good for finding bugs, too small to rank frameworks.
- The same model wrote and judged the answers; an independent judge would be stricter.
- On the public SQuAD benchmark, RAGOps' stage diagnosis has a 22% false-alarm rate (`benchmark/`).
- Some judge mistakes remain possible on paraphrased answers; Judge calibration measures how often.

## Reproduce

```powershell
cd examples\frameworks
pip install -r requirements.txt
python compare.py
```
