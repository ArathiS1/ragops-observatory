

def test_reasoning_model_think_block_is_ignored():
    from ragops.eval.judges import _parse_json_reply

    reply = '<think>The answer says {maybe} ... score should be high</think>\n{"score": 0.9, "reason": "grounded"}'
    assert _parse_json_reply(reply)["score"] == 0.9


def test_broken_json_reply_still_yields_score():
    from ragops.eval.judges import _parse_json_reply

    reply = '{"score": 0.8, "reason": "The reference includes "35 days" \t\n      '
    assert _parse_json_reply(reply)["score"] == 0.8


def test_refusal_reference_is_scored_without_a_model_call():
    from ragops.eval.judges import LLMJudge
    from ragops.eval.types import RagSample

    j = LLMJudge("x", base_url="http://127.0.0.1:9")  # unreachable: must not be called
    s = RagSample(query="Office in Tokyo?", answer="I don't know.", contexts=[], expected_answer="I don't know")
    r = j.score("answer_correctness", s)
    assert r.score == 1.0 and "heuristic" not in r.reasoning


def test_reference_is_not_shown_to_faithfulness(monkeypatch):
    from ragops.eval.judges import LLMJudge
    from ragops.eval.types import ContextChunk, RagSample

    sent = []

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": '{"score": 1, "reason": "ok"}'}}], "usage": {}}

    j = LLMJudge("x", base_url="http://127.0.0.1:9")
    monkeypatch.setattr(j, "_post", lambda body: sent.append(body) or R())
    s = RagSample(query="Backups?", answer="Snapshots run four times daily.",  # paraphrase: needs the model
                  contexts=[ContextChunk(text="Backups every 6 hours.")], expected_answer="Every 6 hours, kept 35 days.")
    j.score("faithfulness", s)
    j.score("answer_correctness", s)
    assert "REFERENCE" not in sent[0]["messages"][1]["content"]
    assert "REFERENCE" in sent[1]["messages"][1]["content"]


def test_faithfulness_is_counted_from_claim_verdicts():
    from ragops.eval.judges import _score_from

    assert _score_from("faithfulness", {"claims": [{"claim": "a", "supported": True},
                                                    {"claim": "b", "supported": False}]}) == 0.5
    assert _score_from("faithfulness", {"claims": [], "reason": "declined"}) == 1.0
    assert _score_from("faithfulness", {"score": 0.8}) == 0.8  # older reply shape still works


def test_out_of_range_scores_are_rescaled():
    from ragops.eval.judges import _score_from

    assert _score_from("answer_relevance", {"score": 8}) == 0.8
    assert _score_from("answer_relevance", {"score": 90}) == 0.9


def test_faithfulness_numbers_the_answer_statements_and_reads_lenient_verdicts(monkeypatch):
    from ragops.eval.judges import LLMJudge
    from ragops.eval.types import ContextChunk, RagSample

    sent = []

    class R:
        status_code = 200
        text = ""

        def json(self):  # Sarvam-style: think block, "yes" instead of true
            return {"choices": [{"message": {"content": '<think>ok</think>{"reason": "both in context", "verdicts": '
                                                        '[{"statement": 1, "supported": "yes"}, {"statement": 2, "supported": true}]}'}}]}

    j = LLMJudge("x", base_url="http://127.0.0.1:9")
    monkeypatch.setattr(j, "_post", lambda body: sent.append(body) or R())
    s = RagSample("Mobile app?", "No native app exists so far. Phones can open the site in a browser.",  # paraphrase
                  [ContextChunk("There is no mobile app yet. The web app works in phone browsers. An app is planned.")])
    r = j.score("faithfulness", s)
    user = sent[0]["messages"][1]["content"]
    assert "1. No native app exists so far." in user and "2. Phones can open" in user
    assert r.score == 1.0


def test_skipped_statement_counts_as_unverified():
    from ragops.eval.judges import _score_from

    assert _score_from("faithfulness", {"verdicts": [{"supported": True}]}, n_statements=2) == 0.5


def test_answer_matching_reference_needs_no_model_call():
    from ragops.eval.judges import LLMJudge
    from ragops.eval.types import RagSample

    j = LLMJudge("x", base_url="http://127.0.0.1:9")  # unreachable: must not be called
    r = j.score("answer_correctness", RagSample("Free plan rate limit?", "20 requests per second", [],
                                                expected_answer="20 requests per second."))
    assert r.score == 1.0 and "heuristic" not in r.reasoning


def test_typographic_apostrophe_counts_as_abstention():
    from ragops.textutil import is_abstention

    assert is_abstention("I don’t know.")


def test_extractive_statements_are_faithful_without_a_model_call():
    from ragops.eval.judges import LLMJudge
    from ragops.eval.types import ContextChunk, RagSample
    from ragops.textutil import extractive_support

    ctx = ["Monthly plans are not refundable, but you can cancel at any time. A reset link valid for 15 minutes is sent."]
    j = LLMJudge("x", base_url="http://127.0.0.1:9")  # unreachable: must not be called
    r = j.score("faithfulness", RagSample("Refunds?", "Monthly plans are not refundable.", [ContextChunk(ctx[0])]))
    assert r.score == 1.0 and "word for word" in r.reasoning
    assert not extractive_support("Monthly plans are refundable.", ctx)        # negation dropped
    assert not extractive_support("A reset link valid for 30 minutes is sent.", ctx)  # number changed


def test_answer_containing_the_reference_is_correct_without_a_model_call():
    from ragops.eval.judges import LLMJudge
    from ragops.eval.types import RagSample

    j = LLMJudge("x", base_url="http://127.0.0.1:9")  # unreachable: must not be called
    s = RagSample("When are invoices issued?", "Acme bills monthly in arrears on the first day of each month. "
                  "Invoices are emailed.", [], expected_answer="Monthly in arrears on the first day of each month.")
    assert j.score("answer_correctness", s).score == 1.0


def test_score_from_tolerates_other_reply_shapes():
    import pytest

    from ragops.eval.judges import _score_from

    assert _score_from("answer_relevance", {"reason": "x", "rating": 8}) == 0.8
    assert _score_from("context_relevance", {"verdicts": [{"supported": True}, {"supported": False}]}) == 0.5
    with pytest.raises(ValueError):
        _score_from("answer_relevance", {"reason": "only a reason"})
