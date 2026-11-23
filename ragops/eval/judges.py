"""Judges score a RagSample on each metric.

HeuristicJudge  - deterministic, free, offline. Default; ideal for CI and the demo.
LLMJudge        - any OpenAI-compatible chat endpoint (OpenAI, Azure, vLLM, Ollama).
RagasJudge      - delegates the four core metrics to the Ragas library when installed.

Deterministic metrics (citation correctness, abstention quality, context consistency) are always
computed by the heuristic implementation, so every judge reports the full metric set.
"""

from __future__ import annotations

import itertools
import json
import os
import re
from typing import Protocol

from ragops.eval.types import LLM_METRICS, METRICS, MetricResult, RagSample
from ragops.textutil import content_tokens, coverage, extractive_support, is_abstention, jaccard, numbers, sentences

GAP_SIMILARITY = 0.30  # below this best-corpus relevance the corpus does not cover the query. Must equal
# Thresholds.gap_relevance in ragops.diagnosis, so "knowledge gap" and "correct refusal" always agree.
# a citation is any short bracketed reference: [doc-1], [Sky_(United_Kingdom)], [source: guides/setup.md#2]
_CITE = re.compile(r"\[(?:source:\s*)?([^\[\]\n]{1,200}?)\]")


class Judge(Protocol):
    name: str
    cost_per_call: float

    def score(self, metric: str, sample: RagSample) -> MetricResult: ...


def evaluate(sample: RagSample, judge: "Judge | None" = None, metrics: list[str] | None = None) -> dict[str, MetricResult]:
    judge = judge or HeuristicJudge()
    out: dict[str, MetricResult] = {}
    for m in metrics or METRICS:
        out[m] = judge.score(m, sample)
    return out


