"""`ragops judge check`: verify a real judge model (OpenAI, Azure, vLLM, Ollama, Ragas) in about a minute.

Twelve hand-labelled RAG answers, six right and six wrong in different ways (unsupported claim, wrong number,
off-topic, contradicted by the context, wrong context, partial), are sent to the configured judge. It reports how
often the judge agrees with the labels, latency and cost, and whether it is fit to gate releases.
"""

from __future__ import annotations

import time

from ragops.eval.types import ContextChunk, RagSample

CTX = {
    "refund": "Annual plans can be refunded within 30 days of purchase. Monthly plans are not refundable.",
    "trash": "Deleted files stay in the trash for 30 days. Any editor can restore them during that time.",
    "rate": "The REST API allows 600 requests per minute per token on the Pro plan and 60 on the Free plan.",
    "backup": "Backups run every 6 hours and are kept for 35 days in two regions.",
    "sso": "SAML single sign-on with Okta or Azure AD is available on the Enterprise plan only.",
    "export": "Workspace owners can export all data as JSON from Settings, Data, Export.",
}
# (question, context key, answer, metric, label: True = the answer is good on this metric)
CASES = [
    ("Can I get a refund on an annual plan?", "refund", "Yes, annual plans can be refunded within 30 days of purchase.", "faithfulness", True),
    ("Can I get a refund on an annual plan?", "refund", "Yes, annual plans can be refunded within 90 days, no questions asked.", "faithfulness", False),
    ("How long do deleted files stay in the trash?", "trash", "Deleted files stay in the trash for 30 days, and editors can restore them.", "faithfulness", True),
    ("How long do deleted files stay in the trash?", "trash", "Deleted files are removed immediately and cannot be restored.", "faithfulness", False),
    ("What is the API rate limit on Pro?", "rate", "600 requests per minute per token.", "answer_relevance", True),
    ("What is the API rate limit on Pro?", "rate", "Our API is built on REST and uses JSON for all responses.", "answer_relevance", False),
    ("How often are backups taken?", "backup", "Backups run every 6 hours.", "answer_relevance", True),
    ("How often are backups taken?", "backup", "Please contact support for more information.", "answer_relevance", False),
    ("Which plan includes SAML SSO?", "sso", "SSO is available on the Enterprise plan.", "context_relevance", True),
    ("Which plan includes SAML SSO?", "backup", "SSO is available on the Enterprise plan.", "context_relevance", False),
    ("How do I export my workspace data?", "export", "Owners can export everything as JSON from Settings.", "context_relevance", True),
    ("How do I export my workspace data?", "rate", "Owners can export everything as JSON from Settings.", "context_relevance", False),
]
THRESHOLDS = {"faithfulness": 0.7, "answer_relevance": 0.5, "context_relevance": 0.5}


def run_check(judge) -> dict:
    from ragops.eval.calibration import cohen_kappa

    rows, judged, labels, cost, t0 = [], [], [], 0.0, time.perf_counter()
    for q, ck, a, metric, good in CASES:
        s = RagSample(q, a, [ContextChunk(CTX[ck], ck, f"{ck}#0")])
        t = time.perf_counter()
        r = judge.score(metric, s)
        ok = r.score is not None and r.score >= THRESHOLDS[metric]
        rows.append({"metric": metric, "question": q, "answer": a, "label": "good" if good else "bad",
                     "score": r.score, "judge": r.judge, "agrees": ok == good,
                     "error": (r.reasoning.split("; heuristic fallback")[0].removeprefix("[llm judge failed: ")
                               if (r.reasoning or "").startswith("[llm judge failed") else None), "seconds": round(time.perf_counter() - t, 2)})
        judged.append(ok)
        labels.append(good)
        cost += r.cost_usd or 0.0
    agree = sum(r["agrees"] for r in rows) / len(rows)
    kappa = cohen_kappa(judged, labels)
    is_heuristic = getattr(judge, "name", "") == "heuristic"
    fell_back = 0 if is_heuristic else sum(1 for r in rows if r["judge"] == "heuristic")
    # some checks are settled without the model (exact copies, declines), so "not working" means: the model was
    # asked and never answered
    if fell_back and (fell_back == len(rows) or (not is_heuristic and getattr(judge, "model_replies", 0) == 0)):
        verdict = "not working: every call fell back to the heuristic judge (check the URL, model name and key)"
    elif kappa is not None and kappa >= 0.8:
        verdict = "reliable enough to gate releases"
    elif kappa is not None and kappa >= 0.6:
        verdict = "good enough for alerts and trends; review its failures before gating releases"
    else:
        verdict = "not reliable on these checks; try a stronger model"
    return {"judge": getattr(judge, "name", type(judge).__name__), "cases": len(rows), "agreement": round(agree, 3),
            "kappa": None if kappa is None else round(kappa, 3), "fell_back": fell_back,
            "first_error": next((x["error"] for x in rows if x.get("error")), None),
            "seconds": round(time.perf_counter() - t0, 1), "cost_usd": round(cost, 5), "verdict": verdict, "rows": rows}
