"""Root-cause attribution benchmark.

We take the demo knowledge base, deliberately inject each of the seven failure types into otherwise healthy RAG
calls, and measure how often the classifier names the stage we broke. A healthy control set measures false alarms.

Every injected case carries the signal a production system would have (a failed golden case or a thumbs-down),
so the benchmark measures *attribution*, separate from failure *detection*. Stale knowledge is the exception: it
is flagged without any failure signal, because the observatory can see that the context came from an old version.

Run it with ``ragops benchmark`` (writes benchmark/RESULTS.md).
"""

from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from ragops.demo.corpus import DOCS, GAP_QUESTIONS
from ragops.demo.pipeline import ABSTAIN, Index, chunk
from ragops.diagnosis import LABELS, CorpusChunk, CorpusView, diagnose
from ragops.eval.judges import HeuristicJudge, evaluate
from ragops.eval.types import ContextChunk, RagSample
from ragops.textutil import content_tokens, coverage, sentences

CHUNK_WORDS = 45


@dataclass
class Case:
    injected: str  # one of LABELS or "ok"
    sample: RagSample
    feedback: float | None


def _cc(c: dict, rank: int, version: int = 2) -> ContextChunk:
    return ContextChunk(text=c["text"], doc_id=c["doc_id"], chunk_id=c["chunk_id"], doc_version=version,
                        score=c.get("score"), rank=rank)


def _answer_from(query: str, chunks: list[dict]) -> str:
    q = content_tokens(query)
    best, bs, bc = None, 0.0, None
    for c in chunks:
        for s in sentences(c["text"]):
            sc = coverage(q, content_tokens(s))
            if sc > bs:
                best, bs, bc = s, sc, c
    if best is None or bs < 0.34:
        return ABSTAIN
    return f"{best} [{bc['doc_id']}]"


def _corrupt(answer: str, rng: random.Random) -> str:
    nums = re.findall(r"\d+", answer)
    if nums and rng.random() < 0.6:
        return answer.replace(nums[0], str(int(nums[0]) * 3 + 11), 1)
    return answer.split(" [")[0] + " It also includes unlimited free storage for every guest and a lifetime discount."


def build_cases(seed: int = 0) -> tuple[list[Case], CorpusView]:
    rng = random.Random(seed)
    all_chunks = [c for d in DOCS for c in chunk(d["doc_id"], d["text"], CHUNK_WORDS, 2)]
    idx = Index(all_chunks)
    corpus = CorpusView([CorpusChunk(c["chunk_id"], c["doc_id"], 2, int(c["chunk_id"].split("#")[1]), c["text"])
                         for c in all_chunks], None, {d["doc_id"]: 2 for d in DOCS}, set())
    by_doc = defaultdict(list)
    for c in all_chunks:
        by_doc[c["doc_id"]].append(c)
    cases: list[Case] = []

    def gold_for(q: str, doc: str) -> dict:
        qt = content_tokens(q)
        return max(by_doc[doc], key=lambda c: coverage(qt, content_tokens(c["text"])))

    questions = [(q, d["doc_id"]) for d in DOCS for q in d["questions"]]
    for q, doc in questions:
        gold = gold_for(q, doc)
        cands = idx.search(q, 8)
        if gold["chunk_id"] not in {c["chunk_id"] for c in cands}:
            cands = [{**gold, "score": 0.5}] + cands[:7]
        others = [c for c in cands if c["doc_id"] != doc]
        noise = [c for c in all_chunks if c["doc_id"] != doc]

        # healthy control
        ctx = [gold] + others[:2]
        cases.append(Case("ok", RagSample(q, _answer_from(q, ctx), [_cc(c, i + 1) for i, c in enumerate(ctx)],
                                          [_cc(c, i + 1) for i, c in enumerate(cands)]), None))
        # generation failure: right context, corrupted answer
        cases.append(Case("generation_failure", RagSample(
            q, _corrupt(_answer_from(q, ctx), rng), [_cc(c, i + 1) for i, c in enumerate(ctx)],
            [_cc(c, i + 1) for i, c in enumerate(cands)]), -1.0))
        # ranking failure: gold retrieved, dropped before the prompt
        ctx_r = others[:3]
        cases.append(Case("ranking_failure", RagSample(
            q, _answer_from(q, ctx_r), [_cc(c, i + 1) for i, c in enumerate(ctx_r)],
            [_cc(c, i + 1) for i, c in enumerate([*others[:3], gold, *others[3:6]])]), -1.0))
        # retrieval miss: the gold document is absent from the candidates
        miss = [c for c in idx.search(q, 20) if c["doc_id"] != doc][:6]
        cases.append(Case("retrieval_miss", RagSample(
            q, _answer_from(q, miss[:3]), [_cc(c, i + 1) for i, c in enumerate(miss[:3])],
            [_cc(c, i + 1) for i, c in enumerate(miss)]), -1.0))
        # context overload: gold buried at position 6 of 10 among unrelated chunks
        filler = rng.sample(noise, 9)
        ctx_o = filler[:5] + [gold] + filler[5:]
        cases.append(Case("context_overload", RagSample(
            q, _answer_from(q, filler[:2]), [_cc(c, i + 1) for i, c in enumerate(ctx_o)],
            [_cc(c, i + 1) for i, c in enumerate(ctx_o)]), -1.0))
        # stale knowledge: context served from document version 1 while the registry is at version 2
        ctx_s = [gold] + others[:2]
        cases.append(Case("stale_knowledge", RagSample(
            q, _answer_from(q, ctx_s), [_cc(c, i + 1, version=1 if c is gold else 2) for i, c in enumerate(ctx_s)],
            [_cc(c, i + 1) for i, c in enumerate(cands)]), None))

    # knowledge gap: questions the corpus does not cover, answered confidently anyway
    for q in GAP_QUESTIONS:
        cands = idx.search(q, 6)
        cases.append(Case("knowledge_gap", RagSample(
            q, "Yes, this is fully supported on all plans. [pricing-plans]", [_cc(c, i + 1) for i, c in enumerate(cands[:3])],
            [_cc(c, i + 1) for i, c in enumerate(cands)]), -1.0))

    # chunking defect: questions built from the tail of chunk i and the head of chunk i+1 of the same document
    for doc, chs in by_doc.items():
        for a, b in zip(chs, chs[1:]):
            ta = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]+", a["text"]) if len(w) > 4][-4:]
            tb = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]+", b["text"]) if len(w) > 4][:4]
            if len(ta) < 3 or len(tb) < 3:
                continue
            q = f"How does {' '.join(ta).lower()} relate to {' '.join(tb).lower()}?"
            cands = idx.search(q, 6)
            ctx_c = [a] + [c for c in cands if c["chunk_id"] not in (a["chunk_id"], b["chunk_id"])][:2]
            cases.append(Case("chunking_defect", RagSample(
                q, _answer_from(q, [a]), [_cc(c, i + 1) for i, c in enumerate(ctx_c)],
                [_cc(c, i + 1) for i, c in enumerate(cands)]), -1.0))
    return cases, corpus


