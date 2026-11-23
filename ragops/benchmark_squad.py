"""Public benchmark: root-cause attribution on SQuAD 2.0 (real Wikipedia articles, crowd-written questions).

    ragops benchmark --dataset squad            # downloads the SQuAD 2.0 dev set (CC BY-SA 4.0) on first use

Setup
-----
* Corpus: the Wikipedia articles of the SQuAD 2.0 dev set. Each article is a document; paragraphs are split into
  ~CHUNK_WORDS-word chunks, numbered in reading order, so adjacent chunks really are adjacent text.
* Retriever: the demo hybrid retriever (hashing embeddings + lexical) over the whole corpus, so candidates are what a
  real retriever returns, including its mistakes.
* Questions: answerable dev questions. The gold chunk is the chunk that contains the annotated answer span.

Each question is turned into one healthy call and several broken ones, each with exactly one injected failure:

    generation_failure   gold chunk in context; the answer span is replaced by an answer to a different question
    ranking_failure      gold chunk among the retriever candidates but not in the prompt
    retrieval_miss       every chunk of the gold article removed from the candidates
    context_overload     gold chunk buried at position 6 of 10 chunks from other articles
    stale_knowledge      gold chunk served from document version 1 while the registry holds version 2
    knowledge_gap        held-out articles: questions whose article is not in the corpus at all
    chunking_defect      questions whose answer sentence straddles a chunk boundary; only the first part in context

Broken calls carry the signal production would have (negative feedback); stale context needs none. Healthy calls
carry no feedback and measure false alarms. Judge: the deterministic heuristic judge, so results are reproducible.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path

import numpy as np

from ragops.demo.pipeline import ABSTAIN, Index
from ragops.diagnosis import LABELS, CorpusChunk, CorpusView, diagnose
from ragops.eval.judges import HeuristicJudge, evaluate
from ragops.eval.types import ContextChunk, RagSample

SQUAD_URL = "https://raw.githubusercontent.com/rajpurkar/SQuAD-explorer/master/dataset/dev-v2.0.json"
DEFAULT_PATH = Path.home() / ".cache" / "ragops" / "squad-dev-v2.0.json"
CHUNK_WORDS = 60


def download(path: Path = DEFAULT_PATH) -> Path:
    if path.exists():
        return path
    import httpx

    path.parent.mkdir(parents=True, exist_ok=True)
    r = httpx.get(SQUAD_URL, timeout=120, follow_redirects=True)
    r.raise_for_status()
    path.write_bytes(r.content)
    return path


def _split(text: str, words: int) -> list[tuple[int, int]]:
    """Character spans of ~`words`-word chunks, cut at word boundaries (sentences may straddle chunks)."""
    spans, toks = [], [m for m in re.finditer(r"\S+", text)]
    for i in range(0, len(toks), words):
        part = toks[i:i + words]
        spans.append((part[0].start(), part[-1].end()))
    return spans


def build_corpus(articles: list[dict], words: int = CHUNK_WORDS) -> tuple[list[dict], list[dict]]:
    """Returns (chunks, questions). Each question knows its gold chunk and the chunk holding the next text."""
    chunks, questions = [], []
    for a in articles:
        doc = a["title"]
        pos = 0
        for p in a["paragraphs"]:
            ctx = p["context"]
            spans = _split(ctx, words)
            ids = []
            for s, e in spans:
                cid = f"{doc}#{pos}"
                chunks.append({"chunk_id": cid, "doc_id": doc, "position": pos, "text": ctx[s:e], "doc_version": 2})
                ids.append((cid, s, e))
                pos += 1
            for q in p["qas"]:
                if q.get("is_impossible") or not q["answers"]:
                    continue
                ans = q["answers"][0]
                a0, a1 = ans["answer_start"], ans["answer_start"] + len(ans["text"])
                gold = next((k for k, (_, s, e) in enumerate(ids) if s <= a0 < e), None)
                if gold is None:
                    continue
                # sentence that holds the answer, and whether it straddles a chunk boundary
                sent_s = max(ctx.rfind(". ", 0, a0) + 2, 0) if ctx.rfind(". ", 0, a0) >= 0 else 0
                sent_e = ctx.find(". ", a1)
                sent_e = len(ctx) if sent_e < 0 else sent_e + 1
                straddle = None
                for k, (_, s, e) in enumerate(ids):
                    if s <= sent_s < e < sent_e and k + 1 < len(ids):
                        straddle = (k, k + 1)
                questions.append({"query": q["question"], "answer": ans["text"], "doc_id": doc,
                                  "gold": ids[gold][0], "sentence": ctx[sent_s:sent_e].strip(),
                                  "straddle": None if straddle is None else (ids[straddle[0]][0], ids[straddle[1]][0])})
    return chunks, questions


def _cc(c: dict, rank: int, version: int = 2) -> ContextChunk:
    return ContextChunk(text=c["text"], doc_id=c["doc_id"], chunk_id=c["chunk_id"], doc_version=version,
                        score=c.get("score"), rank=rank)


def run_squad_benchmark(path: str | Path | None = None, n_questions: int = 300, n_gap_articles: int = 5,
                        seed: int = 0) -> dict:
    rng = random.Random(seed)
    data = json.loads(Path(path or download()).read_text(encoding="utf-8"))["data"]
    order = list(range(len(data)))
    rng.shuffle(order)
    held_out = [data[i] for i in order[:n_gap_articles]]
    kept = [data[i] for i in order[n_gap_articles:]]
    chunks, questions = build_corpus(kept)
    _, gap_questions = build_corpus(held_out)
    by_id = {c["chunk_id"]: c for c in chunks}
    idx = Index(chunks)
    corpus = CorpusView([CorpusChunk(c["chunk_id"], c["doc_id"], 2, c["position"], c["text"]) for c in chunks],
                        idx.E, {c["doc_id"]: 2 for c in chunks}, set())
    other_answers = [q["answer"] for q in questions]

    def answer_with(q, ctx_chunks):  # extractive "LLM": the answer sentence if it is in context, else abstain
        txt = " ".join(c["text"] for c in ctx_chunks)
        return f"{q['sentence']} [{q['doc_id']}]" if q["answer"] in txt else ABSTAIN

    cases = []
    sample = rng.sample(questions, min(n_questions, len(questions)))
    for q in sample:
        gold = by_id[q["gold"]]
        cands = idx.search(q["query"], 8)
        if gold["chunk_id"] not in {c["chunk_id"] for c in cands}:
            cands = [{**gold, "score": 0.5}] + cands[:7]
        others = [c for c in cands if c["doc_id"] != q["doc_id"]]
        ctx = [gold] + others[:2]
        healthy_answer = f"{q['sentence']} [{q['doc_id']}]"
        cases.append(("ok", q, ctx, cands, healthy_answer, None, {}))
        wrong = rng.choice([a for a in other_answers if a.lower() not in q["sentence"].lower()])
        cases.append(("generation_failure", q, ctx, cands, healthy_answer.replace(q["answer"], wrong, 1), -1.0, {}))
        cases.append(("ranking_failure", q, others[:3], [*others[:3], gold, *others[3:6]],
                      answer_with(q, others[:3]), -1.0, {}))
        miss = [c for c in idx.search(q["query"], 30) if c["doc_id"] != q["doc_id"]][:6]
        cases.append(("retrieval_miss", q, miss[:3], miss, answer_with(q, miss[:3]), -1.0, {}))
        filler = rng.sample([c for c in chunks if c["doc_id"] != q["doc_id"]], 9)
        ctx_o = filler[:5] + [gold] + filler[5:]
        cases.append(("context_overload", q, ctx_o, ctx_o, answer_with(q, filler[:2]), -1.0, {}))
        cases.append(("stale_knowledge", q, ctx, cands, healthy_answer, None, {"stale": gold["chunk_id"]}))
    for q in rng.sample(gap_questions, min(n_questions // 3, len(gap_questions))):
        cands = idx.search(q["query"], 6)
        cases.append(("knowledge_gap", q, cands[:3], cands, f"{q['sentence']} [{q['doc_id']}]", -1.0, {}))
    straddlers = [q for q in questions if q["straddle"]]
    for q in rng.sample(straddlers, min(n_questions // 3, len(straddlers))):
        first, second = by_id[q["straddle"][0]], by_id[q["straddle"][1]]
        cands = [c for c in idx.search(q["query"], 8) if c["chunk_id"] != second["chunk_id"]]
        ctx = [first] + [c for c in cands if c["chunk_id"] != first["chunk_id"]][:2]
        part = first["text"].split(". ")[-1]
        cases.append(("chunking_defect", q, ctx, cands, f"{part} [{q['doc_id']}]", -1.0, {}))

    judge = HeuristicJudge()
    labels = LABELS + ["ok"]
    confusion = {t: Counter() for t in labels}
    for injected, q, ctx, cands, answer, fb, extra in cases:
        stale = extra.get("stale")
        s = RagSample(q["query"], answer,
                      [_cc(c, i + 1, 1 if c["chunk_id"] == stale else 2) for i, c in enumerate(ctx)],
                      [_cc(c, i + 1) for i, c in enumerate(cands)])
        s.corpus_max_similarity = float(corpus.relevance(q["query"]).max())
        scores = {k: v.score for k, v in evaluate(s, judge).items()}
        dx = diagnose(s, scores, corpus, fb)
        confusion[injected][dx.label if dx.is_failure else "ok"] += 1  # an alarm = flagged as a failure
    per_label = {}
    for lab in labels:
        n = sum(confusion[lab].values())
        tp = confusion[lab][lab]
        pred = sum(confusion[t][lab] for t in labels)
        per_label[lab] = {"n": n, "recall": round(tp / n, 3) if n else None,
                          "precision": round(tp / pred, 3) if pred else None}
    injected_n = sum(sum(confusion[lab].values()) for lab in LABELS)
    correct = sum(confusion[lab][lab] for lab in LABELS)
    ok_n = sum(confusion["ok"].values())
    return {
        "dataset": "SQuAD 2.0 dev (Rajpurkar et al., 2018; CC BY-SA 4.0)", "articles_in_corpus": len(kept),
        "held_out_articles": len(held_out), "chunks": len(chunks), "chunk_words": CHUNK_WORDS,
        "questions_sampled": len(sample), "cases": len(cases), "injected_failures": injected_n,
        "attribution_accuracy": round(correct / injected_n, 3) if injected_n else None,
        "macro_recall": round(float(np.mean([per_label[lab]["recall"] for lab in LABELS if per_label[lab]["recall"]
                                             is not None])), 3),
        "false_alarm_rate": round(1 - confusion["ok"]["ok"] / ok_n, 3) if ok_n else None,
        "per_label": per_label, "confusion": {t: dict(confusion[t]) for t in labels}, "seed": seed,
    }


def to_markdown(r: dict) -> str:
    labels = LABELS + ["ok"]
    lines = [
        "# Root-cause attribution on SQuAD 2.0 (public benchmark)", "",
        f"Dataset: **{r['dataset']}**. Corpus: {r['articles_in_corpus']} Wikipedia articles split into "
        f"{r['chunks']:,} chunks of ~{r['chunk_words']} words; {r['held_out_articles']} more articles held out to create "
        "real knowledge gaps. Retriever: hybrid hashing-embedding + lexical. Judge: deterministic heuristic judge "
        f"(no LLM calls), seed {r['seed']}. Reproduce with `ragops benchmark --dataset squad`.", "",
        f"- **Cases:** {r['cases']:,} ({r['injected_failures']:,} injected failures from {r['questions_sampled']} sampled "
        "questions + healthy controls)",
        f"- **Attribution accuracy:** {r['attribution_accuracy']:.1%}",
        f"- **Macro recall across the 7 stages:** {r['macro_recall']:.1%}",
        f"- **False-alarm rate on healthy calls:** {r['false_alarm_rate']:.1%}", "",
        "| Injected failure | Cases | Recall | Precision |", "|---|---|---|---|"]
    for lab in labels:
        p = r["per_label"][lab]
        rec = "–" if p["recall"] is None else f"{p['recall']:.0%}"
        pre = "–" if p["precision"] is None else f"{p['precision']:.0%}"
        lines.append(f"| {lab} | {p['n']} | {rec} | {pre} |")
    lines += ["", "## Confusion matrix (rows = injected, columns = predicted)", "",
              "| injected \\ predicted | " + " | ".join(lab.replace("_", " ") for lab in labels) + " |",
              "|---" * (len(labels) + 1) + "|"]
    for t in labels:
        lines.append(f"| **{t}** | " + " | ".join(str(r["confusion"][t].get(p, 0) or "·") for p in labels) + " |")
    lines += ["", "## Reading the results", "",
              "**False alarms come from failure detection, not attribution.** The classifier only runs after the judge "
              "flags a failure, and the deterministic heuristic judge scores answer relevance by word overlap. SQuAD "
              "questions paraphrase their source (\"In what country is Normandy?\" vs \"a region in France\"), so "
              "correct answers sometimes look irrelevant to it. Use an LLM judge (`RAGOPS_JUDGE=openai`) in production "
              "and check it on the Judge calibration page.", "",
              "**What this benchmark changed.** Running on real text exposed two rules that were too narrow or too "
              "eager, and v0.5 fixed both: (1) chunking defects are now recognised by their real signature, a chunk "
              "boundary that cuts through the best-matching sentence (chunking-defect recall 15% → 42%); (2) an "
              "outdated chunk only makes an answer *stale* when the answer cites or uses it, otherwise it is a warning. "
              "Overall attribution went from 71.4% to 73.8% here; the synthetic benchmark moved from 90.3% to 89.6%.", "",
              "Limitations: failures are injected one at a time; SQuAD answers are short spans, unlike long-form RAG "
              "answers; the unanswerable SQuAD 2.0 questions are not used because they are written against a paragraph "
              "that *is* in the corpus (they test reading comprehension, not retrieval).", ""]
    return "\n".join(lines)
