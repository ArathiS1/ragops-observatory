# REST API

Interactive OpenAPI docs: `http://localhost:8000/docs`. Authenticate with `Authorization: Bearer <key>`.

| Method & path | Role | Purpose |
|---|---|---|
| `POST /v1/projects` | instance admin | Create project (returns its admin key) |
| `POST /v1/projects/{p}/keys` · `GET` · `DELETE …/{id}` | admin | Manage keys |
| `GET/PATCH /v1/projects/{p}/settings` | viewer/admin | Sampling, budget, privacy, retention, auto-golden |
| `POST /v1/projects/{p}/traces` · `/traces/batch` | ingest | Ingest traces (idempotent on `id`) |
| `POST /v1/projects/{p}/traces/{id}/feedback` | ingest | User feedback (−1..1) |
| `POST /otlp/v1/traces` | ingest | OTLP/HTTP receiver: protobuf or JSON, optionally gzip |
| `POST /v1/projects/{p}/corpus` · `DELETE /corpus/{doc_id}` | ingest/editor | Corpus registry |
| `POST /v1/projects/{p}/import/{langfuse|phoenix}` | editor | Backfill history |
| `GET /v1/projects/{p}/overview` · `/root-causes` · `/timeline` | viewer | Health, stages, change timeline |
| `GET /v1/projects/{p}/traces` · `/traces/{id}` | viewer | Explorer with evidence |
| `GET /v1/projects/{p}/drift` · `/drift/{query|retrieval|corpus|quality}` | viewer | Drift |
| `GET /v1/projects/{p}/kb-health` | viewer | Knowledge-base health |
| `POST /v1/projects/{p}/labels` · `GET /calibration` | editor/viewer | Judge calibration |
| `POST /v1/projects/{p}/golden/promote` · `GET /golden-sets/{n}/export` | editor/viewer | Golden sets |
| `GET/POST /v1/projects/{p}/slos` · `POST /slos/check` · `GET /alerts` | viewer/editor | SLOs & alerts |
| `GET /v1/projects/{p}/report?format=md|html|json|pdf` | viewer | Reliability report (PDF rendered natively) |
| `GET /v1/projects/{p}/drift/evidently` | viewer | Evidently drift report (HTML) for queries and embeddings |
| `GET /v1/projects/{p}/doctor?days=` | viewer | RAG doctor on production traffic (`format=md` for Markdown) |
| `GET /v1/projects/{p}/runs/{id}/doctor` · `POST …/doctor/trial` | viewer/editor | Diagnose a test run; test fixes and get the verified one |
| `POST /v1/projects/{p}/alert-channels/{id}/test` · `DELETE …/{id}` | admin | Send a real test alert / remove a channel |
| `GET /v1/projects/{p}/analytics` | viewer | Traffic, failure rate, latency, per-config quality (ClickHouse when configured) |
| `GET /v1/projects/{p}/corpus/search?q=` | viewer | Nearest chunks (pgvector HNSW on PostgreSQL) |
| `GET/POST /v1/projects/{p}/pipelines` · `DELETE …/{name}` | viewer/editor | Register your RAG's HTTP endpoint |
| `POST /v1/projects/{p}/pipelines/{name}/check` | editor | Ask one question, see what was parsed and how it scored |
| `POST /v1/projects/{p}/runs` · `GET /runs` · `GET /runs/{id}` | editor/viewer | Test runs on a golden set or pasted cases |
| `POST /v1/projects/{p}/runs/upload` | editor | Store a run from `ragops test --push` |
| `GET /v1/projects/{p}/runs/compare?base=&candidate=` | viewer | Paired comparison with bootstrap intervals |
| `GET /v1/projects/{p}/audit` | admin | Audit log |
| `POST /v1/orgs` · `GET /v1/orgs` · `GET /v1/orgs/{o}` | instance admin / viewer | Organisations and their project health |
| `POST/GET/DELETE /v1/orgs/{o}/keys` | org admin | Organisation-wide keys |
| `POST /v1/orgs/{o}/projects/{p}/move` | instance admin | Move a project between organisations |
| `GET /auth/config` · `/auth/oidc/login` · `/auth/oidc/callback` · `POST /auth/oidc/exchange` | public | Single sign-on |
| `GET /auth/me` · `POST /auth/logout` | any | Current identity; revoke an SSO session |


## Grounding guardrail

`POST /v1/projects/{project}/guard` (role: ingest or higher)

```json
{"answer": "The link lasts 2 hours.", "contexts": ["A reset link valid for 15 minutes is sent..."],
 "question": "How long is the reset link valid?", "policy": "rewrite", "min_supported": 1.0, "use_llm": false}
```

Response: `{"decision": "allow|block|rewrite|flag", "answer": "...", "supported_ratio": 0.0,
"statements": [{"statement": "...", "supported": false, "how": "lexical"}], "checked_by": "rules"}`.
See [assurance](assurance.md#3-grounding-guardrail).

## Reliability analytics (0.8)

| Endpoint | Role | What it returns |
| --- | --- | --- |
| `GET /v1/projects/{p}/environments?days=30` | viewer | traces per environment |
| `GET /v1/projects/{p}/costs?days=30&environment=` | viewer | serving and evaluation cost by model, version, environment, day |
| `GET /v1/projects/{p}/failure-patterns?days=7` | viewer | failures grouped by stage and topic |
| `GET /v1/projects/{p}/incident?days=1` | viewer | signals, recent changes, affected traces, next checks |
| `GET /v1/projects/{p}/runs/{run_id}/assurance?format=json\|html` | viewer | assurance report for a finished run |
| `GET /v1/projects/{p}/export` | admin | everything stored for the project (audited) |
| `DELETE /v1/projects/{p}/data?session_id=&before=&confirm=` | admin | deletes traces (audited) |

`GET /traces` accepts `environment=`. Trace ingestion accepts `environment`. See [reliability](reliability.md).