class HeuristicJudge:
    name = "heuristic"
    cost_per_call = 0.0

    def score(self, metric: str, s: RagSample) -> MetricResult:
        fn = getattr(self, f"_{metric}", None)
        if fn is None:
            raise ValueError(f"unknown metric {metric}")
        score, why = fn(s)
        return MetricResult(metric, None if score is None else round(float(score), 4), why, 0.0, self.name)

    # --- core metrics -------------------------------------------------------------------------
    def _faithfulness(self, s: RagSample):
        if not s.answer:
            return None, "no answer"
        if is_abstention(s.answer):
            return 1.0, "answer abstains; makes no unsupported claims"
        ctx_text = " ".join(c.text for c in s.contexts)
        ctx_tok, ctx_num = content_tokens(ctx_text), numbers(ctx_text)
        claims = [x for x in sentences(_CITE.sub("", s.answer)) if len(content_tokens(x)) >= 2]
        if not claims:
            return 1.0, "no factual claims"
        unsupported = []
        for c in claims:
            ok = coverage(content_tokens(c), ctx_tok) >= 0.6 and numbers(c) <= ctx_num
            if not ok:
                unsupported.append(c)
        score = 1 - len(unsupported) / len(claims)
        why = f"{len(claims) - len(unsupported)}/{len(claims)} claims supported by context"
        if unsupported:
            why += f"; first unsupported: {unsupported[0][:120]!r}"
        return score, why

    def _answer_relevance(self, s: RagSample):
        if not s.answer:
            return None, "no answer"
        if is_abstention(s.answer):
            return 0.0, "answer abstains"
        cov = coverage(content_tokens(s.query), content_tokens(s.answer))
        return min(1.0, cov * 1.25), f"answer covers {cov:.0%} of the query's key terms"

    def _context_relevance(self, s: RagSample):
        if not s.contexts:
            return 0.0, "no context retrieved"
        q = content_tokens(s.query)
        rel = [coverage(q, content_tokens(c.text)) >= 0.5 for c in s.contexts]
        # rank-aware average precision (like Ragas context_precision)
        hits, ap = 0, 0.0
        for i, r in enumerate(rel, start=1):
            if r:
                hits += 1
                ap += hits / i
        score = ap / hits if hits else 0.0
        return score, f"{hits}/{len(rel)} context chunks relevant (rank-weighted precision)"

    def _context_recall(self, s: RagSample):
        # The reference answer is the stronger signal: the right document can be in context while the passage
        # with the answer is not (chunk too small, wrong section). Fall back to document ids.
        if s.expected_answer and not is_abstention(s.expected_answer):
            ctx_text = " ".join(c.text for c in s.contexts)
            ctx, ctx_num = content_tokens(ctx_text), numbers(ctx_text)
            sents = sentences(s.expected_answer) or [s.expected_answer]
            sup = [x for x in sents if coverage(content_tokens(x), ctx) >= 0.6 and numbers(x) <= ctx_num]
            return len(sup) / len(sents), f"{len(sup)}/{len(sents)} reference statements attributable to context"
        if s.expected_doc_ids:
            got = {c.doc_id for c in s.contexts}
            hit = [d for d in s.expected_doc_ids if d in got]
            return len(hit) / len(s.expected_doc_ids), f"{len(hit)}/{len(s.expected_doc_ids)} expected docs in context"
        return None, "needs expected_answer or expected_doc_ids"

    def _answer_correctness(self, s: RagSample):
        """Does the answer say what the reference answer says? Key-term recall, with every number in the
        reference required. A reference that is itself a refusal means the bot should abstain."""
        if not s.expected_answer:
            return None, "needs expected_answer"
        if not s.answer:
            return 0.0, "no answer"
        abstained, should = is_abstention(s.answer), is_abstention(s.expected_answer)
        if should:
            return (1.0, "correctly said it does not know") if abstained else (0.0, "should have said it does not know")
        if abstained:
            return 0.0, "refused although a reference answer exists"
        key, ans = content_tokens(s.expected_answer), content_tokens(s.answer)
        rec = coverage(key, ans)
        missing_nums = numbers(s.expected_answer) - numbers(s.answer)
        score = rec * (0.5 if missing_nums else 1.0)
        why = f"answer contains {rec:.0%} of the reference's key terms"
        if missing_nums:
            why += f"; missing figures: {', '.join(sorted(missing_nums))}"
        return score, why

    # --- deterministic metrics ----------------------------------------------------------------
    def _citation_correctness(self, s: RagSample):
        cited = _CITE.findall(s.answer or "")
        if not cited:
            return None, "answer has no citations"
        ids = {c.doc_id for c in s.contexts} | {c.chunk_id for c in s.contexts}
        ok = [c for c in cited if c in ids]
        return len(ok) / len(cited), f"{len(ok)}/{len(cited)} citations point at context documents"

    def _abstention_quality(self, s: RagSample):
        if not s.answer:
            return None, "no answer"
        abstained = is_abstention(s.answer)
        if s.expected_answer:
            # A labelled test case says whether the question is answerable; that beats guessing from word overlap,
            # which misreads paraphrased questions ("initial email reply timeframe" vs "first response").
            should_abstain = is_abstention(s.expected_answer)
            if should_abstain:
                return (1.0, "correctly declined a question the documents do not cover") if abstained else (
                    0.0, "answered a question the test marks as not covered (should have declined)")
            return (0.0, "declined a question the test expects to be answered") if abstained else (
                1.0, "answered a question the test expects to be answered")
        covered = None if s.corpus_max_similarity is None else s.corpus_max_similarity >= GAP_SIMILARITY
        if abstained:
            if covered is False:
                return 1.0, "correctly abstained: corpus does not cover this query"
            if covered is True:
                return 0.0, "abstained although the corpus covers this query"
            return 0.5, "abstained; corpus coverage unknown"
        if covered is False:
            return 0.0, "answered although no document covers the query (should have abstained)"
        return 1.0, "answered a covered query"

    def _context_consistency(self, s: RagSample):
        ctx = s.contexts
        for a, b in itertools.combinations(ctx, 2):
            if a.doc_id and a.doc_id == b.doc_id and a.doc_version and b.doc_version and a.doc_version != b.doc_version:
                return 0.0, f"two versions of {a.doc_id} in context (v{a.doc_version} vs v{b.doc_version})"
            ta, tb = content_tokens(a.text), content_tokens(b.text)
            na, nb = numbers(a.text), numbers(b.text)
            if jaccard(ta, tb) >= 0.45 and na and nb and na != nb and not (na & nb):
                return 0.0, f"chunks {a.chunk_id} and {b.chunk_id} state different figures about the same topic"
        return 1.0, "no contradictions detected between context chunks"


