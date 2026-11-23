"""Small, dependency-free text helpers shared by the heuristic judge and the diagnosis engine."""

from __future__ import annotations

import re

STOPWORDS = frozenset(
    """a an the and or but if then else of to in on at by for with about against between into through during
    before after above below from up down out over under again further once here there when where why how all
    any both each few more most other some such no nor not only own same so than too very can will just should
    now is are was were be been being have has had having do does did doing i me my we our you your he him his
    she her it its they them their what which who whom this that these those am would could may might must shall
    also as per via etc please tell know want need get thanks thank hi hello hey quick question
    anyone help""".split()
)

_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-\.%$]*[a-z0-9%]|[a-z0-9]")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_NUM = re.compile(r"(?<![\w.])\$?\d+(?:[.,]\d+)?%?")

ABSTAIN_PATTERNS = re.compile(
    r"\b(i (do not|don't) know|i'm not sure|i am not sure|not (able|enough information) to|"
    r"no (relevant )?information|cannot find|can't find|couldn't find|unable to (find|answer)|"
    r"not covered|does not (mention|contain|say))\b",
    re.I,
)


def _stem(t: str) -> str:
    for suf in ("ing", "ies", "es", "ed", "s"):
        if len(t) > len(suf) + 2 and t.endswith(suf):
            return t[: -len(suf)] + ("y" if suf == "ies" else "")
    return t


def tokens(text: str | None) -> list[str]:
    if not text:
        return []
    return _TOKEN.findall(text.lower())


def content_tokens(text: str | None) -> set[str]:
    return {_stem(t) for t in tokens(text) if t not in STOPWORDS and len(t) > 1}


def sentences(text: str | None) -> list[str]:
    if not text:
        return []
    parts = [s.strip() for s in _SENT.split(text.strip())]
    return [p for p in parts if p]


def numbers(text: str | None) -> set[str]:
    if not text:
        return set()
    return {n.replace(",", "") for n in _NUM.findall(text)}


def coverage(needle: set[str], haystack: set[str]) -> float:
    """Fraction of `needle` tokens present in `haystack`."""
    if not needle:
        return 1.0
    return len(needle & haystack) / len(needle)


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / max(1, len(a | b))


_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u2032": "'"})


def is_abstention(answer: str | None) -> bool:
    # models often write a typographic apostrophe ("I don\u2019t know"), which the pattern must still match
    return bool(answer) and bool(ABSTAIN_PATTERNS.search((answer or "").translate(_QUOTES)))


_NEGATIONS = {"no", "not", "never", "cannot", "nor", "none", "without"}


def _negations(text: str) -> set[str]:
    return {t for t in tokens(text.replace("n't", " not")) if t in _NEGATIONS}


def extractive_support(statement: str, chunks: list[str], min_cover: float = 0.85) -> bool:
    """True when a sentence of the context (or two consecutive ones) contains the statement nearly word for word:
    >= min_cover of its content words, every number in it, and exactly the same negations (so "plans are
    refundable" is not matched by "plans are not refundable"). Copying from the context is supported by
    definition, so such a statement needs no model call; anything paraphrased or partial still goes to the judge."""
    norm = statement.translate(_QUOTES).lower()
    need = content_tokens(norm)
    if len(need) < 2:
        return False
    nums = set(re.findall(r"\d+(?:[.,]\d+)?", norm))
    negs = _negations(norm)
    for c in chunks:
        sents = [x.lower() for x in sentences(c.translate(_QUOTES))] or [c.lower()]
        spans = sents + [a + " " + b for a, b in zip(sents, sents[1:])]
        for span in spans:
            have = content_tokens(span)
            if len(need & have) / len(need) < min_cover:
                continue
            if nums <= set(re.findall(r"\d+(?:[.,]\d+)?", span)) and negs == _negations(span):
                return True
    return False


def top_terms(texts: list[str], k: int = 4) -> list[str]:
    counts: dict[str, int] = {}
    for t in texts:
        for tok in {x for x in tokens(t) if x not in STOPWORDS and len(x) > 2 and not x.isdigit()}:
            counts[tok] = counts.get(tok, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]
