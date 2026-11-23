import json

import httpx

from ragops.eval.judges import LLMJudge, evaluate, get_judge
from ragops.eval.types import ContextChunk, RagSample

S = RagSample("How long are audit logs kept?", "1 year. [data-retention]",
              [ContextChunk("Audit logs are retained for 1 year on Enterprise.", "data-retention", "data-retention#1")])


def _judge(handler) -> LLMJudge:
    j = LLMJudge("gpt-4o-mini", api_key="sk-test")
    j.client = httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler))
    return j


def test_llm_judge_parses_json_and_costs_tokens():
    seen = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"score": 0.8, "reason": "ok"}'}}],
                                         "usage": {"total_tokens": 1000}})

    res = evaluate(S, _judge(handler), ["faithfulness", "answer_relevance", "citation_correctness"])
    assert res["faithfulness"].score == 0.8 and res["faithfulness"].judge == "llm:gpt-4o-mini"
    assert abs(res["faithfulness"].cost_usd - 0.0006) < 1e-9
    assert res["citation_correctness"].judge == "heuristic"  # deterministic metrics stay free
    assert seen[0]["temperature"] == 0 and seen[0]["response_format"] == {"type": "json_object"}
    assert len(seen) == 2


def test_llm_judge_falls_back_on_errors():
    res = _judge(lambda req: httpx.Response(500)).score("faithfulness", S)
    assert res.score is not None and "heuristic fallback" in res.reasoning


def test_get_judge_kinds():
    assert get_judge("heuristic").name == "heuristic"
    assert get_judge("ollama", "llama3.1").client.base_url.host == "localhost"


def test_citations_with_punctuation_are_not_claims():
    from ragops.eval.judges import HeuristicJudge

    s = RagSample("Which satellite?", "Sky Digital used the Astra 2A satellite. [Sky_(United_Kingdom)] [Jacksonville,_Florida]",
                  [ContextChunk("Sky Digital used the Astra 2A satellite.", "Sky_(United_Kingdom)", "Sky#1")])
    j = HeuristicJudge()
    assert j.score("faithfulness", s).score == 1.0
    assert j.score("citation_correctness", s).score == 0.5  # one of the two citations points at the context
