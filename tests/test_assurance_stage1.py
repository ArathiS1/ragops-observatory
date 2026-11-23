"""Stage 1 assurance features: test generation, grounding guard, assurance report, /guard endpoint."""

from __future__ import annotations

import json

DOCS = [
    {"doc_id": "security.md", "text": "# Security\n\nTo reset your password open Settings, then Security, and click "
     "Reset password. A reset link valid for 15 minutes is sent to your email address.\n\nTwo-factor authentication "
     "can be enabled under Settings, then Security, then Two-factor."},
    {"doc_id": "billing.md", "text": "# Billing\n\nRefunds are available within 30 days of a charge for annual plans. "
     "Monthly plans are not refundable, but you can cancel at any time.\n\nAccepted payment methods are Visa, "
     "Mastercard, American Express and SEPA direct debit."},
]


# ------------------------------------------------------------------------------------------------ test generation
def test_offline_generation_is_verified_and_includes_unanswerable():
    from ragops.testgen import generate
    from ragops.textutil import is_abstention

    rep = generate(DOCS, n=10, unanswerable_share=0.3, offline=True)
    ans = [c for c in rep.cases if c["kind"] == "answerable"]
    neg = [c for c in rep.cases if c["kind"] == "unanswerable"]
    assert ans and neg
    for c in ans:  # every expected answer is text from its own document
        doc = next(d for d in DOCS if d["doc_id"] == c["expected_doc_ids"][0])
        assert c["expected_answer"].rstrip(".") in doc["text"].replace("\n", " ")
    assert all(is_abstention(c["expected_answer"]) and not c["expected_doc_ids"] for c in neg)


class FakeLLM:
    """OpenAI-compatible judge double: answers generation prompts with one good and one hallucinated case."""
    name = "llm:fake"
    model = "fake"

    def _post(self, body):
        prompt = body["messages"][0]["content"]

        class R:
            status_code = 200
            text = ""

            def __init__(self, content):
                self._c = content

            def json(self):
                return {"choices": [{"message": {"content": self._c}}]}

        if "would NOT answer" in prompt:
            return R(json.dumps({"questions": ["How long is the password reset link valid?",
                                               "Do you have an office in Tokyo?"]}))
        if "Security" in prompt:
            return R("<think>plan</think>" + json.dumps({"cases": [
                {"question": "How long does the reset link stay valid?", "answer": "15 minutes"},
                {"question": "Can I reset by phone?", "answer": "Yes, call 1800 555 0199"}]}))
        return R(json.dumps({"cases": [{"question": "Which cards can I pay with?",
                                        "answer": "Visa, Mastercard and American Express"}]}))


def test_llm_generation_drops_hallucinated_answers_and_answerable_negatives():
    from ragops.testgen import generate

    rep = generate(DOCS, n=10, unanswerable_share=0.5, judge=FakeLLM())
    qs = {c["query"] for c in rep.cases}
    assert "How long does the reset link stay valid?" in qs
    assert "Can I reset by phone?" not in qs                       # answer not in the passage
    assert rep.rejected.get("answer_not_in_passage") == 1
    assert "Do you have an office in Tokyo?" in qs
    assert "How long is the password reset link valid?" not in qs   # the docs DO answer it
    assert rep.rejected.get("unanswerable_question_is_answered") == 1


# ------------------------------------------------------------------------------------------------ guard
CTX = ["To reset your password open Settings, then Security, and click Reset password. A reset link valid for 15 "
       "minutes is sent to your email address."]


def test_guard_allows_grounded_and_rewrites_invented_answers():
    from ragops.guard import FALLBACK, check

    ok = check("A reset link valid for 15 minutes is sent to your email address.", CTX)
    assert ok.decision == "allow" and ok.supported_ratio == 1.0
    bad = check("The reset link is valid for 15 minutes. You can also reset it by calling 1800 555 0199.", CTX)
    assert bad.decision == "rewrite" and bad.answer == FALLBACK
    assert [s["supported"] for s in bad.statements] == [True, False]
    assert check("I don't know.", CTX).decision == "allow"
    assert check("The link is valid for 30 minutes.", CTX, policy="block").decision == "block"
    assert check("The link is valid for 30 minutes.", CTX, policy="flag").answer == "The link is valid for 30 minutes."


def test_guard_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("RAGOPS_DATABASE_URL", f"sqlite:///{tmp_path / 'g.db'}")
    from ragops.config import get_settings

    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    from ragops.server.app import app

    with TestClient(app) as c:
        h = {"Authorization": "Bearer ragops-admin-dev-key"}
        assert c.post("/v1/projects", json={"name": "guarded"}, headers=h).status_code in (200, 201)
        r = c.post("/v1/projects/guarded/guard", headers=h, json={
            "answer": "The link lasts 2 hours.", "contexts": [{"text": CTX[0]}], "policy": "rewrite"})
        assert r.status_code == 200, r.text
        assert r.json()["decision"] == "rewrite"
        assert c.post("/v1/projects/guarded/guard", json={"answer": "x", "contexts": []}).status_code == 401


