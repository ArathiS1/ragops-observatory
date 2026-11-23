"""Alembic migrations against a real PostgreSQL (+pgvector). Skipped unless RAGOPS_TEST_PG_URL is set."""

import json
import os

import pytest
from sqlalchemy import create_engine, text

PG = os.environ.get("RAGOPS_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG, reason="set RAGOPS_TEST_PG_URL to run migration tests")


@pytest.fixture()
def eng(monkeypatch):
    from ragops.config import reset_settings
    from ragops.db import reset_engine

    monkeypatch.setenv("RAGOPS_DATABASE_URL", PG)
    reset_settings()
    reset_engine()
    e = create_engine(PG)
    with e.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    yield e
    e.dispose()
    reset_engine()
    reset_settings()


def test_fresh_install_matches_models(eng):
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from ragops import migrate
    from ragops.db import init_db
    from ragops.models import Base

    init_db()
    assert migrate.current(eng) == migrate.head() == "0003_v06_orgs_sso"
    with eng.connect() as c:
        ctx = MigrationContext.configure(c, opts={"compare_type": False, "include_name": lambda name, kind, parent:
                                                  not (kind == "index" and str(name).endswith("_hnsw"))})
        diffs = compare_metadata(ctx, Base.metadata)
        vec = c.execute(text("select format_type(atttypid, atttypmod) from pg_attribute "
                             "where attrelid='chunks'::regclass and attname='embedding'")).scalar()
    assert diffs == [], diffs
    assert vec == "vector(384)"


def test_upgrade_populated_v04_database_keeps_data(eng):
    """Build the exact v0.4 schema, fill it like a v0.4 install would, then upgrade to head."""
    from fastapi.testclient import TestClient

    from ragops import migrate

    migrate.upgrade(eng, "0001_v04_baseline")
    emb = json.dumps([0.0] * 383 + [1.0])
    from ragops.security import generate_key

    raw, prefix, h = generate_key()
    with eng.begin() as c:
        c.execute(text("INSERT INTO projects (id, name, settings, created_at) VALUES ('p1', 'legacy', '{}', now())"))
        c.execute(text("INSERT INTO api_keys (id, project_id, name, prefix, key_hash, role, created_at, revoked) "
                       "VALUES ('k1', 'p1', 'old', :pre, :h, 'admin', now(), false)"), {"pre": prefix, "h": h})
        c.execute(text("INSERT INTO documents (id, project_id, doc_id, version, title, content_hash, meta, deleted, "
                       "is_current, created_at) VALUES ('d1','p1','faq',1,'FAQ','x','{}',false,true,now())"))
        c.execute(text("INSERT INTO chunks (id, project_id, chunk_id, doc_id, doc_version, position, text, embedding, "
                       "is_current) VALUES ('c1','p1','faq#0','faq',1,0,'Refunds take 5 days.',CAST(:e AS json),true)"),
                  {"e": emb})
        c.execute(text("INSERT INTO traces (id, project_id, query, answer, source, eval_status, meta, created_at, "
                       "query_embedding) VALUES ('t1','p1','How long do refunds take?','5 days','sdk','done','{}', "
                       "now(), CAST(:e AS json))"), {"e": emb})
    assert migrate.upgrade(eng) == "0003_v06_orgs_sso"
    with eng.connect() as c:
        assert c.execute(text("select count(*) from traces")).scalar() == 1
        assert c.execute(text("select format_type(atttypid, atttypmod) from pg_attribute where "
                              "attrelid='chunks'::regclass and attname='embedding'")).scalar() == "vector(384)"
        assert c.execute(text("select embedding::text from chunks")).scalar().endswith(",1]")
        assert c.execute(text("select o.name from projects p join organizations o on o.id=p.org_id")).scalar() == "default"
        assert c.execute(text("select count(*) from eval_runs")).scalar() == 0
    # the v0.4 key still works through the v0.6 API, and the old data is readable
    from ragops.server.app import app

    with TestClient(app) as client:
        hdr = {"Authorization": f"Bearer {raw}"}
        assert client.get("/v1/projects/legacy/traces/t1", headers=hdr).json()["query"] == "How long do refunds take?"
        hits = client.get("/v1/projects/legacy/corpus/search", params={"q": "refunds"}, headers=hdr).json()
        assert hits["backend"] == "pgvector" and hits["results"][0]["chunk_id"] == "faq#0"


def test_database_created_without_migrations_is_stamped(eng):
    from ragops import migrate

    migrate.upgrade(eng, "0002_v05_runs_vectors")
    with eng.begin() as c:
        c.execute(text("DROP TABLE alembic_version"))  # what a v0.5 create_all install looks like
    assert migrate.detect_legacy_revision(eng) == "0002_v05_runs_vectors"
    assert migrate.upgrade(eng) == "0003_v06_orgs_sso"


def test_downgrade_roundtrip(eng):
    from ragops import migrate

    migrate.upgrade(eng)
    from alembic import command

    cfg = migrate._config(eng)
    command.downgrade(cfg, "0001_v04_baseline")
    assert migrate.current(eng) == "0001_v04_baseline"
    assert migrate.upgrade(eng) == "0003_v06_orgs_sso"