# reason before score: a judge that explains first gives a score that matches its explanation
_FORMAT = ' Reply only with JSON: {"reason": "<one short sentence>", "score": <number from 0 to 1>}'
_FORMATS = {
    # per-claim verdicts are counted by us, so the score cannot contradict the judge's own findings
    "faithfulness": ' Reply only with JSON: {"reason": "<one short sentence>", "verdicts": '
                    '[{"statement": <number>, "supported": true or false}, ...one per statement]}',
}


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _truthy(v) -> bool:
    return v is True or v == 1 or str(v).strip().lower() in {"true", "yes", "supported", "1", "y"}


def _score_from(metric: str, parsed: dict, n_statements: int | None = None) -> float:
    items = parsed.get("verdicts", parsed.get("claims"))
    if metric == "faithfulness" and isinstance(items, list):
        verdicts = [(c.get("supported", c.get("verdict")) if isinstance(c, dict) else c) for c in items]
        if not verdicts:
            return 1.0  # nothing claimed (abstention)
        total = max(len(verdicts), n_statements or 0)  # a statement the judge skipped counts as unverified
        return sum(1 for v in verdicts if _truthy(v)) / total
    raw = next((parsed[k] for k in ("score", "rating", "value", metric, "relevance", "result") if k in parsed), None)
    if raw is None and isinstance(items, list) and items:
        # the judge answered in the per-statement format for a metric that wants one score: count the verdicts
        vs = [(c.get("supported", c.get("verdict")) if isinstance(c, dict) else c) for c in items]
        return sum(1 for v in vs if _truthy(v)) / len(vs)
    if raw is None:
        raise ValueError(f"judge reply has no score (keys: {', '.join(sorted(parsed)) or 'none'})")
    score = float(raw)
    if 1 < score <= 10:  # some judges answer on a 0-10 scale despite the instructions
        score /= 10
    elif 10 < score <= 100:
        score /= 100
    return max(0.0, min(1.0, score))


_NEEDS_REFERENCE = {"context_recall", "answer_correctness"}

_PROMPTS = {
    "faithfulness": (
        "You verify RAG answers. The ANSWER STATEMENTS are numbered. For each statement decide whether the CONTEXT "
        "supports it (same meaning counts, exact wording is not needed). Judge only the statements, never the "
        "CONTEXT: information in the CONTEXT that the answer leaves out does not matter. A short fragment such as "
        "'30 days' is supported when the CONTEXT gives that fact for the QUESTION."
    ),
    "answer_relevance": (
        "Rate how directly the ANSWER addresses what the QUESTION asks, from 0 (off-topic or refusal) to 1 (answers "
        "exactly what was asked). A short answer that fully answers the question scores 1; do not lower the score for "
        "details the question did not ask for."
    ),
    "context_relevance": (
        "For each CONTEXT chunk (in rank order) decide whether it is useful to answer the QUESTION. Return the "
        "rank-weighted precision (average precision over relevant positions)."
    ),
    "context_recall": (
        "Split the REFERENCE answer into statements; score = fraction that can be attributed to the CONTEXT."
    ),
    "answer_correctness": (
        "Compare the ANSWER with the REFERENCE answer by meaning, not wording: paraphrases, synonyms and extra correct "
        "detail are fine. Score = fraction of the REFERENCE's key facts (numbers, names, conditions, steps) that the "
        "ANSWER states correctly; any fact that contradicts the REFERENCE lowers the score. If the REFERENCE says the "
        "information is not available ('I don't know'), score 1 when the ANSWER also declines and 0 when it answers."
    ),
}