# ------------------------------------------------------------------------------------------------ assurance
def _row(q, failed=False, label="ok", answer="ok", expected="ok", faith=1.0, error=None):
    return {"query": q, "answer": answer, "expected_answer": expected, "failed": failed, "label": label,
            "scores": {"faithfulness": faith}, "reasons": {"faithfulness": "every statement is taken from the context "
                                                           "nearly word for word"}, "hit": True, "error": error,
            "contexts": [{"doc_id": "a.md", "text": "t"}]}


def test_assurance_verdicts_and_fingerprint():
    from ragops.assurance import build, to_html, verify
    from ragops.runs import summarize

    good = [_row(f"q{i}") for i in range(24)] + [_row("Tokyo?", answer="I don't know.", expected="I don't know")]
    rep = build(good, summarize(good), {"judge": "llm:x", "judge_is_llm": True, "pipeline": "p"})
    assert rep["verdict"] == "Ready", rep["criteria"]
    assert verify(rep)
    rep["summary"]["pass_rate"] = 0.1  # tampering is detected
    assert not verify(rep)

    bad = good[:10] + [_row(f"f{i}", failed=True, label="knowledge_gap", answer="Yes we do", faith=0.2)
                       for i in range(6)] + [_row("Tokyo?", answer="Yes, in Shibuya.", expected="I don't know")]
    rep = build(bad, summarize(bad), {"judge": "heuristic", "judge_is_llm": False})
    assert rep["verdict"] == "Not ready"
    assert rep["root_causes"][0]["stage"] == "knowledge_gap" and len(rep["documents_to_write"]) == 6
    page = to_html(rep, title="Acme help bot", brand="Your Agency")
    assert "Not ready" in page and "Acme help bot" in page and "Your Agency" in page and "EU AI Act" in page


def test_assure_cli_from_json(tmp_path):
    from typer.testing import CliRunner

    from ragops.cli import app
    from ragops.runs import summarize

    rows = [_row(f"q{i}") for i in range(5)]
    src = tmp_path / "run.json"
    src.write_text(json.dumps({"summary": summarize(rows), "rows": rows, "judge": "llm:x", "judge_is_llm": True}))
    out = tmp_path / "a.html"
    r = CliRunner().invoke(app, ["assure", "--from-json", str(src), "--out", str(out), "-f", str(tmp_path / "none")])
    assert r.exit_code == 0, r.output
    assert "Verdict: Ready with conditions" in r.output  # only 5 questions
    v = CliRunner().invoke(app, ["assure", "--verify", str(out.with_suffix(".json"))])
    assert v.exit_code == 0 and "matches" in v.output


def test_generate_cli_offline(tmp_path):
    from typer.testing import CliRunner

    from ragops.cli import app

    d = tmp_path / "docs"
    d.mkdir()
    for doc in DOCS:
        (d / doc["doc_id"]).write_text(doc["text"], encoding="utf-8")
    out = tmp_path / "t.jsonl"
    r = CliRunner().invoke(app, ["generate", str(d), "--offline", "--out", str(out), "--n", "6"])
    assert r.exit_code == 0, r.output
    lines = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    assert lines and {"query", "expected_answer", "expected_doc_ids", "kind"} <= set(lines[0])


def test_extractive_support_splits_multi_sentence_chunks():
    """Regression: the chunk was lower-cased before sentence splitting, so a multi-sentence chunk was one span and
    a negation in ANOTHER sentence ("not refundable") vetoed a verbatim statement."""
    from ragops.textutil import extractive_support

    ctx = ["Refunds are available within 30 days of a charge for annual plans. Monthly plans are not refundable."]
    assert extractive_support("Refunds are available within 30 days of a charge for annual plans.", ctx)
    assert not extractive_support("Monthly plans are refundable.", ctx)


def test_correct_answer_to_paraphrased_question_is_not_a_failure():
    from ragops.diagnosis import Thresholds, is_failure

    scores = {"answer_correctness": 1.0, "faithfulness": 1.0, "answer_relevance": 0.36}
    failed, reasons = is_failure(scores, None, Thresholds())
    assert not failed, reasons
    # without a matching reference, low relevance still fails
    failed, _ = is_failure({"faithfulness": 1.0, "answer_relevance": 0.36}, None, Thresholds())
    assert failed


def test_abstention_uses_the_test_label_over_word_overlap():
    from ragops.eval.judges import HeuristicJudge
    from ragops.eval.types import RagSample

    j = HeuristicJudge()
    answered = RagSample(query="What is the initial email reply timeframe?", answer="Within 8 business hours.",
                         contexts=[], expected_answer="within 8 business hours", corpus_max_similarity=0.05)
    assert j.score("abstention_quality", answered).score == 1.0
    declined = RagSample(query="Do you support SSO?", answer="I don't know: the documents do not cover that.",
                         contexts=[], expected_answer="I don't know: the documents do not cover this.",
                         corpus_max_similarity=0.9)
    assert j.score("abstention_quality", declined).score == 1.0
    wrongly = RagSample(query="Office in Antarctica?", answer="Yes, in McMurdo.", contexts=[],
                        expected_answer="I don't know: the documents do not cover this.")
    assert j.score("abstention_quality", wrongly).score == 0.0
