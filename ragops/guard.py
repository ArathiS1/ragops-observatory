"""Grounding guardrail: check an answer against the passages it was built from BEFORE a user sees it.

Monitoring finds a wrong answer after the customer read it; the guard stops it. For every statement in the answer:
1. supported word for word by a context sentence (same numbers and negations) -> supported, no model call;
2. otherwise, if an LLM judge is configured, the judge decides that one statement;
3. otherwise a lexical check: most of the statement's content words appear in one passage.

Policy (per call): `block` an answer with any unsupported statement, `rewrite` it to a safe fallback, or only `flag`
it. An answer that declines ("I don't know") always passes. The result lists which statements failed, so the
application can show a partial answer or route to a human.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ragops.textutil import extractive_support, is_abstention, sentences

FALLBACK = "I don't know. I couldn't find this in the documents I have."


@dataclass
class GuardResult:
    decision: str                       # allow | block | rewrite | flag
    supported_ratio: float
    answer: str                         # the answer to show (fallback when rewritten)
    statements: list[dict] = field(default_factory=list)
    checked_by: str = "rules"

    def to_dict(self) -> dict:
        return asdict(self)


def _lexical(statement: str, texts: list[str], min_cover: float = 0.7) -> bool:
    """Most content words in one context sentence (or two adjacent ones), with the SAME numbers and negations:
    "valid for 30 minutes" is not supported by "valid for 15 minutes"."""
    return extractive_support(statement, texts, min_cover=min_cover)


def check(answer: str, contexts: list[str], question: str = "", judge=None, policy: str = "rewrite",
          min_supported: float = 1.0, fallback: str = FALLBACK) -> GuardResult:
    if policy not in ("block", "rewrite", "flag"):
        raise ValueError("policy must be block, rewrite or flag")
    answer = (answer or "").strip()
    if not answer or is_abstention(answer):
        return GuardResult("allow", 1.0, answer, [], "rules")
    texts = [c for c in contexts if c and c.strip()]
    stmts = sentences(answer) or [answer]
    out, used_llm = [], False
    use_llm = judge is not None and getattr(judge, "name", "heuristic") != "heuristic"
    for s in stmts:
        if texts and extractive_support(s, texts):
            out.append({"statement": s, "supported": True, "how": "verbatim"})
            continue
        if use_llm and texts:
            from ragops.eval.types import ContextChunk, RagSample

            r = judge.score("faithfulness", RagSample(question or "", s, [ContextChunk(t) for t in texts]))
            if r.score is not None and getattr(r, "judge", "") != "heuristic":
                used_llm = True
                out.append({"statement": s, "supported": r.score >= 0.7, "how": "llm", "reason": r.reasoning})
                continue
        out.append({"statement": s, "supported": bool(texts) and _lexical(s, texts), "how": "lexical"})
    ratio = sum(1 for o in out if o["supported"]) / len(out)
    by = "rules+llm" if used_llm else "rules"
    if ratio >= min_supported:
        return GuardResult("allow", round(ratio, 3), answer, out, by)
    if policy == "flag":
        return GuardResult("flag", round(ratio, 3), answer, out, by)
    if policy == "block":
        return GuardResult("block", round(ratio, 3), "", out, by)
    return GuardResult("rewrite", round(ratio, 3), fallback, out, by)
