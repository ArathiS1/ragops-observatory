# Quickstart

## 1. Run it

=== "Local"
    ```bash
    pip install "ragops-observatory[server,dashboard]"
    ragops demo          # optional: 14 days of realistic demo traffic
    ragops serve &       # API :8000
    ragops worker &      # background evaluation
    ragops dashboard     # UI :8501
    ```
=== "Docker"
    ```bash
    cp .env.example .env    # set RAGOPS_ADMIN_KEY
    docker compose --profile demo up
    ```

Single process for laptops: `ragops serve --inline-jobs` processes jobs inside the API (no worker needed).

## 2. Create a project and keys

```bash
curl -X POST localhost:8000/v1/projects -H "Authorization: Bearer $RAGOPS_ADMIN_KEY" \
     -H "Content-Type: application/json" -d '{"name": "support-bot"}'
# -> {"admin_key": "rgo_..."}   (shown once)
curl -X POST localhost:8000/v1/projects/support-bot/keys -H "Authorization: Bearer rgo_admin..." \
     -H "Content-Type: application/json" -d '{"name": "prod-service", "role": "ingest"}'
```

## 3. Register your corpus

Do this after every (re)index. The corpus registry is what separates a *knowledge gap* from a *retrieval miss*,
finds *chunking defects*, and flags answers built on *stale* document versions.

```python
rg.upsert_corpus([{"doc_id": "refund-policy", "title": "Refunds", "text": "..."}], chunk_words=120)
# or send your own chunks: {"doc_id": ..., "chunks": [{"chunk_id": "refund-policy#0", "text": "..."}]}
```

Changed content creates a new document version automatically (content hash). `delete_missing=True` marks documents
absent from the batch as deleted.

## 4. Trace requests

See [Integrations](integrations.md) for the SDK, decorators, LangChain/LlamaIndex and OpenTelemetry.

## 5. Set SLOs, alerts and the auto-golden rule

Dashboard → **SLOs & alerts** and **Settings**, or the API:

```bash
curl -X POST .../v1/projects/support-bot/slos -d '{"name":"Grounded","metric":"faithfulness","threshold":0.7,"target":0.9}'
curl -X POST .../v1/projects/support-bot/alert-channels -d '{"kind":"slack","target":"https://hooks.slack.com/..."}'
curl -X PATCH .../v1/projects/support-bot/settings -d '{"auto_golden":{"enabled":true,"min_confidence":0.75}}'
```
