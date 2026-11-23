"""Seed a realistic 14-day production history so every dashboard page has something real to show.

Storyline ("Nimbus Cloud" support bot):

    day 0     corpus registered, config v1 (no reranker) in production
    day 3-6   a long-context experiment sends 15% of traffic through context_k=9 -> context overload
    day 7     config v2 ships a misconfigured reranker                         -> ranking failures spike
    day 8+    users start asking about the mobile app and the AI assistant      -> knowledge gaps, query drift
    day 9     the pricing page changes, but the serving index is not refreshed  -> stale knowledge
    day 10    config v3 fixes the reranker
    day 11    index refreshed, pricing answers recover
    always    ~4% hallucinated figures                                          -> generation failures

Everything goes through the same service layer the API uses, with timestamps backdated, then the durable worker
evaluates and diagnoses the traces.
"""

from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy import select

from ragops import services as svc
from ragops.db import init_db, session_scope
from ragops.demo import pipeline as pl
from ragops.demo.corpus import DOCS, GAP_QUESTIONS, PRICING_V2
from ragops.models import SLO, HumanLabel, Project, Trace, utcnow
from ragops.schemas import CorpusIn, DocumentIn, TraceIn

CONFIGS = {
    "v1-baseline": {"chunk_words": 45, "top_k": 6, "rerank": "none", "context_k": 3, "embedding": "hashing-384",
                    "llm": "extractive-demo", "prompt": "support-v1"},
    "v1-longctx-exp": {"chunk_words": 45, "top_k": 12, "rerank": "none", "context_k": 9, "shuffle_context": True,
                       "embedding": "hashing-384", "llm": "extractive-demo", "prompt": "support-v1"},
    "v2-reranker": {"chunk_words": 45, "top_k": 6, "rerank": "bad", "context_k": 2, "embedding": "hashing-384",
                    "llm": "extractive-demo", "prompt": "support-v1"},
    "v3-reranker-fix": {"chunk_words": 45, "top_k": 6, "rerank": "good", "context_k": 3, "embedding": "hashing-384",
                        "llm": "extractive-demo", "prompt": "support-v2"},
}

# What the production index serves after the day-11 refresh: used by the built-in demo endpoint and seeded test runs.
CURRENT_INDEX = {"serve_docs": {"pricing-plans": {"text": PRICING_V2, "version": 2}}}

PARAPHRASE = ["{q}", "{q}", "hi, {ql}", "quick question: {ql}", "{q} thanks", "can you tell me {ql}"]


def _phrase(rng: random.Random, q: str) -> str:
    t = rng.choice(PARAPHRASE)
    return t.format(q=q, ql=q[0].lower() + q[1:])


def _seed_test_runs(db, p) -> None:
    """Register the built-in demo RAG endpoint and store two test runs so 'Test your RAG' has something to show."""
    import os

    from ragops.run_service import store_uploaded_run, upsert_pipeline
    from ragops.runs import run_cases

    base = os.environ.get("RAGOPS_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    upsert_pipeline(db, p, "nimbus-demo-rag", {"kind": "http", "url": f"{base}/demo/rag/ask", "answer_path": "answer",
                                               "contexts_path": "contexts", "retrieved_path": "retrieved",
                                               "query_field": "query", "timeout": 30},
                    {**CONFIGS["v3-reranker-fix"], **CURRENT_INDEX},
                    "Built-in demo RAG (Nimbus help centre). Try 'Ask' and 'Start test'.")
    cases = [{"query": q, "expected_doc_ids": [d["doc_id"]], "expected_answer": None} for d in DOCS for q in d["questions"]]
    corpus = svc.load_corpus_view(db, p.id)
    for name, cfg in (("v2 reranker (shipped day 7)", CONFIGS["v2-reranker"]), ("v3 reranker fix", CONFIGS["v3-reranker-fix"])):
        rows = run_cases(lambda q, c: pl.run(q, c), cases, {**cfg, **CURRENT_INDEX}, corpus=corpus)
        store_uploaded_run(db, p, name, "nimbus-demo-rag", cfg, "help-centre questions", rows)


