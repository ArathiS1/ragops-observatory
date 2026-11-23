# Deployment & security

## Docker Compose
`docker compose up` starts PostgreSQL (pgvector), Redis, the API (which serves the UI at `/ui`) and a worker.
`docker compose --profile demo up` also seeds a demo project. Scale workers with `docker compose up --scale worker=3`;
job claiming is atomic, and jobs stuck for 10 minutes are reclaimed.

Build the image with extra features by choosing extras:

```bash
docker build --build-arg EXTRAS=server,dashboard,pgvector,redis,sso,presidio,ragas,evidently -t ragops:full .
# behind a registry mirror / air-gapped:
docker build --build-arg NODE_IMAGE=registry.acme.internal/node:22-slim \
             --build-arg PYTHON_IMAGE=registry.acme.internal/python:3.12-slim -t ragops:0.6.0 .
```

The image runs as a non-root user (uid 10001), defaults to SQLite in the `/data` volume, and switches to PostgreSQL
when `RAGOPS_DATABASE_URL` is set.

## Database and migrations

| Backend | When | How |
|---|---|---|
| PostgreSQL + pgvector | Production default | Use a Postgres with the `vector` extension (`pgvector/pgvector:pg16`). Embeddings go to `vector(N)` columns with HNSW cosine indexes; above `RAGOPS_INMEMORY_CORPUS_LIMIT` chunks (50k) diagnosis uses index candidates instead of loading the corpus. `RAGOPS_VECTOR_BACKEND=auto\|pgvector\|json`. |
| PostgreSQL (JSON vectors) | Managed Postgres without extensions | `RAGOPS_VECTOR_BACKEND=json` |
| SQLite | Laptop, tests, single-process demo | default `RAGOPS_DATABASE_URL` |
| ClickHouse (analytics mirror) | Millions of traces per day | `RAGOPS_CLICKHOUSE_URL=http://user:pass@clickhouse:8123/ragops`, then `ragops clickhouse init` and `ragops clickhouse backfill`. Every evaluated trace is mirrored (async inserts) and `/v1/projects/{p}/analytics` is answered from ClickHouse. Postgres stays the system of record. |

PostgreSQL schemas are managed by **Alembic**. On start the API upgrades the database to the latest revision
(`RAGOPS_AUTO_MIGRATE=true`, the default). With several API replicas, run migrations once instead and set
`RAGOPS_AUTO_MIGRATE=false`; the Helm chart does this with a pre-install/pre-upgrade hook Job.

```bash
ragops db upgrade      # to the latest revision (stamps databases created by v0.4/v0.5 first, then upgrades in place)
ragops db current      # revision the database is at
ragops db history
```

Upgrading from v0.4 or v0.5 keeps all data: JSON embeddings are converted to `vector` columns, and existing projects
move into the `default` organisation.

Set `RAGOPS_EMBEDDING_DIM` to your embedder's dimension (384 for the default hashing embedder, 1536 for
`text-embedding-3-small`) before the first start; changing embedders later needs the corpus re-registered.

## Job queue

Evaluation, test runs, imports and SLO checks run in workers. Two dispatch modes, same guarantees:

| Mode | Set | Behaviour |
|---|---|---|
| PostgreSQL queue | nothing | Workers poll the `jobs` table (`FOR UPDATE SKIP LOCKED`), retry with back-off. |
| Arq + Redis | `RAGOPS_REDIS_URL=redis://redis:6379/0` | Jobs are dispatched to workers instantly through Redis. Postgres still stores every job (transactional outbox), and a sweep every `RAGOPS_SWEEP_SECONDS` (30) re-dispatches anything Redis lost, so a Redis restart never drops work. |

`ragops worker --backend auto` (the default) picks Arq when `RAGOPS_REDIS_URL` is set.

## Web UI

The API serves the React UI at `/ui`. For UI development: `cd web && npm install && npm run dev` (proxies API calls
to `localhost:8000`). The Streamlit dashboard is still available (`ragops dashboard`, Compose profile `streamlit`).

## Kubernetes (Helm)

```bash
kubectl create secret generic ragops-db --from-literal=DATABASE_URL=postgresql+psycopg://...
kubectl create secret generic ragops-admin --from-literal=ADMIN_KEY=$(openssl rand -hex 32) \
  --from-literal=OPENAI_API_KEY=sk-... --from-literal=OIDC_CLIENT_SECRET=... --from-literal=SMTP_PASSWORD=...
helm install ragops deploy/helm/ragops-observatory \
  --set redisUrl=redis://redis-master:6379/0 --set publicUrl=https://ragops.acme.dev \
  --set ingress.enabled=true --set ingress.host=ragops.acme.dev
helm test ragops
```

The chart runs migrations in a hook Job, sets a restrictive pod and container security context, restarts pods when
values change, and validates required SSO values. It passes `helm lint --strict` and schema validation against
Kubernetes 1.30.

## Organisations and access control

