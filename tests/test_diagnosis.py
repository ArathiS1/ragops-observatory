import pytest

from ragops.demo.corpus import DOCS, GAP_QUESTIONS
from ragops.demo.pipeline import chunk
from ragops.diagnosis import CorpusChunk, CorpusView, diagnose
from ragops.eval.judges import HeuristicJudge, evaluate
from ragops.eval.types import ContextChunk, RagSample


@pytest.fixture(scope="module")
def corpus():
    chunks = [c for d in DOCS for c in chunk(d["doc_id"], d["text"], 45, 2)]
    return CorpusView([CorpusChunk(c["chunk_id"], c["doc_id"], 2, int(c["chunk_id"].split("#")[1]), c["text"])
                       for c in chunks], None, {d["doc_id"]: 2 for d in DOCS}, set()), {c["chunk_id"]: c for c in chunks}


def cc(c, rank=1, version=2):
    return ContextChunk(c["text"], c["doc_id"], c["chunk_id"], version, 0.5, rank)


def run(sample, corpus, feedback=-1.0):
    sample.corpus_max_similarity = float(corpus.relevance(sample.query).max())
    scores = {k: v.score for k, v in evaluate(sample, HeuristicJudge()).items()}
    return diagnose(sample, scores, corpus, feedback)


Q = "How long do deleted files stay in the trash?"


def test_healthy_is_ok(corpus):
    cv, ch = corpus
    g = ch["data-retention#0"]
    s = RagSample(Q, "Deleted files stay in the trash for 30 days and can be restored by any editor. [data-retention]",
                  [cc(g)], [cc(g)])
    dx = run(s, cv, feedback=None)
    assert dx.label == "ok" and not dx.is_failure


def test_knowledge_gap(corpus):
    cv, ch = corpus
    s = RagSample(GAP_QUESTIONS[0], "Yes it works offline.", [cc(ch["pricing-plans#0"])])
    assert run(s, cv).label == "knowledge_gap"


def test_stale_without_complaint(corpus):
    cv, ch = corpus
    g = ch["data-retention#0"]
    s = RagSample(Q, "Deleted files stay in the trash for 30 days.", [cc(g, version=1)])
    dx = run(s, cv, feedback=None)
    assert dx.label == "stale_knowledge" and dx.evidence["stale_documents"][0]["current_version"] == 2


def test_deleted_document_is_stale(corpus):
    cv, ch = corpus
    view = CorpusView(cv.chunks, cv.embeddings, dict(cv.current_version), {"data-retention"})
    s = RagSample(Q, "Deleted files stay in the trash for 30 days.", [cc(ch["data-retention#0"])])
    assert run(s, view).label == "stale_knowledge"


def test_retrieval_miss(corpus):
    cv, ch = corpus
    wrong = [cc(ch["backups#0"]), cc(ch["pricing-plans#0"], 2)]
    s = RagSample(Q, "I couldn't find information about that.", wrong, wrong)
    assert run(s, cv).label == "retrieval_miss"


def test_ranking_failure(corpus):
    cv, ch = corpus
    cands = [cc(ch["backups#0"]), cc(ch["data-retention#0"], 2), cc(ch["pricing-plans#0"], 3)]
    s = RagSample(Q, "I couldn't find information about that.", [cands[0]], cands)
    dx = run(s, cv)
    assert dx.label == "ranking_failure" and dx.evidence["gold_rank_in_candidates"] == 2


def test_generation_failure(corpus):
    cv, ch = corpus
    g = cc(ch["data-retention#0"])
    s = RagSample(Q, "Deleted files stay in the trash for 99 days.", [g], [g])
    assert run(s, cv).label == "generation_failure"


def test_context_overload(corpus):
    cv, ch = corpus
    noise = [c for k, c in ch.items() if not k.startswith("data-retention")][:9]
    ctx = [cc(c, i + 1) for i, c in enumerate(noise[:5])] + [cc(ch["data-retention#0"], 6)] + \
          [cc(c, i + 7) for i, c in enumerate(noise[5:9])]
    s = RagSample(Q, "I couldn't find information about that.", ctx, ctx)
    assert run(s, cv).label == "context_overload"


def test_diagnosis_without_corpus():
    s = RagSample(Q, "I couldn't find that.", [ContextChunk("Backups run every 6 hours.", "backups", "b#0")])
    scores = {k: v.score for k, v in evaluate(s).items()}
    dx = diagnose(s, scores, None, -1)
    assert dx.label == "retrieval_miss" and "corpus not registered" in dx.evidence["note"]


def test_unused_stale_chunk_is_a_warning_not_a_failure(corpus):
    cv, ch = corpus
    g = ch["data-retention#0"]
    other = ch["pricing-plans#0"]
    s = RagSample(Q, "Deleted files stay in the trash for 30 days and can be restored by any editor. [data-retention]",
                  [cc(g), cc(other, 2, version=1)], [cc(g)])
    dx = run(s, cv, feedback=None)
    assert dx.label == "ok" and dx.evidence["outdated_unused"][0]["doc_id"] == "pricing-plans"


def test_app_with_its_own_chunk_ids_is_not_a_retrieval_miss(corpus):
    """LlamaIndex/LangChain chunk ids ("doc@0") never equal the registered corpus ids ("doc#0"); a retrieved chunk
    that carries the gold text must still count as retrieved, so a bad answer is a generation failure."""
    view, by_id = corpus
    gold = max(view.chunks, key=lambda c: float(view.relevance(Q)[view.chunks.index(c)]))
    ctx = [ContextChunk(gold.text, gold.doc_id, f"{gold.doc_id}@0", None, 0.9, 1)]
    s = RagSample(Q, "Deleted files are kept forever and can never be restored.", ctx)
    dx = run(s, view)
    assert dx.is_failure and dx.label != "retrieval_miss", dx