def run_benchmark(seed: int = 0) -> dict:
    cases, corpus = build_cases(seed)
    judge = HeuristicJudge()
    labels = LABELS + ["ok"]
    confusion = {t: Counter() for t in labels}
    for c in cases:
        c.sample.corpus_max_similarity = float(corpus.relevance(c.sample.query).max())
        scores = {k: v.score for k, v in evaluate(c.sample, judge).items()}
        dx = diagnose(c.sample, scores, corpus, c.feedback)
        confusion[c.injected][dx.label if dx.is_failure else "ok"] += 1  # an alarm = flagged as a failure
    per_label = {}
    for lab in labels:
        n = sum(confusion[lab].values())
        tp = confusion[lab][lab]
        predicted = sum(confusion[t][lab] for t in labels)
        per_label[lab] = {"n": n, "recall": round(tp / n, 3) if n else None,
                          "precision": round(tp / predicted, 3) if predicted else None}
    injected = [c for c in cases if c.injected != "ok"]
    correct = sum(confusion[c][c] for c in LABELS)
    ok_n = sum(confusion["ok"].values())
    return {
        "cases": len(cases), "injected_failures": len(injected),
        "attribution_accuracy": round(correct / len(injected), 3),
        "macro_recall": round(float(np.mean([per_label[lab]["recall"] for lab in LABELS])), 3),
        "false_alarm_rate": round(1 - confusion["ok"]["ok"] / ok_n, 3) if ok_n else None,
        "per_label": per_label,
        "confusion": {t: dict(confusion[t]) for t in labels},
    }


def to_markdown(r: dict) -> str:
    labels = LABELS + ["ok"]
    lines = [
        "# Root-cause attribution benchmark", "",
        "Generated by `ragops benchmark`. Each case is a healthy RAG call with one failure injected on purpose; "
        "the classifier must name the pipeline stage that was broken. Judge: deterministic heuristic judge "
        "(no LLM calls), so the numbers are exactly reproducible.", "",
        f"- **Cases:** {r['cases']} ({r['injected_failures']} injected failures + healthy controls)",
        f"- **Attribution accuracy:** {r['attribution_accuracy']:.1%}",
        f"- **Macro recall across the 7 stages:** {r['macro_recall']:.1%}",
        f"- **False-alarm rate on healthy calls:** {r['false_alarm_rate']:.1%}", "",
        "## Per stage", "", "| Injected failure | Cases | Recall | Precision |", "|---|---|---|---|",
    ]
    for lab in labels:
        pl = r["per_label"][lab]
        rec = "–" if pl["recall"] is None else f"{pl['recall']:.0%}"
        pre = "–" if pl["precision"] is None else f"{pl['precision']:.0%}"
        lines.append(f"| {lab} | {pl['n']} | {rec} | {pre} |")
    lines += ["", "## Confusion matrix (rows = injected, columns = predicted)", "",
              "| injected \\ predicted | " + " | ".join(lab.replace("_", " ") for lab in labels) + " |",
              "|---" * (len(labels) + 1) + "|"]
    for t in labels:
        lines.append(f"| **{t}** | " + " | ".join(str(r["confusion"][t].get(p, 0) or "·") for p in labels) + " |")
    lines += ["", "## How failures are injected", "",
              "| Stage | Injection |", "|---|---|",
              "| knowledge_gap | Questions on topics absent from the corpus, answered confidently |",
              "| stale_knowledge | Context chunk served from document v1 while the registry holds v2 |",
              "| chunking_defect | Question built from the end of chunk *i* and the start of chunk *i+1*; only chunk *i* in context |",
              "| retrieval_miss | Every chunk of the relevant document removed from the candidates |",
              "| ranking_failure | Relevant chunk in the retriever candidates but not in the prompt |",
              "| context_overload | Relevant chunk buried at position 6 of 10 unrelated chunks |",
              "| generation_failure | Relevant chunk at rank 1, answer corrupted (wrong figure or invented claim) |",
              "", "Limitations: the corpus is small (18 documents) and synthetic, and failures are injected one at a "
              "time. Real systems mix failure types; treat these numbers as a regression baseline for the classifier, "
              "not as a claim about every production system.", ""]
    return "\n".join(lines)
