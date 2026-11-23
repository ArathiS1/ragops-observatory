# Feature map

Where each capability of a production RAG reliability platform lives in RAGOps, and what is deliberately left out.

| Capability | Where | Since |
| --- | --- | --- |
| Python SDK, per-stage timing (`with t.retrieve()`, `t.generate()`), sessions | `ragops/sdk/client.py` | 0.1 |
| TypeScript SDK | `sdk-ts/` | 0.5 |
| OpenTelemetry / OTLP ingestion (protobuf or JSON) | `POST /otlp/v1/traces` | 0.4 |
| LangChain, LlamaIndex, Haystack adapters; import from Langfuse and others | `ragops/sdk/adapters.py`, `/import/{source}` | 0.4 |
| Environments (dev / staging / production) | `environment` on traces and the client | 0.8 |
| Trace viewer: retrieved, reranked and prompt chunks, stale versions, scores, diagnosis | dashboard Traces | 0.1 |
| Claim-level evidence (supported / contradicted / not found) | trace page, `reliability.claim_evidence` | 0.8 |
| Faithfulness, answer relevance, context relevance and recall, correctness, abstention, citations | `ragops/eval/judges.py` | 0.1 |
| Ragas metrics as an alternative judge | `RAGOPS_JUDGE=ragas` | 0.3 |
| LLM judge calibration against human labels; `ragops judge check` | Judge calibration page | 0.4 |
| Precision@K, Recall@K, MRR, nDCG | `reliability.ranking_metrics` | 0.8 |
| Stage diagnosis: knowledge gap, stale knowledge, chunking, retrieval miss, ranking, overload, generation | `ragops/diagnosis.py` | 0.1 |
| Failure patterns (grouped failures) | Failure patterns page | 0.8 |
| Document freshness, duplicates and conflicts | Knowledge base page, `kb_health.py` | 0.4 |
| Drift: queries, retrieval scores, quality, corpus; Evidently reports | Drift page, `/drift/evidently` | 0.2 |
| Latency p50 / p95, tokens, serving and judge cost | Overview, Cost page | 0.1 / 0.8 |
| Versioned price table, estimated cost, per-project prices | `reliability.price_table` | 0.8 |
| Golden sets, promote failures to tests, versioned test runs, run comparison | Golden sets, Test your RAG | 0.3 |
| Test generation from documents, unanswerable questions, security probes | `ragops generate [--security]` | 0.7 / 0.8 |
| Statistical release gate, latency / error budgets, JUnit, GitHub Action | `ragops gate`, `action.yml` | 0.2 / 0.8 |
| RAG doctor: tests candidate fixes | RAG doctor page, `ragops doctor` | 0.5 |
| Assurance report: verdict, fingerprint, regulatory evidence map | `ragops assure`, Assurance page | 0.7 / 0.8 |
| Grounding guard before answers reach users | `client.guard`, `/guard` | 0.7 |
| SLOs, error budgets, burn rate; alerts to Slack, Teams, PagerDuty, email, webhooks | SLOs and alerts | 0.3 |
| Incident context with alerts | `/incident`, alert bodies | 0.8 |
| Organisations, projects, API keys with roles (admin, editor, viewer, ingest), OIDC single sign-on | Organisation, Settings | 0.6 |
| PII redaction before storage (Presidio or rules), hashed-only mode, retention | Settings | 0.2 |
| Audit log; data export and deletion | `/audit`, `/export`, `DELETE /data` | 0.3 / 0.8 |
| PostgreSQL + pgvector, Alembic migrations, Redis queue, ClickHouse analytics | `deploy/`, `docker-compose.yml` | 0.5 |
| Docker Compose and a Helm chart for Kubernetes | `docker-compose.yml`, `deploy/helm` | 0.4 |

## Deliberately not included

- **Prompt management, playgrounds, an LLM gateway, fine-tuning.** Other tools do these well; RAGOps stays focused on
  proving that answers are right.
- **Celery and object storage (MinIO / S3).** The built-in database queue, or Redis with arq, handles evaluation jobs;
  reports are small enough for the database. Add object storage when artefacts grow.
- **Claims of certification.** Load, penetration and SOC 2 or ISO 27001 testing need real infrastructure and external
  auditors; the assurance report is supporting evidence, not a certificate.
