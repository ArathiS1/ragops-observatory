"""A deterministic OpenAI-compatible server for offline CI. NOT a language model.

It answers the structured prompts that LLM judges send (Ragas 0.4 schemas, the RAGOps LLM judge) with simple lexical
rules, and serves embeddings from the hashing embedder. That lets the *real* Ragas library and the RAGOps LLM-judge
code path (HTTP client, JSON mode, schema parsing, retries, scoring) run end to end in CI without an API key.
Scores are only as smart as the lexical rules; use a real model in production.

    python -m ragops.testing.stub_llm --port 8301
    RAGOPS_JUDGE=ragas RAGOPS_JUDGE_BASE_URL=http://localhost:8301/v1 ragops test ...
"""

from __future__ import annotations

import json
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ragops.textutil import content_tokens, coverage, is_abstention, sentences

app = FastAPI(title="RAGOps deterministic stub LLM (CI only)")
CALLS = {"chat": 0, "embeddings": 0}


def _input_json(text: str) -> dict:
    """The JSON object after the last 'input:' and before 'Output:' (the Ragas prompt layout)."""
    tail = text.rsplit("input:", 1)[-1]
    tail = tail.rsplit("Output:", 1)[0].strip()
    try:
        return json.loads(tail)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", tail, re.S)
        return json.loads(m.group(0)) if m else {}


def _supported(stmt: str, context: str) -> int:
    return int(coverage(content_tokens(stmt), content_tokens(context)) >= 0.6)


def _answer(schema_title: str, data: dict) -> dict | None:
    ctx = data.get("context") or " ".join(data.get("retrieved_contexts") or [])
    if schema_title == "StatementGeneratorOutput":
        return {"statements": sentences(data.get("answer") or data.get("response") or "") or []}
    if schema_title == "NLIStatementOutput":
        return {"statements": [{"statement": s, "reason": "lexical support check",
                                "verdict": _supported(s, ctx)} for s in data.get("statements", [])]}
    if schema_title == "AnswerRelevanceOutput":
        resp = data.get("response") or ""
        return {"question": resp, "noncommittal": int(is_abstention(resp))}
    if schema_title == "ContextPrecisionOutput":
        q = content_tokens(data.get("question") or "")
        return {"reason": "lexical relevance check", "verdict": int(coverage(q, content_tokens(ctx)) >= 0.3)}
    if schema_title == "ContextRecallOutput":
        return {"classifications": [{"statement": s, "reason": "lexical attribution check",
                                     "attributed": _supported(s, ctx)} for s in sentences(data.get("answer") or "")]}
    return None


def _ragops_judge(system: str, user: str) -> dict:
    from ragops.eval.judges import HeuristicJudge
    from ragops.eval.types import ContextChunk, RagSample

    def part(name: str) -> str:
        m = re.search(rf"{name}:\n(.*?)(?:\n\n[A-Z][A-Z ]*:\n|\Z)", user, re.S)
        return m.group(1).strip() if m else ""

    ctx = [ContextChunk(re.sub(r"^\[\d+\] \([^)]*\) ", "", c)) for c in part("CONTEXT").split("\n\n") if c and c != "(none)"]
    answer = part("ANSWER") or " ".join(re.findall(r"^\d+\. (.*)$", part("ANSWER STATEMENTS"), re.M))
    sample = RagSample(part("QUESTION"), answer, ctx, expected_answer=part("REFERENCE") or None)
    metric = next((m for m, key in [("faithfulness", "verify RAG answers"), ("answer_relevance", "the QUESTION asks"),
                                    ("context_relevance", "CONTEXT chunk"), ("context_recall", "REFERENCE")]
                   if key in system), "faithfulness")
    r = HeuristicJudge().score(metric, sample)
    if '"verdicts"' in system:  # faithfulness: one verdict per numbered answer statement
        stmts = re.findall(r"^\d+\. (.*)$", part("ANSWER STATEMENTS"), re.M) or [""]
        verdicts = []
        for i, st in enumerate(stmts):
            one = HeuristicJudge().score("faithfulness", RagSample(sample.query, st, ctx)).score or 0.0
            verdicts.append({"statement": i + 1, "supported": one >= 0.5})
        return {"reason": f"stub lexical {metric}", "verdicts": verdicts}
    return {"score": 0.0 if r.score is None else r.score, "reason": f"stub lexical {metric}"}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    CALLS["chat"] += 1
    system = body["messages"][0]["content"]
    user = body["messages"][-1]["content"]
    titles = re.findall(r'"title":\s*"(\w+Output)"', system + user)
    out = None
    if titles:
        out = _answer(titles[-1], _input_json(user))
    elif '"reason"' in system and "QUESTION:" in user:  # the RAGOps LLM judge prompt
        out = _ragops_judge(system, user)
    if out is None:
        return JSONResponse({"error": {"message": "stub LLM does not know this prompt"}}, status_code=400)
    content = json.dumps(out)
    return {"id": f"stub-{CALLS['chat']}", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", "stub"),
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": len(content) // 4,
                      "total_tokens": (len(user) + len(content)) // 4}}


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    from ragops.embeddings import HashingEmbedder

    body = await req.json()
    CALLS["embeddings"] += 1
    inp = body["input"] if isinstance(body["input"], list) else [body["input"]]
    vecs = HashingEmbedder().embed([str(x) for x in inp])
    return {"object": "list", "model": body.get("model", "stub"),
            "data": [{"object": "embedding", "index": i, "embedding": [float(x) for x in v]} for i, v in enumerate(vecs)],
            "usage": {"prompt_tokens": 0, "total_tokens": 0}}


if __name__ == "__main__":  # pragma: no cover
    import sys

    import uvicorn

    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8301
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
