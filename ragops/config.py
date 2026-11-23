"""Runtime settings, read from environment variables (prefix RAGOPS_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"RAGOPS_{name}", default)


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./ragops.db"))
    # Bootstrap admin key. Required in production; used to create projects and project keys.
    admin_key: str = field(default_factory=lambda: _env("ADMIN_KEY", "ragops-admin-dev-key"))
    # Judge: "heuristic" (no API key, deterministic), "openai" or "ollama" (OpenAI-compatible endpoint).
    judge: str = field(default_factory=lambda: _env("JUDGE", "heuristic"))
    judge_model: str = field(default_factory=lambda: _env("JUDGE_MODEL", "gpt-4o-mini"))
    judge_base_url: str | None = field(default_factory=lambda: _env("JUDGE_BASE_URL"))
    # Embeddings: "hashing" (local, no download) or "openai" / "sentence-transformers".
    embedder: str = field(default_factory=lambda: _env("EMBEDDER", "hashing"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "text-embedding-3-small"))
    embedding_dim: int = field(default_factory=lambda: int(_env("EMBEDDING_DIM", "384")))
    # Vector storage: auto | pgvector | json  (see ragops/vectors.py)
    vector_backend: str = field(default_factory=lambda: _env("VECTOR_BACKEND", "auto"))
    # Above this many chunks per project, diagnosis uses index candidates instead of the in-memory corpus.
    inmemory_corpus_limit: int = field(default_factory=lambda: int(_env("INMEMORY_CORPUS_LIMIT", "50000")))
    # Optional ClickHouse analytics mirror, e.g. http://default:@clickhouse:8123/ragops
    clickhouse_url: str | None = field(default_factory=lambda: _env("CLICKHOUSE_URL"))
    worker_poll_seconds: float = field(default_factory=lambda: float(_env("WORKER_POLL_SECONDS", "1.0")))
    slo_check_minutes: float = field(default_factory=lambda: float(_env("SLO_CHECK_MINUTES", "15")))
    # When true, the API processes jobs inline (single-process mode, used by tests and the demo).
    inline_jobs: bool = field(default_factory=lambda: _env("INLINE_JOBS", "false").lower() == "true")

    @property
    def is_dev_admin_key(self) -> bool:
        return self.admin_key == "ragops-admin-dev-key"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
