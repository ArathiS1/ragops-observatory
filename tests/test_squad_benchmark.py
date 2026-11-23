from pathlib import Path

import pytest

from ragops.benchmark_squad import DEFAULT_PATH, _split, build_corpus, run_squad_benchmark


def test_split_and_gold_chunk():
    text = "Alpha beta gamma. Delta epsilon zeta eta. Theta iota kappa."
    spans = _split(text, 4)
    assert [text[s:e] for s, e in spans][0] == "Alpha beta gamma. Delta"
    art = [{"title": "Doc", "paragraphs": [{"context": text, "qas": [
        {"question": "What follows delta?", "is_impossible": False,
         "answers": [{"text": "epsilon", "answer_start": text.index("epsilon")}]}]}]}]
    chunks, qs = build_corpus(art, 4)
    assert qs[0]["gold"] == "Doc#1" and qs[0]["straddle"] == ("Doc#0", "Doc#1")
    assert [c["position"] for c in chunks] == [0, 1, 2]


@pytest.mark.skipif(not Path(DEFAULT_PATH).exists(), reason="SQuAD dev set not downloaded (ragops benchmark --dataset squad)")
def test_squad_benchmark_guard_rails():
    r = run_squad_benchmark(n_questions=60)
    assert r["attribution_accuracy"] >= 0.6 and r["per_label"]["stale_knowledge"]["recall"] >= 0.85
