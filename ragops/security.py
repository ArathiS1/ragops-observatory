"""API keys and role-based access control."""

from __future__ import annotations

import hashlib
import hmac
import secrets

ROLE_RANK = {"viewer": 0, "ingest": 1, "editor": 2, "admin": 3}
# ingest keys can only write traces/corpus; they cannot read data back (safe to embed in services)
INGEST_ONLY = "ingest"


def generate_key() -> tuple[str, str, str]:
    """Return (plaintext_key, prefix, hash). Plaintext is shown once and never stored."""
    raw = "rgo_" + secrets.token_urlsafe(32)
    return raw, raw[:12], hash_key(raw)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def verify(raw: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_key(raw), key_hash)


def role_allows(role: str, required: str) -> bool:
    if required == "ingest":
        return role in ("ingest", "editor", "admin")
    if role == INGEST_ONLY:
        return False
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[required]
