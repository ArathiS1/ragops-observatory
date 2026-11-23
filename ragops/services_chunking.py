"""Paragraph-aware word chunker (shared by the corpus registry and the demo pipeline; no DB imports)."""

from __future__ import annotations


def chunk_text(text: str, words: int = 120) -> list[str]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    for p in paras:
        w = p.split()
        if cur and len(cur) + len(w) > words:
            chunks.append(" ".join(cur))
            cur = []
        while len(w) > words:
            chunks.append(" ".join(w[:words]))
            w = w[words:]
        cur.extend(w)
    if cur:
        chunks.append(" ".join(cur))
    return chunks
