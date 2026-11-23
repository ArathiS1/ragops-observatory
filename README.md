<div align="center">

# RAGOps Observatory

**Prove your RAG works.**<br>
Find which stage of your RAG pipeline fails and why, block worse releases before they ship,
and hand clients a sealed **Ready / Not ready** report.

[![CI](https://github.com/ArathiS1/ragops-observatory/actions/workflows/ci.yml/badge.svg)](https://github.com/ArathiS1/ragops-observatory/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ragops-observatory?label=pypi)](https://pypi.org/project/ragops-observatory/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-website-7b96ff)](https://arathis1.github.io/ragops-observatory/)

[Website and docs](https://arathis1.github.io/ragops-observatory/) ·
[Quickstart](#quickstart) ·
[Case study](docs/case-studies/my-rag.md) ·
[Field report](docs/field-report.md) ·
[Dashboard tour](docs/dashboard.md)

![RAGOps dashboard: overview, evidence graph, failure intelligence, benchmark lab, release guardian and assurance](docs/images/demo.gif)

</div>

---

## Why

RAG assistants look fine in a demo and fail quietly in production: the right document is never retrieved, an old
version of a policy is quoted, the model ignores a good passage, or a "small" config change makes answers worse.
Tracing tools show *what happened*; evaluation libraries give *a score*. Teams still have to answer:

- **Which part broke**, and what do I change?
- **Is the new version safe to release?**
- **Can I prove to a client or reviewer that this assistant is ready?**

RAGOps Observatory answers those three, self-hosted, with any RAG stack.

## What you get

| | Feature | What it does |
|---|---|---|
| 🔍 | **Stage diagnosis** | Assigns every failure to one of 7 pipeline stages (knowledge gap, stale document, chunking, retrieval miss, ranking, context overload, generation) with the evidence |
| 🕸️ | **Evidence graph** | Documents → passages → each claim in the answer: supported, contradicted, or with no source |
| 🧠 | **Failure intelligence** | Groups recurring failures, shows whether they are new, growing or shrinking, and the likely cause |
| 🛡️ | **Release guardian** | Compares a candidate version with production on the same questions: **Pass / Review / Block** against your gates, with approvals and overrides in the audit log |
| 🧪 | **Benchmark lab** | Models, retrievers, rerankers and chunk sizes side by side on the questions they share; picks the best |
| ✅ | **Assurance report** | Generates tests from your documents (including questions it must refuse and prompt-injection probes) and issues a fingerprinted Ready / Not ready report, mapped to RBI FREE-AI, the EU AI Act and ISO/IEC 42001 |
| 🩺 | **RAG doctor** | Re-runs your pipeline with candidate fixes and recommends only the one that verifiably helps |
| 📈 | **Monitoring** | SLOs with error budgets, drift (questions, retrieval, documents, quality), cost per request, alerts to Slack, Teams, PagerDuty, e-mail |
| 🔌 | **Works with your stack** | Python and TypeScript SDKs, OpenTelemetry, LangChain, LlamaIndex, Haystack, any HTTP endpoint; any OpenAI-compatible judge or Ragas |
| 🏢 | **Ready for teams** | Organisations, roles, SSO (OIDC), PII redaction, retention, audit log, data export and deletion, PostgreSQL, Redis, Docker, Helm |

## Screenshots

| Overview | Evidence graph |
|---|---|
| ![Overview](docs/images/overview.png) | ![Evidence graph](docs/images/evidence-graph.png) |
| **Release guardian** | **Benchmark lab** |
| ![Release guardian](docs/images/release-guardian.png) | ![Benchmark lab](docs/images/benchmark-lab.png) |
| **Failure intelligence** | **Assurance** |
| ![Failure intelligence](docs/images/failure-intelligence.png) | ![Assurance](docs/images/assurance.png) |

Light theme, cost, drift, SLOs, integrations and the RAG doctor: [dashboard tour](docs/dashboard.md).

## Quickstart

**60 seconds with demo data:**

```bash
pip install "ragops-observatory[server,dashboard]"
ragops demo            # 14 days of realistic traffic with real incidents (free rule-based judge)
ragops serve           # then open http://localhost:8000/ui and sign in with: ragops-admin-dev-key
```

**With Docker** (PostgreSQL, worker and demo included):

```bash
git clone https://github.com/ArathiS1/ragops-observatory && cd ragops-observatory
docker compose --profile demo up
```

**From source:** `pip install -e ".[server,dashboard]"`, then build the dashboard once with
`cd web && npm install && npm run build`.

## Test your own RAG in 3 commands

```bash
ragops init                                   # ragops.yaml + my_rag.py + sample tests; point my_rag.py at your RAG
ragops generate docs/ --out tests.jsonl --security    # questions from YOUR documents, incl. ones to refuse and attacks
ragops assure --dataset tests.jsonl           # Ready / Not ready, root causes, documents to fix, sealed report
```

Your RAG can be a Python function or any HTTP endpoint in any language. Use any OpenAI-compatible model as the judge
(`RAGOPS_JUDGE=openai`, plus base URL, model and key) and check it first with `ragops judge check`.
Guide: [Test your RAG](docs/test-your-rag.md).

## Proof it works

| | Setup | Result |
|---|---|---|
| [Case study](docs/case-studies/my-rag.md) | A real help-centre assistant (Sarvam 105B) | **Not ready → Ready**: 2 assistant bugs and 2 wrong test questions found and fixed; the guardian blocked a worse config and passed the fix (90% → 100%, p95 6.0 s → 3.8 s) |
| [Field report](docs/field-report.md) | The same RAG in LlamaIndex, LangChain, LangGraph and Haystack | 7 LLM-judge failure modes found and fixed: measured pass rates went from 45–55% (judge noise) to 95–100% (real) |
| [Synthetic benchmark](benchmark/RESULTS.md) | 365 cases, one injected failure each | **89.6%** correct stage, **1.8%** false alarms |
| [SQuAD 2.0 benchmark](benchmark/SQUAD_RESULTS.md) | 2,000 cases on 30 Wikipedia articles | **73.8%** correct stage |
| [Long-run case study](examples/case-study/CASE_STUDY.md) | A RAG service over 10 days, nothing injected | **88%** of flagged answers really wrong; **34** silent stale answers caught |

The benchmarks use the free rule-based judge and are honest about its limits (it misjudges paraphrases); use an LLM
judge in production and check it on the Judge calibration page.

## How it compares

RAGOps is not a replacement for tracing platforms such as Langfuse, Arize Phoenix or LangSmith, or evaluation
libraries such as Ragas and DeepEval; it can ingest their traces and use Ragas as its judge. Its focus is different:

| Question | Tracing platforms | Evaluation libraries | RAGOps |
|---|---|---|---|
| What happened in this request? | ✅ | – | ✅ |
| How good are the answers? | some | ✅ | ✅ |
| **Which stage failed, and what to change?** | – | – | ✅ |
| **Is the new version safe to release?** (significance-tested gate + approvals) | – | – | ✅ |
| **Can I prove it to a client?** (sealed Ready / Not ready report) | – | – | ✅ |

Features change quickly; check each tool's current documentation for your use case.

## Monitor production

```python
from ragops import RagopsClient

rg = RagopsClient("http://localhost:8000", api_key="rgo_...", project="support-bot")
rg.upsert_corpus(documents)            # once per (re)index: enables gap / stale / chunking diagnosis

with rg.trace(question, config_label="chunk512-bge-v3") as t:
    with t.retrieve() as s: s.output(retriever.search(question))
    with t.build_context() as s: s.output(top_chunks)
    with t.generate(model="gpt-4o-mini") as s: s.output(llm(question, top_chunks))
```

```ts
import { RagopsClient } from "@ragops/sdk";            // sdk-ts/: Node, Bun, Deno, edge, browser
const rg = new RagopsClient({ project: "support-bot" });
await rg.trace(q, { configLabel: "v3" }, async (t) => { t.context(docs); t.generation(answer); });
```

Both SDKs batch in the background, retry, never raise into your app and never block the request. Already on
OpenTelemetry/OpenInference? Set three environment variables instead: [examples/otel](examples/otel/README.md).

## Gate releases in CI

```yaml
- uses: ArathiS1/ragops-observatory@v0.10.0
  with:
    pipeline: my_app.rag:pipeline          # fn(query, config) -> {"answer", "contexts", "retrieved"}
    baseline: configs/prod.yaml
    candidate: configs/candidate.yaml
    server: ${{ secrets.RAGOPS_URL }}
    api-key: ${{ secrets.RAGOPS_API_KEY }}
    project: support-bot
```

A metric blocks the release only when it drops more than your tolerance **and** the 95% paired-bootstrap interval is
below zero, so one noisy case can't block a PR and a real regression can't hide.

---

## Architecture

```mermaid
flowchart LR
  subgraph Your systems
    SDK[Python / TypeScript SDK] --> API
    OTEL[OpenTelemetry / OpenInference] --> API
    IMP[Langfuse / Phoenix exports] --> API
    RAG[Your RAG endpoint]
  end
  IDP[OIDC identity provider] -.->|SSO| API
  API[FastAPI /v1 + /otlp + /ui] --> PG[(PostgreSQL + pgvector)]
  API --> Q[(job queue: Postgres outbox + Redis/Arq dispatch)]
  Q --> W[Worker: sampling + budget, evaluation, diagnosis, test runs, SLOs, retention]
  W -->|test runs| RAG
  W --> PG
  W -.->|optional mirror| CH[(ClickHouse)]
  PG --> UI[React UI]
  CH -.-> UI
  W --> AL[Slack, Teams, webhook, PagerDuty, e-mail]
  PG --> CI[ragops gate / GitHub Action]
```

| Component | Tech |
|---|---|
| API | FastAPI, Pydantic v2, OpenAPI at `/docs`, versioned `/v1`; OTLP/HTTP protobuf + JSON (+gzip) |
| Storage | PostgreSQL with **pgvector** (HNSW cosine indexes), **Alembic** migrations, JSON fallback, SQLite for local use |
| Tenancy & auth | Organisations → projects; scoped API keys with 4 roles; **SSO via OIDC** (PKCE, JWKS, group → role mapping) |
| High-volume analytics | Optional **ClickHouse** mirror (ReplacingMergeTree, async inserts) |
| Worker | Durable Postgres job queue (atomic claiming, retries, crash recovery) with optional **Arq + Redis** instant dispatch |
| Evaluation | Heuristic judge (free, deterministic), any OpenAI-compatible LLM judge (OpenAI, Azure, vLLM, Ollama), **Ragas 0.4**; `ragops judge check` |
| Drift & privacy | Built-in four-layer drift + **Evidently** reports; regex or **Microsoft Presidio** PII redaction |
| Reports & alerts | Markdown, HTML and native **PDF** reports; Slack, Teams, PagerDuty, webhook and SMTP alerts with test-send |
| UI | React + TypeScript + Recharts, served by the API at `/ui`; Streamlit dashboard as a lightweight alternative |
| SDKs | Python, TypeScript; LangChain, LlamaIndex, Haystack adapters; OTLP receiver |
| Deploy | Docker (multi-stage, non-root), Docker Compose (Postgres, Redis, worker), Helm (migration hook, `helm test`), GitHub Actions |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `RAGOPS_DATABASE_URL` | `sqlite:///./ragops.db` | e.g. `postgresql+psycopg://user:pass@host/ragops` |
| `RAGOPS_ADMIN_KEY` | dev key (warns) | Instance admin key: creates projects |
| `RAGOPS_JUDGE` | `heuristic` | `heuristic`, `openai`, `ollama` or `ragas` |
| `RAGOPS_JUDGE_MODEL` / `RAGOPS_JUDGE_BASE_URL` | `gpt-4o-mini` / – | Judge model and any OpenAI-compatible endpoint |
| `RAGOPS_EMBEDDER` / `RAGOPS_EMBEDDING_DIM` | `hashing` / `384` | `hashing`, `openai`, `sentence-transformers` |
| `RAGOPS_VECTOR_BACKEND` | `auto` | `auto`, `pgvector`, `json` |
| `RAGOPS_CLICKHOUSE_URL` | – | Enables the ClickHouse analytics mirror |
| `RAGOPS_PIPELINE_ALLOWED_HOSTS` | – | Private hosts the server may call when testing registered RAG endpoints |
| `RAGOPS_REDIS_URL` | – | Instant job dispatch through Arq/Redis (Postgres still stores every job) |
| `RAGOPS_AUTO_MIGRATE` | `true` | Upgrade the PostgreSQL schema on start (set `false` when a migration job runs it) |
| `RAGOPS_OIDC_ISSUER` / `_CLIENT_ID` / `_CLIENT_SECRET` | – | Single sign-on; see [deployment](docs/deployment.md#single-sign-on-oidc) |
| `RAGOPS_PUBLIC_URL` | – | External URL for SSO redirects and links in alerts |
| `RAGOPS_REDACTOR` | `regex` | `regex` or `presidio` |
| `RAGOPS_SMTP_HOST` … | – | E-mail alerts (`_PORT`, `_USER`, `_PASSWORD`, `_SECURITY`, `_FROM`) |
| `RAGOPS_INLINE_JOBS` | `false` | Process jobs inside the API (single-process mode) |

Per-project settings (sampling, judge budget, redaction, retention, thresholds, auto-golden rule) live on the UI's
**Settings** page. More: [docs/](docs/index.md).

## Repository layout

```text
ragops/                Python package: API, worker, SDK, diagnosis, drift, gate, runs, pipelines, CLI
  server/              FastAPI app, OTLP receiver, importers
  sdk/                 Python SDK + LangChain / LlamaIndex / Haystack adapters
  pipelines.py runs.py bring-your-own-RAG adapters and the test-run engine
  doctor.py            RAG doctor: diagnosis playbook and verified treatment trials
  migrations/          Alembic revisions        sso.py  OIDC        queue_arq.py  Redis dispatch
  vectors.py           pgvector storage    clickhouse.py   ClickHouse analytics
  benchmark*.py        synthetic and SQuAD 2.0 benchmarks
web/                   React UI (Vite + TypeScript)
sdk-ts/                TypeScript SDK (@ragops/sdk)
examples/              doctor walkthrough, frameworks (LlamaIndex, LangChain, LangGraph, Haystack), case study, CI gate, OpenTelemetry, SDK quickstarts
benchmark/             published benchmark results
deploy/helm/           Helm chart          docs/   MkDocs site          tests/   Python tests + TS SDK tests
```

## Roadmap

- [x] 0.1–0.6: SDKs, OTLP, evaluation, stage diagnosis, golden sets, drift, SLOs, release gate, judge calibration, RAG doctor, SSO, PostgreSQL / Redis / ClickHouse, Helm
- [x] 0.7–0.9: test generation, assurance reports, grounding guard, cost and ranking metrics, claim-level evidence, evidence graph, failure intelligence, release guardian, benchmark lab, dark dashboard
- [x] 0.10: first public release (PyPI, Docker image, docs website)
- [ ] Next: larger LLM-judged benchmarks, multi-turn and agent traces, Indian-language test generation, SCIM provisioning, hosted version

## Limitations (honest ones)

- The default rule-based judge is free and reproducible but misjudges paraphrased answers; use an LLM judge for real
  scoring and check it with `ragops judge check`.
- LLM answers vary between runs: run each version more than once, and use 50+ questions before trusting small
  differences. The guardian's significance test helps, but small test sets stay noisy.
- Generated test questions are a first draft; read them before relying on them (the case study shows why).
- The assurance report is supporting evidence, not a certification of compliance.
- Changing the embedder changes vector dimensions; re-register the corpus afterwards.

## Contributing

Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md), the [code of conduct](CODE_OF_CONDUCT.md)
and [SUPPORT.md](SUPPORT.md). Security reports: [SECURITY.md](SECURITY.md). Release process: [docs/releasing.md](docs/releasing.md).

If you use RAGOps in a report or paper, please cite it ([CITATION.cff](CITATION.cff)).

## License

[MIT](LICENSE) © 2026 Arathi S. SQuAD 2.0 (CC BY-SA 4.0) is downloaded at benchmark time, not redistributed.
