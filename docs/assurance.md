# RAG assurance: generate tests, certify, guard

Three commands take a RAG assistant from "we think it works" to evidence you can hand to a client or an auditor.

## 1. Generate a test set from your documents

```bash
ragops generate docs/ --n 30 --out tests.jsonl
```

- Uses your LLM judge settings (`RAGOPS_JUDGE`, `RAGOPS_JUDGE_BASE_URL`, `RAGOPS_JUDGE_MODEL`, `RAGOPS_JUDGE_API_KEY`)
  to write realistic questions and short answers per passage.
- Adds about 15% **unanswerable** questions (`--unanswerable 0.15`), with expected answer "I don't know", to check
  the assistant declines instead of inventing.
- Every case is **verified** before it is kept: the expected answer must be supported by its source passage (same
  numbers), the question must not give the answer away, duplicates are dropped, and an "unanswerable" question that
  the documents do answer is rejected. The summary line lists what was rejected and why.
- `--offline` needs no LLM: template questions, one per fact-bearing sentence. Good for CI smoke tests; use an LLM
  for real assessments.

Review a sample of the generated cases before relying on them.

## 2. Assurance report

```bash
ragops assure --dataset tests.jsonl --title "Acme help bot" --brand "Your Agency"
# or, from a run you already have:
ragops test --json-out run.json && ragops assure --from-json run.json
```

Writes `assurance.html` (open it, print it to PDF) and `assurance.json` (the evidence). The report contains:

| Section | What it says |
| --- | --- |
| Verdict | **Ready**, **Ready with conditions** or **Not ready**, from the criteria below |
| Criteria | Pass rate, faithfulness, declining uncovered questions, pipeline errors, document hit rate, sample size, judge type; each marked Pass, Condition or Blocking with its number |
| What fails | Each failing stage in plain language, the fix, and example questions with the answer given and expected |
| Documents to write | Questions no document answers |
| Outdated documents | Documents behind stale answers |
| Trust in the scores | Judge used, share of scores settled by deterministic rules, judge fallbacks |
| Evidence mapping | How the evidence can support RBI FREE-AI, EU AI Act Articles 15 and 72, ISO/IEC 42001 clause 9; DPDP is listed as not covered by a quality test |
| Integrity | SHA-256 fingerprint of inputs and results |

Default thresholds: Ready needs a pass rate of at least 95%, mean faithfulness of at least 0.90, every unanswerable
question declined, no pipeline errors, at least 20 questions and an LLM judge without fallbacks. A pass rate below
80%, faithfulness below 0.80, under 80% of unanswerable questions declined, or more than 5% errors is blocking.
Change them in `ragops.yaml`:

```yaml
assurance_thresholds:
  pass_ready: 0.95
  pass_min: 0.80
  faith_ready: 0.90
  faith_min: 0.80
  refuse_min: 0.80
  hit_ready: 0.90
  min_cases: 20
```

Check that a report was not edited after it was issued:

```bash
ragops assure --verify assurance.json
```

**Wording.** The report is an assessment of the tested questions and supporting evidence for requirements. It is
not a certification of compliance or a legal opinion; have a lawyer review how you describe it to regulated clients.

## 3. Grounding guardrail

Check an answer against the passages it came from before a user sees it.

```python
from ragops import RagopsClient

client = RagopsClient("http://localhost:8000", api_key="...", project="support-bot")
verdict = client.guard(answer, retrieved_docs, question=question, policy="rewrite")
show_to_user(verdict["answer"])  # the original answer, or a safe "I don't know" when it is not supported
```

Or over HTTP: `POST /v1/projects/{project}/guard` with `{"answer", "contexts", "question", "policy",
"min_supported", "use_llm"}`.

- Each statement of the answer is checked: word-for-word support in a context sentence first (same numbers and
  negations, no model call, a few milliseconds), then the LLM judge if `use_llm` is true, else a stricter lexical rule.
- `policy`: `rewrite` (replace with a fallback), `block` (return no answer), or `flag` (keep the answer, report it).
- `min_supported` (default 1.0): share of statements that must be supported.
- "I don't know" always passes. The response lists every statement with `supported` and how it was checked.
- If the RAGOps server is unreachable the SDK allows the answer by default (`fail_open=True`); set
  `fail_open=False` to show the fallback instead.

The rules-only check is conservative: a correct paraphrase may be rewritten. Turn on `use_llm` for paraphrasing
assistants, and measure wrongly blocked answers on your own traffic before enforcing `block`.