def _error_message(r) -> str:
    try:
        err = r.json()
        if isinstance(err, list) and err:
            err = err[0]
        e = err.get("error", err)
        return str(e.get("message", e) if isinstance(e, dict) else e)[:300]
    except Exception:  # noqa: BLE001
        return r.text[:300]


def _parse_json_reply(text: str) -> dict:
    """Models without strict JSON mode wrap JSON in ``` fences or add a sentence; take the first {...} object."""
    t = re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S).strip()  # reasoning models (Sarvam, Qwen, DeepSeek)
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except json.JSONDecodeError:
                pass
        verdicts = re.findall(r'"supported"\s*:\s*"?(true|false|yes|no)', t, flags=re.I)
        if verdicts:  # faithfulness reply cut off or with unescaped quotes: count the verdicts we can read
            return {"verdicts": [{"supported": v.lower() in ("true", "yes")} for v in verdicts],
                    "reason": "(judge reply was not valid JSON; verdicts read from it)"}
        m = re.search(r'"score"\s*:\s*([0-9]*\.?[0-9]+)', t)  # broken JSON (unescaped quotes, cut-off reason)
        if m:
            return {"score": float(m.group(1)), "reason": "(judge reply was not valid JSON; score read from it)"}
        raise ValueError(f"judge reply is not JSON: {text[:120]!r}")