def seed_demo(project_name: str = "nimbus-support", days: int = 14, per_day: int = 45, seed: int = 42,
              reset: bool = False, verbose: bool = True) -> dict:
    """Create (or reset) a demo project with `days` of traffic. Returns a summary including the project API key."""
    init_db()
    rng = random.Random(seed)
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days)
    pricing_v1 = next(d["text"] for d in DOCS if d["doc_id"] == "pricing-plans")
    pricing_qs = next(d["questions"] for d in DOCS if d["doc_id"] == "pricing-plans")
    covered = [(q, d["doc_id"]) for d in DOCS for q in d["questions"]]

    with session_scope() as db:
        existing = svc.get_project_by_name(db, project_name)
        if existing is not None:
            if not reset:
                raise ValueError(f"project '{project_name}' already exists (use reset=True / --reset)")
            db.delete(existing)
            db.flush()
        p = svc.create_project(db, project_name, {
            "sample_rate": 0.8, "daily_eval_budget_usd": 5.0,
            "auto_golden": {"enabled": True, "min_confidence": 0.75, "golden_set": "production-failures"},
        })
        admin_key = svc.create_api_key(db, p.id, "demo-admin", "admin")
        ingest_key = svc.create_api_key(db, p.id, "demo-ingest", "ingest")
        svc.upsert_corpus(db, p, CorpusIn(documents=[DocumentIn(doc_id=d["doc_id"], title=d["title"], text=d["text"])
                                                     for d in DOCS], chunk_words=45), when=start)
        db.add_all([
            SLO(project_id=p.id, name="Grounded answers", metric="faithfulness", threshold=0.7, target=0.9, window_days=7),
            SLO(project_id=p.id, name="Answers address the question", metric="answer_relevance", threshold=0.5,
                target=0.8, window_days=7),
            SLO(project_id=p.id, name="Right context retrieved", metric="context_relevance", threshold=0.3,
                target=0.85, window_days=7),
        ])
        pid = p.id
        svc.audit(db, pid, "demo-seed", "project.create", {"name": project_name})

    n = 0
    for day in range(days):
        day0 = start + timedelta(days=day)
        if day == 9:  # pricing page changes in the CMS; registry gets v2 immediately
            with session_scope() as db:
                p = db.get(Project, pid)
                svc.upsert_corpus(db, p, CorpusIn(documents=[DocumentIn(doc_id="pricing-plans", title="Plans and pricing",
                                                                        text=PRICING_V2)], chunk_words=45),
                                  when=day0 + timedelta(hours=9))
        label = "v1-baseline" if day < 7 else "v2-reranker" if day < 10 else "v3-reranker-fix"
        gap_share = 0.03 if day < 8 else min(0.22, 0.06 + 0.04 * (day - 8))
        batch = []
        for i in range(per_day):
            ts = day0 + timedelta(minutes=rng.randint(0, 24 * 60 - 1))
            if ts > now:
                continue
            cfg_label = label
            if 3 <= day <= 6 and rng.random() < 0.15:
                cfg_label = "v1-longctx-exp"
            cfg = dict(CONFIGS[cfg_label])
            # serving index: pricing stays on v1 until the refresh on day 11
            if day >= 11 or (day == 10 and ts.hour >= 18):
                cfg["serve_docs"] = {"pricing-plans": {"text": PRICING_V2, "version": 2}}
            else:
                cfg["serve_docs"] = {"pricing-plans": {"text": pricing_v1, "version": 1}}
            cfg["hallucinate"] = 0.04
            cfg["guess_when_unsure"] = 0.5
            r = rng.random()
            if r < gap_share:
                q, gold = rng.choice(GAP_QUESTIONS), None
            elif 9 <= day <= 10 and r < gap_share + 0.12:
                q, gold = rng.choice(pricing_qs), "pricing-plans"
            else:
                q, gold = rng.choice(covered)
            query = _phrase(rng, q)
            out = pl.run(query, cfg, salt=f"{day}-{i}")
            failed_hint = gold is not None and gold not in {c["doc_id"] for c in out["contexts"]}
            fb = None
            u = rng.random()
            if failed_hint and u < 0.35:
                fb = -1.0
            elif not failed_hint and u < 0.08:
                fb = 1.0
            ptoks = 180 + 70 * len(out["contexts"])
            # per-stage timings, as the SDK's `with t.retrieve():` / `t.generate()` blocks record them
            steps = {"retrieve": round(max(20, rng.gauss(140, 35)), 1),
                     "rerank": round(max(10, rng.gauss(90, 25)), 1) if CONFIGS[cfg_label].get("rerank", "none") != "none" else None,
                     "generate": round(max(200, rng.gauss(600 + 90 * len(out["contexts"]), 110)), 1)}
            steps = {k: v for k, v in steps.items() if v is not None}
            batch.append(TraceIn(
                query=query, answer=out["answer"], retrieved=out["retrieved"], contexts=out["contexts"],
                model="gpt-4o-mini", latency_ms=round(sum(steps.values()) + max(5, rng.gauss(25, 8)), 1),
                expected_doc_ids=[gold] if gold else [],
                environment="staging" if rng.random() < 0.08 else "production",
                prompt_tokens=ptoks, completion_tokens=60, cost_usd=round(ptoks * 0.15e-6 + 60 * 0.6e-6, 6),
                session_id=f"s{rng.randint(1, 400)}", config_label=cfg_label,
                config={k: v for k, v in CONFIGS[cfg_label].items()}, user_feedback=fb, timestamp=ts,
                metadata={"channel": rng.choice(["web", "web", "slack", "email"]), "step_latency_ms": steps},
            ))
        with session_scope() as db:
            p = db.get(Project, pid)
            for t in batch:
                svc.ingest_trace(db, p, t)
                n += 1
        if verbose:
            print(f"  day {day + 1:>2}/{days}: {len(batch)} requests on {label}")

    from ragops.worker import drain

    processed = drain()

    # simulated human review of 60 evaluated traces, for judge calibration (humans mostly agree with ground truth)
    with session_scope() as db:
        rows = db.scalars(select(Trace).where(Trace.project_id == pid, Trace.eval_status == "done")
                          .order_by(Trace.created_at)).all()
        for t in rng.sample(rows, min(60, len(rows))):
            for ev in t.evaluations:
                if ev.metric in ("faithfulness", "answer_relevance") and ev.score is not None:
                    th = 0.7 if ev.metric == "faithfulness" else 0.5
                    agree = rng.random() < 0.88
                    passed = (ev.score >= th) if agree else not (ev.score >= th)
                    db.add(HumanLabel(project_id=pid, trace_id=t.id, metric=ev.metric, passed=passed,
                                      labeler=rng.choice(["priya@nimbus", "dan@nimbus"])))
        p = db.get(Project, pid)
        svc.check_slos_and_alert(db, p)
        ov = svc.overview(db, p, days)
        _seed_test_runs(db, p)
    return {"project": project_name, "project_id": pid, "traces": n, "jobs_processed": processed,
            "admin_key": admin_key, "ingest_key": ingest_key, "failure_rate": ov["failure_rate"],
            "root_causes": ov["root_causes"], "health": ov["health"], "generated_at": utcnow().isoformat()}
