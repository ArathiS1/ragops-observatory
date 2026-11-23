import pytest
from fastapi.testclient import TestClient

from ragops.config import reset_settings
from ragops.db import init_db, reset_engine
from ragops.demo.corpus import DOCS

ADMIN = {"Authorization": "Bearer ragops-admin-dev-key"}

# Settings from the developer's own shell (an LLM judge, a server to push to, a model provider) must not leak into
# the tests: they would call a paid API, take minutes and change the expected scores.
_ISOLATE = ("RAGOPS_JUDGE", "RAGOPS_JUDGE_BASE_URL", "RAGOPS_JUDGE_MODEL", "RAGOPS_JUDGE_API_KEY",
            "RAGOPS_JUDGE_MAX_TOKENS", "RAGOPS_JUDGE_RETRIES", "RAGOPS_URL", "RAGOPS_API_KEY", "RAGOPS_PROJECT",
            "RAGOPS_ENVIRONMENT", "RAGOPS_PRICING_FILE", "RAGOPS_REDIS_URL", "RAGOPS_CLICKHOUSE_URL",
            "RAGOPS_OIDC_ISSUER", "RAGOPS_ADMIN_KEY", "RAGOPS_DATABASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "RAG_LLM", "SARVAM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "LLM_BASE_URL")


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in _ISOLATE:
        monkeypatch.delenv(name, raising=False)
    reset_settings()
    yield
    reset_settings()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGOPS_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("RAGOPS_INLINE_JOBS", "true")
    reset_settings()
    reset_engine()
    from ragops import services

    services._corpus_cache.clear()
    init_db()
    yield
    reset_engine()
    reset_settings()


@pytest.fixture()
def client(env):
    from ragops.server.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def project(client):
    """A project with the demo corpus registered. Returns (name, project_admin_headers)."""
    r = client.post("/v1/projects", json={"name": "p1", "settings": {"sample_rate": 1.0}}, headers=ADMIN)
    assert r.status_code == 201
    h = {"Authorization": f"Bearer {r.json()['admin_key']}"}
    docs = [{"doc_id": d["doc_id"], "title": d["title"], "text": d["text"]} for d in DOCS]
    r = client.post("/v1/projects/p1/corpus", json={"documents": docs, "chunk_words": 45}, headers=h)
    assert r.json()["added"] == len(DOCS)
    return "p1", h


def demo_trace(query: str, cfg: dict | None = None, **kw) -> dict:
    from ragops.demo import pipeline as pl

    out = pl.run(query, cfg or {})
    return {"query": query, "answer": out["answer"], "retrieved": out["retrieved"], "contexts": out["contexts"], **kw}
