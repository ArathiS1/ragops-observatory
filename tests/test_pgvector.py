"""Runs against a real PostgreSQL + pgvector when RAGOPS_TEST_PG_URL is set (CI does this); skipped otherwise."""

import os

import pytest
from sqlalchemy import text

PG = os.environ.get("RAGOPS_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG, reason="set RAGOPS_TEST_PG_URL to run PostgreSQL/pgvector tests")


@pytest.fixture()
def pg(monkeypatch):
    from ragops import services
    from ragops.config import reset_settings
    from ragops.db import get_engine, init_db, reset_engine

    monkeypatch.setenv("RAGOPS_DATABASE_URL", PG)
    monkeypatch.setenv("RAGOPS_VECTOR_BACKEND", "pgvector")
    monkeypatch.setenv("RAGOPS_INMEMORY_CORPUS_LIMIT", "10")  # force the index-candidate diagnosis path
    reset_settings()
    reset_engine()
    services._corpus_cache.clear()
    with get_engine().begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    init_db()
    yield get_engine()
    reset_engine()
    reset_settings()


def test_pgvector_schema_search_and_diagnosis(pg):
    from ragops import services as svc
    from ragops.db import session_scope
    from ragops.demo import pipeline as pl
    from ragops.demo.corpus import DOCS, GAP_QUESTIONS
    from ragops.schemas import CorpusIn, DocumentIn, TraceIn
    from ragops.vectors import backend_name
    from ragops.worker import drain

    assert backend_name() == "pgvector"
    with pg.connect() as c:
        assert c.execute(text("select format_type(atttypid, atttypmod) from pg_attribute where attrelid='chunks'::regclass "
                              "and attname='embedding'")).scalar() == "vector(384)"
        idx = c.execute(text("select indexdef from pg_indexes where indexname='ix_chunks_embedding_hnsw'")).scalar()
        assert "hnsw" in idx and "vector_cosine_ops" in idx
    with session_scope() as db:
        p = svc.create_project(db, "pgv", {"sample_rate": 1.0})
        svc.upsert_corpus(db, p, CorpusIn(documents=[DocumentIn(doc_id=d["doc_id"], text=d["text"]) for d in DOCS],
                                          chunk_words=45))
        hits = svc.search_corpus(db, p, "How long do deleted files stay in the trash?", 3)
        assert hits["backend"] == "pgvector" and hits["results"][0]["doc_id"] == "data-retention"
        for i, (q, cfg) in enumerate([("How much does the Pro plan cost per user?", {}),
                                      ("What is the API rate limit on the Pro plan?", {"exclude_docs": ["api-rate-limits"]}),
                                      (GAP_QUESTIONS[0], {})]):
            out = pl.run(q, cfg)
            ans = "Yes, it works offline everywhere." if i == 2 else out["answer"]
            svc.ingest_trace(db, p, TraceIn(id=f"t{i}", query=q, answer=ans, retrieved=out["retrieved"],
                                            contexts=out["contexts"], user_feedback=-1 if i else None))
    drain()
    with session_scope() as db:
        from ragops.models import Trace

        labels = {t.id: t.diagnosis.label for t in db.query(Trace).all()}
        assert labels == {"t0": "ok", "t1": "retrieval_miss", "t2": "knowledge_gap"}
        p = svc.get_project_by_name(db, "pgv")
        assert svc.query_drift(db, p)["n_current"] >= 0  # vector columns round-trip into numpy
