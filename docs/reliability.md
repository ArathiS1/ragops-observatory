# Reliability analytics (0.8)

Five additions that turn scores into decisions: what a request cost, how well the search ranked, which sentence of
an answer has no evidence, which failures are the same problem, and what to check when an alert fires.

## Environments

Send `environment` with each trace (or set it once on the client) to keep test traffic out of production numbers:

```python
client = RagopsClient("http://localhost:8000", api_key="rgo_...", project="support-bot", environment="staging")
# or: export RAGOPS_ENVIRONMENT=staging
```

Traces without one are `production`. Filter with `GET /v1/projects/{p}/traces?environment=staging`; see the counts
with `GET /v1/projects/{p}/environments`. The Cost page has an environment filter.

## Cost

Send `cost_usd` if your app knows it. Otherwise send `model`, `prompt_tokens` and `completion_tokens`, and RAGOps
estimates the cost from a **versioned price table**; the trace records `cost_estimated` and `pricing_version`, so
old estimates stay reproducible after prices change. Models whose price is unknown are never guessed.

Add your own models and contract prices (USD per 1M tokens, input and output) as a project setting:

```bash
curl -X PATCH localhost:8000/v1/projects/my-rag/settings -H "Authorization: Bearer $KEY" \
  -H "content-type: application/json" \
  -d '{"pricing": {"version": "2026-11-contract", "models": {"sarvam-105b": [0.40, 1.20]}}}'
```

or instance-wide with `RAGOPS_PRICING_FILE=prices.json` (same shape). `GET /v1/projects/{p}/costs` returns serving
cost (your models) and evaluation cost (the judge) by model, config version, environment and day; the **Cost** page
shows it.

## Ranking metrics

When a trace or test case lists `expected_doc_ids`, RAGOps scores the ranked list: **Precision@K, Recall@K, MRR,
nDCG@K** (several chunks of one document count once, at their best rank). Without labels they are not computed, so
no precision is ever made up. They appear as trace scores (judge `rules`) and in `summary.ranking` of test runs.

## Claim-level evidence

Each answer is split into claims, and each claim is linked to the passage that says it:

| Verdict | Meaning |
| --- | --- |
| Supported | a retrieved passage states it (numbers and negations must match) |
| Contradicted | a passage about the same thing gives a different number or the opposite |
| Not found | no retrieved passage states it: a lead to check, not proof of a hallucination |

The trace page shows the table; `diagnosis.evidence.claims` and `claim_summary` carry it in the API, and test runs
add `summary.claims`. These rules are explainable and free; the LLM judge's faithfulness score is kept separately.

## Failure patterns

`GET /v1/projects/{p}/failure-patterns` (and the **Failure patterns** page) groups failed requests by stage and by
question similarity, names each group by its common terms, and lists the documents involved and the fix. The page
also shows the current incident context.

## Incidents

SLO alerts now carry an incident: metrics that moved (window vs. the previous week), latency changes, the dominant
failure stage, recent configuration or document changes, affected trace ids and the checks to run first. The same is
available any time at `GET /v1/projects/{p}/incident`.

## Release gate budgets and JUnit

```bash
ragops gate --pipeline my_rag:answer --baseline base.yaml --candidate cand.yaml --golden golden.jsonl \
  --max-p95-ms 3000 --max-latency-increase 0.3 --max-error-rate 0.01 --junit gate.xml
```

Budgets block a release that answers as well but much more slowly or crashes more. `--junit` writes one test per
metric and budget for GitHub Actions, GitLab, Jenkins or Azure DevOps.

## Security probes

`ragops generate docs/ --security` adds six prompt-injection and data-leak probes. A reply containing the canary code,
an email address, a phone number or a key-like string is a `security_leak` failure, and the assurance report gains a
blocking check, "Resists prompt injection and data leaks".

## Data export and deletion

```bash
GET    /v1/projects/{p}/export                      # everything stored for the project (admin, audited)
DELETE /v1/projects/{p}/data?session_id=user-42     # one user's traces (right to erasure)
DELETE /v1/projects/{p}/data?before=2026-01-01      # older than a date
DELETE /v1/projects/{p}/data?confirm=true           # every trace
```

## Assurance in the dashboard

The **Assurance** page builds the assurance report for any finished test run (server runs or `ragops test --push`):
verdict, checks, what to fix, documents to write, regulatory evidence map, limits, a printable report and the JSON
evidence. API: `GET /v1/projects/{p}/runs/{run_id}/assurance?format=json|html`.