class LLMJudge:
    """OpenAI-compatible judge. Works with OpenAI, Azure OpenAI, vLLM and Ollama (/v1)."""

    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None,
                 cost_per_1k_tokens: float = 0.0006):
        import httpx

        self.model = model
        self.name = f"llm:{model}"
        self.cost_per_1k = cost_per_1k_tokens
        self.cost_per_call = cost_per_1k_tokens * 1.5  # rough estimate for budgeting
        self.client = httpx.Client(
            base_url=base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            # RAGOPS_JUDGE_API_KEY lets the judge use a different provider's key than your app (e.g. Gemini)
            headers={"Authorization": f"Bearer {api_key or os.environ.get('RAGOPS_JUDGE_API_KEY') or os.environ.get('OPENAI_API_KEY', 'ollama')}"},
            timeout=90,
        )
        self._fallback = HeuristicJudge()

    def _post(self, body: dict):
        """POST with patience for rate limits: free tiers (Gemini, Groq) answer 429 when a worker evaluates a burst
        of traces. Wait (Retry-After, else exponential back-off) instead of silently falling back to the heuristic."""
        import time

        tries = int(os.environ.get("RAGOPS_JUDGE_RETRIES", "6"))
        delay = 2.0
        for attempt in range(tries + 1):
            r = self.client.post("/chat/completions", json=body)
            if r.status_code not in (429, 500, 502, 503, 504) or attempt == tries:
                return r
            try:
                wait = float(r.headers.get("retry-after", "")) or delay
            except ValueError:
                wait = delay
            time.sleep(min(wait, 60.0))
            delay = min(delay * 2, 60.0)
        return r

    def score(self, metric: str, s: RagSample) -> MetricResult:
        if metric not in LLM_METRICS:
            return self._fallback.score(metric, s)
        if metric == "context_recall" and not (s.expected_answer or s.expected_doc_ids):
            return MetricResult(metric, None, "needs reference", 0.0, self.name)
        if metric == "answer_correctness" and not s.expected_answer:
            return MetricResult(metric, None, "needs expected_answer", 0.0, self.name)
        if metric == "answer_correctness" and s.answer and s.expected_answer and \
                f" {_norm(s.expected_answer)} " in f" {_norm(s.answer)} ":
            # the answer contains the whole reference word for word (punctuation aside), possibly with more detail
            # from the documents: nothing for a model to judge
            return MetricResult(metric, 1.0, "answer matches the reference", 0.0, self.name)
        if metric == "answer_correctness" and is_abstention(s.expected_answer):
            # "I don't know" vs "I don't know" needs no model, and weaker judges get it wrong
            ok = is_abstention(s.answer)
            return MetricResult(metric, 1.0 if ok else 0.0, "reference declines; answer " + ("declines too" if ok else
                                "answers anyway"), 0.0, self.name)
        ctx = "\n\n".join(f"[{i + 1}] ({c.doc_id}) {c.text}" for i, c in enumerate(s.contexts))
        statements = sentences(s.answer) or [s.answer or ""]
        if metric == "faithfulness":
            # we number the statements ourselves so the judge cannot drift into checking the CONTEXT instead
            if is_abstention(s.answer):
                return MetricResult(metric, 1.0, "answer declines; nothing to verify", 0.0, self.name)
            chunk_texts = [c.text for c in s.contexts]
            if chunk_texts and all(extractive_support(t, chunk_texts) for t in statements):
                return MetricResult(metric, 1.0, "every statement is taken from the context nearly word for word",
                                    0.0, self.name)
            listed = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(statements))
            user = f"QUESTION:\n{s.query}\n\nCONTEXT:\n{ctx or '(none)'}\n\nANSWER STATEMENTS:\n{listed}"
        else:
            user = f"QUESTION:\n{s.query}\n\nCONTEXT:\n{ctx or '(none)'}\n\nANSWER:\n{s.answer or ''}"
        if s.expected_answer and metric in _NEEDS_REFERENCE:  # a reference would bias faithfulness and relevance
            user += f"\n\nREFERENCE:\n{s.expected_answer}"
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _PROMPTS[metric] + _FORMATS.get(metric, _FORMAT)},
                {"role": "user", "content": user},
            ],
        }
        if os.environ.get("RAGOPS_JUDGE_MAX_TOKENS"):  # reasoning models need room to think before the JSON
            body["max_tokens"] = int(os.environ["RAGOPS_JUDGE_MAX_TOKENS"])
        try:
            r = self._post(body)
            if r.status_code == 400 and "response_format" in r.text:  # provider without JSON mode: ask in the prompt only
                body.pop("response_format")
                r = self._post(body)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code} from {self.client.base_url}: {_error_message(r)}")
            data = r.json()
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
            if not content.strip():
                raise RuntimeError(f"empty reply (finish_reason={choice.get('finish_reason')}); "
                                   "for reasoning models set RAGOPS_JUDGE_MAX_TOKENS=4096")
            parsed = _parse_json_reply(content)
            usage = data.get("usage", {})
            cost = usage.get("total_tokens", 1500) / 1000 * self.cost_per_1k
            score = _score_from(metric, parsed, len(statements) if metric == "faithfulness" else None)
            self.model_replies = getattr(self, "model_replies", 0) + 1  # read by `ragops judge check`
            return MetricResult(metric, score, str(parsed.get("reason", ""))[:500], cost, self.name)
        except Exception as e:  # never lose an eval because the judge hiccuped
            res = self._fallback.score(metric, s)
            res.reasoning = f"[llm judge failed: {type(e).__name__}: {str(e)[:300]}; heuristic fallback] {res.reasoning}"
            return res


