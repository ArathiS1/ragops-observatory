# RAG doctor: is my RAG working, and how do I fix it?

`ragops doctor` answers three questions about **your** RAG system:

1. **Is it working?** It runs your test questions through your pipeline and gives a verdict: *healthy*, *needs work* or *broken*.
2. **What mistakes does it make, and why?** Every wrong answer is traced to the stage that caused it: missing knowledge, outdated documents, chunks that cut the answer in half, a retriever that misses the right document, a reranker that drops it, a prompt that buries it, or a model that answers badly with good context. Each cause comes with how to confirm it, how to fix it, and real examples from your run.
3. **Which fix actually helps?** It re-runs your test set with candidate config changes and keeps only the changes that are *verified*: fewer mistakes, more cases fixed than broken, no new crashes, and no metric that gets significantly worse (paired bootstrap, 95% interval).

## Try it in two minutes

The repository ships a small but complete RAG pipeline with deliberately weak defaults:

```bash
pip install "ragops-observatory[server]"
cd examples/doctor
ragops doctor --write-config fixed.yaml
```

Output (abridged):

```text
## 🔴 Verdict: broken
Your RAG fails 65% of questions; the main cause is right document, wrong passage.

### 1. Right document, wrong passage — 11 cases (85% of mistakes)
The retriever found the right document, but the passage holding the answer did not make it into the prompt ...
- `When are invoices issued?` — answer_correctness 0.00 < 0.5; right chunk at position 1 of 1

## Treatment trial: fixes tested on your pipeline
| Change                                  | Mistakes | Fixed | Newly broken | Verified |
| chunk_size=24                           | 13 → 9   | 5     | 1            | ✅ |
| chunk_size=48                           | 13 → 7   | 6     | 0            | ✅ |
| top_k=3                                 | 13 → 13  | 0     | 0            | ❌ no fewer mistakes |
| abstain_when_unsure=True                | 13 → 11  | 2     | 0            | ✅ |
| chunk_size=48, abstain_when_unsure=True | 13 → 5   | 8     | 0            | ✅ |

**Recommended change: `chunk_size=48, abstain_when_unsure=True`**
```

Note what it did *not* recommend: raising `top_k` looks like the obvious fix, but on this pipeline it changes nothing, so the doctor says so instead of guessing.

## Use it on your own RAG

```bash
ragops init            # writes ragops.yaml, my_rag.py and a sample rag_tests.jsonl
# 1. make my_rag.py call your retriever and LLM (or point ragops.yaml at your HTTP endpoint)
# 2. put 20+ real questions in rag_tests.jsonl, with expected_answer where you know it
ragops doctor --corpus docs/
```

What makes the diagnosis sharp:

| Give it | What it unlocks |
|---|---|
| `expected_answer` per question | answer correctness, and "right document, wrong passage" vs. "good context, wrong answer" |
| `expected_doc_ids` per question | retrieval hit rate |
| `--corpus docs/` (your documents) | missing knowledge vs. retrieval miss vs. chunking defect |
| knobs in `config:` | automatic treatment trials |

A question whose expected answer is a refusal (`"I don't know"`) tests that the bot declines questions your documents do not cover; a correct refusal counts as a pass.

## Knobs: letting the doctor test fixes

The doctor never edits your code. It passes a config dict to your pipeline (`answer(query, config)`, or the `config` field of the HTTP request) and changes values in it. It only tries knobs that already appear in your `config:` section, using these conventional names:

| Cause | Knobs tried |
|---|---|
| Right document, wrong passage | `chunk_size` ×2 / ×4, `top_k` +2 / ×3+2, `chunk_overlap` |
| Answers split across chunks | `chunk_size` ×1.5 / ×2, `chunk_overlap`, `expand_neighbors` |
| Retriever misses the right document | `top_k` ×2 / ×3, `hybrid`, `query_rewrite` |
| Right document retrieved, then dropped | `rerank_top_n` ×2, `rerank`, `score_threshold` ÷2 |
| Right chunk buried in noise | `top_k` ÷2, `rerank` |
| Good context, wrong answer | `temperature=0`, `grounded_prompt` |
| Missing knowledge | `abstain_when_unsure` |

Choose your own values instead with `--try` (repeatable) or in `ragops.yaml`:

```bash
ragops doctor --try top_k=8 --try top_k=12 --try rerank=true
```

```yaml
doctor:
  knobs:
    top_k: [8, 12]
    model: [gpt-4o-mini, gpt-4o]
```

When two or more single changes are verified, the doctor also tests them together. `--write-config fixed.yaml` writes your config with the recommended change applied. If your pipeline exposes no knobs, the report lists which ones to expose.

Exit codes make it usable in CI: `0` healthy, `1` needs work, `2` broken.

## In the web UI and API

- **RAG doctor** page: the same diagnosis for live production traffic (`GET /v1/projects/{p}/doctor?days=7`).
- **Test your RAG → a run → Diagnosis**: verdict, causes, examples and a **Test these fixes** button. For a pipeline registered as an HTTP endpoint, the server runs one trial per proposed change in the worker and shows the verified fix (`GET /v1/projects/{p}/runs/{id}/doctor`, `POST .../doctor/trial` with `{"changes": [{"top_k": 10}]}`).
- Add `?format=md` to either endpoint for a Markdown report to paste into a ticket or PR.

## How "verified" is decided

A change is verified only when, on the same questions:

- the number of mistakes goes down,
- it fixes more cases than it newly breaks,
- it causes no new pipeline errors, and
- no metric drops significantly (paired bootstrap 95% interval entirely below zero and a mean drop above 0.02).

With a small test set the interval is wide, so a real improvement may show as "no significant change" on metrics while still reducing mistakes; add questions to tighten it. The judge matters too: run `ragops judge check` to confirm your judge agrees with labelled examples before trusting the numbers.