An **organisation** groups projects (one per RAG application). Keys are scoped to a project or to an organisation;
the instance admin key (`RAGOPS_ADMIN_KEY`) manages everything.

| Role | Can |
|---|---|
| `ingest` | Send traces, feedback and corpus updates. Cannot read anything (safe inside services) |
| `viewer` | Read everything in scope (dashboards, CI gate) |
| `editor` | Plus: SLOs, labels, golden sets, test runs, doctor trials, re-processing, imports |
| `admin` | Plus: keys, settings, alert channels, audit log; an organisation admin also creates projects |

```bash
curl -X POST $URL/v1/orgs -H "X-API-Key: $ADMIN" -d '{"name": "acme"}'      # returns an org admin key
curl $URL/v1/orgs/acme -H "X-API-Key: $ORG_KEY"                             # every project's health
curl -X POST $URL/v1/orgs/acme/projects/support-bot/move -H "X-API-Key: $ADMIN"
```

Keys are stored as SHA-256 hashes and shown once.

## Single sign-on (OIDC)

Works with any OpenID Connect provider (Okta, Microsoft Entra ID, Google Workspace, Keycloak, Auth0). Register a web
application with redirect URI `{RAGOPS_PUBLIC_URL}/auth/oidc/callback`, then:

| Variable | Example |
|---|---|
| `RAGOPS_OIDC_ISSUER` | `https://login.microsoftonline.com/<tenant>/v2.0` |
| `RAGOPS_OIDC_CLIENT_ID` / `RAGOPS_OIDC_CLIENT_SECRET` | from your provider |
| `RAGOPS_PUBLIC_URL` | `https://ragops.acme.dev` |
| `RAGOPS_OIDC_ORG` | organisation SSO users belong to (default `default`) |
| `RAGOPS_OIDC_ROLE_CLAIM` / `RAGOPS_OIDC_ROLE_MAP` | `groups` / `ragops-admins=admin,ragops-editors=editor` |
| `RAGOPS_OIDC_DEFAULT_ROLE` | role when no group matches; `none` denies access |
| `RAGOPS_OIDC_ALLOWED_DOMAINS` | `acme.com` |

The login uses the authorization-code flow with PKCE; ID tokens are verified against the provider's JWKS (signature,
issuer, audience, expiry, nonce). The UI then holds a session token (`rgs_…`, 12 hours by default,
`RAGOPS_OIDC_SESSION_HOURS`) that `POST /auth/logout` revokes. API keys keep working for services and CI.

## Alerts

Alert channels are set per project (UI: *SLOs and alerts*, or `POST /v1/projects/{p}/alert-channels`):
`slack` (incoming webhook, Block Kit message), `teams` (workflow webhook, Adaptive Card), `pagerduty` (Events v2
routing key; repeated firings share a `dedup_key`), `webhook` (JSON) and `email` (SMTP: `RAGOPS_SMTP_HOST`, `_PORT`,
`_USER`, `_PASSWORD`, `_SECURITY=starttls|ssl|none`, `_FROM`; EU PagerDuty accounts set `RAGOPS_PAGERDUTY_URL`).

Verify a channel before relying on it:

```bash
ragops alerts test --kind slack --target https://hooks.slack.com/services/...
curl -X POST $URL/v1/projects/support-bot/alert-channels/<id>/test -H "X-API-Key: $KEY"
# {"ok": true, "kind": "slack", "detail": "HTTP 200"}  — or the receiver's exact error
```

## Testing registered RAG endpoints

The server calls user-registered endpoints when someone starts a test run or doctor trial from the UI. Private,
loopback and link-local addresses are refused unless listed in `RAGOPS_PIPELINE_ALLOWED_HOSTS` (comma-separated, `*`
allows all), which protects internal services and cloud metadata endpoints. Only `editor` and `admin` keys can
register endpoints or start runs. Disable the built-in demo endpoint with `RAGOPS_DEMO_ENDPOINT=false`.

## Privacy

PII redaction runs at ingestion on the fields you choose (`redact_fields`), for the entities you choose
(`redaction_entities`):

| Redactor | Set | Finds |
|---|---|---|
| Regex (default) | — | e-mail, phone, card numbers (Luhn-checked), SSN, IP address, IBAN |
| Microsoft Presidio | `RAGOPS_REDACTOR=presidio`, extra `presidio`, a spaCy model | the above plus **person names and locations**, with NLP context |

Presidio runs fully offline (`RAGOPS_PRESIDIO_MODEL` picks the spaCy model, `RAGOPS_PRESIDIO_THRESHOLD` the minimum
score, default 0.4). Redacted text looks like `I am <PERSON> from <LOCATION>, mail <EMAIL>`.
`store_raw_text: false` keeps only salted hashes. `retention_days` deletes old traces hourly.

## Sizing

One worker with the heuristic judge processes roughly 50–100 traces per second; LLM judges are bounded by the judge
API, so use the sampling rate and daily budget. For very high volume, put PostgreSQL on fast storage, use Redis
dispatch, run several workers and enable the ClickHouse mirror.