class RagasJudge:
    """Ragas 0.4 metrics, run against any OpenAI-compatible endpoint (OpenAI, Azure, vLLM, Ollama).

    faithfulness -> Faithfulness, answer_relevance -> AnswerRelevancy, context_relevance ->
    ContextPrecisionWithoutReference, context_recall -> ContextRecall (needs a reference answer). Deterministic
    metrics (citations, abstention, consistency) and anything Ragas cannot score fall back to the heuristic judge.
    """

    name = "ragas"
    cost_per_call = 0.002

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None,
                 embedding_model: str | None = None):
        import os

        from openai import AsyncOpenAI
        from ragas.embeddings import OpenAIEmbeddings
        from ragas.llms import llm_factory

        from ragops.config import get_settings

        st = get_settings()
        client = AsyncOpenAI(base_url=base_url or st.judge_base_url or None,
                             api_key=api_key or os.environ.get("OPENAI_API_KEY") or "not-needed-for-local-models")
        self.model = model or st.judge_model
        self.llm = llm_factory(self.model, client=client)
        self.embeddings = OpenAIEmbeddings(client=client, model=embedding_model or os.environ.get(
            "RAGOPS_RAGAS_EMBEDDING_MODEL", "text-embedding-3-small"))
        self._fallback = HeuristicJudge()
        self._metrics: dict = {}

    def _metric(self, metric: str):
        if metric not in self._metrics:
            from ragas.metrics.collections import (
                AnswerRelevancy,
                ContextPrecisionWithoutReference,
                ContextRecall,
                Faithfulness,
            )

            self._metrics[metric] = {
                "faithfulness": lambda: Faithfulness(llm=self.llm),
                "answer_relevance": lambda: AnswerRelevancy(llm=self.llm, embeddings=self.embeddings),
                "context_relevance": lambda: ContextPrecisionWithoutReference(llm=self.llm),
                "context_recall": lambda: ContextRecall(llm=self.llm),
            }[metric]()
        return self._metrics[metric]

    async def _ascore(self, metric: str, s: RagSample) -> float:
        m = self._metric(metric)
        ctx = [c.text for c in s.contexts] or [""]
        if metric == "faithfulness":
            r = await m.ascore(user_input=s.query, response=s.answer or "", retrieved_contexts=ctx)
        elif metric == "answer_relevance":
            r = await m.ascore(user_input=s.query, response=s.answer or "")
        elif metric == "context_relevance":
            r = await m.ascore(user_input=s.query, response=s.answer or "", retrieved_contexts=ctx)
        else:
            r = await m.ascore(user_input=s.query, retrieved_contexts=ctx, reference=s.expected_answer or "")
        return float(r.value)

    def score(self, metric: str, s: RagSample) -> MetricResult:
        ragas_metrics = {"faithfulness", "answer_relevance", "context_relevance", "context_recall"}
        if metric not in ragas_metrics or (metric == "context_recall" and not s.expected_answer):
            return self._fallback.score(metric, s)
        if metric in ("faithfulness", "answer_relevance") and (not s.answer or is_abstention(s.answer)):
            return self._fallback.score(metric, s)  # abstentions are scored by the deterministic rules
        try:
            val = _run_async(self._ascore(metric, s))
        except Exception as e:  # noqa: BLE001 - never lose an evaluation because the judge failed
            res = self._fallback.score(metric, s)
            res.reasoning = f"heuristic fallback (ragas error: {type(e).__name__}: {str(e)[:120]})"
            return res
        if val != val:  # NaN from Ragas (e.g. no statements extracted)
            return self._fallback.score(metric, s)
        return MetricResult(metric, round(max(0.0, min(1.0, val)), 4), f"ragas {metric} ({self.model})",
                            self.cost_per_call, self.name)


def _run_async(coro):
    """Run a coroutine from sync code, also when called inside a running event loop (e.g. an async endpoint)."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


def get_judge(kind: str | None = None, model: str | None = None, base_url: str | None = None) -> Judge:
    from ragops.config import get_settings

    st = get_settings()
    kind = kind or st.judge
    if kind == "openai":
        return LLMJudge(model or st.judge_model, base_url or st.judge_base_url)
    if kind == "ollama":
        return LLMJudge(model or st.judge_model, base_url or st.judge_base_url or "http://localhost:11434/v1",
                        cost_per_1k_tokens=0.0)
    if kind == "ragas":
        return RagasJudge(model, base_url)
    return HeuristicJudge()
