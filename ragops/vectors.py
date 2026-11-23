"""Vector storage: pgvector on PostgreSQL, JSON everywhere else.

``RAGOPS_VECTOR_BACKEND``:
    auto      (default) use pgvector when the database is PostgreSQL and the extension can be enabled
    pgvector  require pgvector (startup fails if the extension is missing)
    json      always store embeddings as JSON arrays (portable, fine up to ~50k chunks per project)

With pgvector, chunk and query embeddings live in ``vector(N)`` columns with HNSW cosine indexes, so corpus search
and root-cause diagnosis stay fast on corpora with millions of chunks: diagnosis pulls the top candidates from the
index instead of loading the whole corpus into memory.
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import JSON, text
from sqlalchemy.types import TypeDecorator

from ragops.config import get_settings

log = logging.getLogger("ragops.vectors")
_state = {"pgvector": False}


def pgvector_enabled() -> bool:
    return _state["pgvector"]


def backend_name() -> str:
    return "pgvector" if _state["pgvector"] else "json"


class EmbeddingType(TypeDecorator):
    """vector(N) when pgvector is active, JSON otherwise. Values are always python lists / numpy arrays."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql" and _state["pgvector"]:
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(get_settings().embedding_dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in (value.tolist() if hasattr(value, "tolist") else value)]


def prepare(engine) -> None:
    """Called by init_db before create_all: decide the backend and enable the extension."""
    mode = get_settings().vector_backend
    _state["pgvector"] = False
    if engine.dialect.name != "postgresql" or mode == "json":
        return
    try:
        import pgvector  # noqa: F401

        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        _state["pgvector"] = True
    except Exception as e:  # noqa: BLE001
        if mode == "pgvector":
            raise RuntimeError(f"RAGOPS_VECTOR_BACKEND=pgvector but the extension is unavailable: {e}") from e
        log.warning("pgvector unavailable (%s); storing embeddings as JSON", type(e).__name__)


def sync_with_schema(engine) -> None:
    """Use pgvector only if the columns really are vectors (an old database may still have JSON columns)."""
    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as conn:
        t = conn.execute(text("SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                              "WHERE attrelid = to_regclass('chunks') AND attname = 'embedding'")).scalar()
    if t is None:
        return
    is_vec = t.startswith("vector")
    if _state["pgvector"] != is_vec:
        log.warning("embedding columns are %s; vector backend set to %s", t, "pgvector" if is_vec else "json")
    _state["pgvector"] = is_vec


def create_indexes(engine) -> None:
    if not _state["pgvector"]:
        return
    with engine.begin() as conn:
        for table, col in (("chunks", "embedding"), ("traces", "query_embedding")):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{col}_hnsw ON {table} "
                              f"USING hnsw ({col} vector_cosine_ops)"))


def nearest_chunks(db, project_id: str, q_vec, k: int = 20) -> list[dict]:
    """Top-k current chunks by cosine similarity. Uses the HNSW index under pgvector, numpy otherwise."""
    from sqlalchemy import select

    from ragops.models import Chunk

    q = [float(x) for x in q_vec]
    if _state["pgvector"]:
        lit = "[" + ",".join(f"{x:.6f}" for x in q) + "]"
        rows = db.execute(text(
            "SELECT chunk_id, doc_id, doc_version, position, text, 1 - (embedding <=> CAST(:q AS vector)) AS sim "
            "FROM chunks WHERE project_id = :p AND is_current ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"),
            {"q": lit, "p": project_id, "k": k}).all()
        return [{"chunk_id": r.chunk_id, "doc_id": r.doc_id, "doc_version": r.doc_version, "position": r.position,
                 "text": r.text, "similarity": round(float(r.sim), 4)} for r in rows]
    rows = db.execute(select(Chunk.chunk_id, Chunk.doc_id, Chunk.doc_version, Chunk.position, Chunk.text,
                             Chunk.embedding).where(Chunk.project_id == project_id, Chunk.is_current.is_(True))).all()
    if not rows:
        return []
    E = np.array([r.embedding for r in rows], dtype=np.float32)
    sims = E @ np.asarray(q, dtype=np.float32)
    order = np.argsort(-sims)[:k]
    return [{"chunk_id": rows[i].chunk_id, "doc_id": rows[i].doc_id, "doc_version": rows[i].doc_version,
             "position": rows[i].position, "text": rows[i].text, "similarity": round(float(sims[i]), 4)}
            for i in order]
