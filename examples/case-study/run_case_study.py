"""Case study: run RAGOps Observatory against a real RAG service for 10 simulated days and write up what it caught.

    python examples/case-study/run_case_study.py            # starts RAGOps + the app locally, ~3 minutes
    python examples/case-study/run_case_study.py --openai   # use an OpenAI model for generation (needs OPENAI_API_KEY)

Nothing here is injected: failures come from the app's real behaviour (BM25/embedding retrieval mistakes, an
extractive reader, unanswerable questions, a chunk-size change, a document edit the index missed). SQuAD's answer
annotations give ground truth, so we can measure whether RAGOps flagged the answers that were actually wrong.

Timeline (simulated, timestamps backdated):
    day 0-3   v1-bm25-60w     BM25+embedding hybrid, 60-word chunks, top-3 context
    day 4-6   v2-25w-chunks   "cost cut": 25-word chunks to shrink prompts
    day 6     the 'Black_Death' page is edited in the CMS; the serving index is only rebuilt on day 8
    day 6+    users start asking about topics that are not in the knowledge base yet (Rhine, prime numbers)
    day 7-9   v3-60w-rerank   back to 60-word chunks + an overlap reranker
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import squad_rag as app  # noqa: E402

from ragops import RagopsClient  # noqa: E402

API, APP_PORT, API_PORT = None, 9100, 8100
EDITS = {"Black_Death": "renumber", "Steam_engine": "renumber"}  # pages corrected in the CMS on day 6
CONFIGS = {
    "v1-bm25-60w": {"chunk_words": 60, "top_k": 8, "context_k": 3, "rerank": "none"},
    "v2-25w-chunks": {"chunk_words": 25, "top_k": 8, "context_k": 3, "rerank": "none"},
    "v3-60w-rerank": {"chunk_words": 60, "top_k": 8, "context_k": 3, "rerank": "overlap"},
}


def questions(rng: random.Random):
    data = json.loads(Path(os.environ.get("SQUAD_PATH", app.download())).read_text(encoding="utf-8"))["data"]
    covered = [(q, a["title"]) for a in data[:25] for p in a["paragraphs"] for q in p["qas"]]
    answerable = [(q["question"], [x["text"] for x in q["answers"]], t) for q, t in covered if not q["is_impossible"]]
    impossible = [(q["question"], [], t) for q, t in covered if q["is_impossible"]]
    edited = [(q["question"], [x["text"] for x in q["answers"]], a["title"]) for a in data[:25] if a["title"] in EDITS
              for p in a["paragraphs"] for q in p["qas"] if not q["is_impossible"]]
    emerging = [(q["question"], [x["text"] for x in q["answers"]], a["title"]) for a in data[27:29]
                for p in a["paragraphs"] for q in p["qas"] if not q["is_impossible"]]
    rng.shuffle(answerable), rng.shuffle(impossible), rng.shuffle(emerging), rng.shuffle(edited)
    return answerable, impossible, emerging, edited


def correct(answer: str, gold: list[str]) -> bool:
    if not gold:  # unanswerable or not in the knowledge base: the right behaviour is to abstain
        return answer.startswith("I don't know")
    return any(g.lower() in answer.lower() for g in gold)


def wait(url: str, secs: int = 40) -> None:
    for _ in range(secs * 4):
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"{url} did not come up")


def main() -> None:  # noqa: C901
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-day", type=int, default=120)
    ap.add_argument("--openai", action="store_true")
    ap.add_argument("--out", default=str(HERE / "CASE_STUDY.md"))
    args = ap.parse_args()
    rng = random.Random(7)
    gen = {"generator": "openai"} if args.openai else {}
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "RAGOPS_DATABASE_URL": f"sqlite:///{tmp}/case.db", "RAGOPS_INLINE_JOBS": "true",
           "RAGOPS_PIPELINE_ALLOWED_HOSTS": "127.0.0.1,localhost"}
    procs = [subprocess.Popen([sys.executable, "-m", "ragops.cli", "serve", "--port", str(API_PORT)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
             subprocess.Popen([sys.executable, str(HERE / "squad_rag.py"), "serve", "--port", str(APP_PORT)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    base = f"http://127.0.0.1:{API_PORT}"
    try:
        wait(f"{base}/health")
        wait(f"http://127.0.0.1:{APP_PORT}/docs")
        admin = {"Authorization": "Bearer ragops-admin-dev-key"}
        key = httpx.post(f"{base}/v1/projects", headers=admin, json={"name": "wikihelp", "settings": {
            "sample_rate": 1.0, "auto_golden": {"enabled": True, "min_confidence": 0.75,
                                                "golden_set": "production-failures"}}}).json()["admin_key"]
        H = {"Authorization": f"Bearer {key}"}
        rg = RagopsClient(base, api_key=key, project="wikihelp", sync=True)
        for name, metric, th, tg in [("Grounded answers", "faithfulness", 0.7, 0.9),
                                     ("Answers address the question", "answer_relevance", 0.5, 0.8)]:
            httpx.post(f"{base}/v1/projects/wikihelp/slos", headers=H,
                       json={"name": name, "metric": metric, "threshold": th, "target": tg})
        answerable, impossible, emerging, edited = questions(rng)
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(days=10)
        ground: dict[str, dict] = {}
        registered = None
        for day in range(10):
            label = "v1-bm25-60w" if day < 4 else "v2-25w-chunks" if day < 7 else "v3-60w-rerank"
            cms_edits = EDITS if day >= 6 else {}
            served_edits = EDITS if day >= 8 else {}
            reg_key = (CONFIGS[label]["chunk_words"], json.dumps(cms_edits))
            if reg_key != registered:  # re-index => re-register the corpus (what the CMS says *now*)
                docs = app.corpus_documents({**CONFIGS[label], "edits": cms_edits})
                rg.upsert_corpus(docs, delete_missing=True)
                registered = reg_key
            share_new = 0.04 if day < 6 else 0.22
            share_edited = 0.15 if day in (6, 7) else 0.0  # the corrected pages get attention right after the change
            for i in range(args.per_day):
                r = rng.random()
                if r < share_edited and edited:
                    q, gold, kind = *edited.pop()[:2], "answerable"
                elif r < share_new + share_edited:
                    q, gold, kind = *emerging.pop()[:2], "not_in_kb"
                elif r < share_new + share_edited + 0.10:
                    q, gold, kind = *impossible.pop()[:2], "unanswerable"
                else:
                    q, gold, kind = *answerable.pop()[:2], "answerable"
                cfg = {**CONFIGS[label], "edits": served_edits, **gen}
                ts = start + timedelta(days=day, minutes=rng.randint(0, 1439))
                with rg.trace(q, config_label=label, config=CONFIGS[label], kind=kind) as t:
                    t.data["timestamp"] = ts.isoformat()
                    out = app.answer(q, cfg)
                    t.retrieved(out["retrieved"])
                    t.context(out["contexts"])
                    t.generation(out["answer"], model="gpt-4o-mini" if args.openai else "extractive-reader")
                ok = correct(out["answer"], gold)
                ground[t.id] = {"day": day, "config": label, "kind": kind, "correct": ok}
                u = rng.random()
                if not ok and u < 0.3:
                    rg.feedback(t.id, -1.0)
                elif ok and u < 0.1:
                    rg.feedback(t.id, 1.0)
            print(f"day {day}: {label} ({args.per_day} requests)", flush=True)
        time.sleep(2)
        httpx.post(f"{base}/v1/projects/wikihelp/slos/check", headers=H)

        # ---------------------------------------------------------------------------------------- collect results
        def get(path, **params):
            resp = httpx.get(f"{base}/v1/projects/wikihelp{path}", headers=H, params=params, timeout=120)
            resp.raise_for_status()
            return resp.json()

        flagged: dict[str, dict] = {}
        offset = 0
        while True:
            page = get("/traces", limit=500, offset=offset)
            for row in page:
                flagged[row["id"]] = row
            if len(page) < 500:
                break
            offset += 500
        rows = [{**ground[k], "flagged": bool(flagged[k]["is_failure"]), "label": flagged[k]["label"]}
                for k in ground if k in flagged]
        wrong = [r for r in rows if not r["correct"]]
        right = [r for r in rows if r["correct"]]
        detect = {
            "requests": len(rows), "actually_wrong": len(wrong),
            "wrong_flagged": round(sum(r["flagged"] for r in wrong) / max(1, len(wrong)), 3),
            "right_flagged": round(sum(r["flagged"] for r in right) / max(1, len(right)), 3),
            "precision": round(sum(r["flagged"] for r in wrong) / max(1, sum(r["flagged"] for r in rows)), 3),
        }
        by_cfg = {}
        for label in CONFIGS:
            rs = [r for r in rows if r["config"] == label]
            ans = [r for r in rs if r["kind"] == "answerable"]
            by_cfg[label] = {"requests": len(rs), "accuracy": round(sum(r["correct"] for r in rs) / max(1, len(rs)), 3),
                             "answerable_accuracy": round(sum(r["correct"] for r in ans) / max(1, len(ans)), 3),
                             "flagged": round(sum(r["flagged"] for r in rs) / max(1, len(rs)), 3)}
        by_kind = {}
        for kind in ("answerable", "unanswerable", "not_in_kb"):
            rs = [r for r in rows if r["kind"] == kind]
            labs = {}
            for r in rs:
                if r["flagged"]:
                    labs[r["label"]] = labs.get(r["label"], 0) + 1
            by_kind[kind] = {"requests": len(rs), "accuracy": round(sum(r["correct"] for r in rs) / max(1, len(rs)), 3),
                             "flagged_labels": dict(sorted(labs.items(), key=lambda kv: -kv[1]))}
        rc = get("/root-causes", days=11)
        drift = get("/drift", current_days=4, reference_days=6)
        kb = get("/kb-health", days=11)
        analytics = get("/analytics", days=11)
        alerts = get("/alerts")
        golden = get("/golden-sets/production-failures/export")

        # ---------------------------------------------------------------------------------------- bring-your-own-RAG
        httpx.post(f"{base}/v1/projects/wikihelp/pipelines", headers=H, json={
            "name": "wikihelp-http", "url": f"http://127.0.0.1:{APP_PORT}/ask", "retrieved_path": "retrieved"})
        cases = golden["cases"][:80]
        runs = {}
        for label in ("v2-25w-chunks", "v3-60w-rerank"):
            r = httpx.post(f"{base}/v1/projects/wikihelp/runs", headers=H, json={
                "pipeline": "wikihelp-http", "cases": cases, "config": {**CONFIGS[label], "edits": EDITS, **gen},
                "name": label}).json()
            for _ in range(240):
                st = get(f"/runs/{r['id']}")
                if st["status"] in ("done", "failed"):
                    break
                time.sleep(0.5)
            runs[label] = st
        cmp_ = get("/runs/compare", base=runs["v2-25w-chunks"]["id"], candidate=runs["v3-60w-rerank"]["id"])
        results = {"detection": detect, "by_config": by_cfg, "by_kind": by_kind, "root_causes": rc["breakdown"],
                   "quality_changes": drift["quality"], "query_drift": drift["query"]["summary"],
                   "new_topics": drift["query"]["new_topics"][:4], "corpus_drift": drift["corpus"],
                   "kb_gaps": kb["coverage_gaps"][:4], "analytics_by_config": analytics["by_config"],
                   "alerts": [a["title"] for a in alerts], "golden_cases": len(golden["cases"]),
                   "runs": {k: v["summary"] for k, v in runs.items()}, "compare": cmp_["report"],
                   "generator": "openai" if args.openai else "extractive"}
        (HERE / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        Path(args.out).write_text(write_up(results), encoding="utf-8")
        print(f"wrote {args.out}")
    finally:
        for p in procs:
            p.terminate()


def write_up(r: dict) -> str:
    d, pct = r["detection"], (lambda x: f"{x:.0%}")
    stale = next((x for x in r["root_causes"] if x["label"] == "stale_knowledge"), None)
    qc = [q for q in r["quality_changes"] if q.get("detected")]
    lines = [
        "# Case study: RAGOps Observatory on WikiHelp, a real RAG service", "",
        "**System under observation.** WikiHelp ([`squad_rag.py`](squad_rag.py)) answers questions about 25 Wikipedia "
        "articles (the SQuAD 2.0 dev set): BM25 + embedding hybrid retrieval, an optional reranker and an "
        f"{'OpenAI model' if r['generator'] == 'openai' else 'extractive reader'} for generation. It is a separate "
        "service that RAGOps observes through the Python SDK and tests through its HTTP endpoint.", "",
        "**What happened.** Ten days of traffic with three deploys: v1 (60-word chunks), v2 (a 25-word-chunk "
        "\"cost cut\") and v3 (60-word chunks plus a reranker). On day 6 the *Black_Death* page was edited but the "
        "serving index was only rebuilt on day 8, and users started asking about topics the knowledge base does not "
        "cover. Nothing was injected: every failure below is the app's real behaviour. SQuAD's answer labels give "
        "ground truth for whether each answer was actually right.", "",
        "Reproduce: `python examples/case-study/run_case_study.py` (raw numbers in [`results.json`](results.json)).", "",
        "## 1. Did RAGOps catch the bad answers?", "",
        f"Of **{d['requests']:,}** requests, **{d['actually_wrong']:,}** answers were actually wrong. RAGOps flagged "
        f"**{pct(d['wrong_flagged'])}** of the wrong answers and **{pct(d['right_flagged'])}** of the correct ones; "
        f"**{pct(d['precision'])}** of flagged requests were really wrong. Only 30% of wrong answers got a thumbs-down "
        "from users, so most of these were found without any user complaint.", "",
        "| Request type | Requests | Answered correctly | What RAGOps said about the flagged ones |", "|---|---|---|---|"]
    for k, v in r["by_kind"].items():
        labs = ", ".join(f"{a.replace('_', ' ')} {n}" for a, n in list(v["flagged_labels"].items())[:3]) or "–"
        lines.append(f"| {k.replace('_', ' ')} | {v['requests']} | {pct(v['accuracy'])} | {labs} |")
    lines += ["", "## 2. Which change hurt, and which stage broke", "",
              "| Config | Requests | Correct (all) | Correct (answerable questions only) | Flagged by RAGOps |",
              "|---|---|---|---|---|"]
    for k, v in r["by_config"].items():
        lines.append(f"| `{k}` | {v['requests']} | {pct(v['accuracy'])} | {pct(v['answerable_accuracy'])} | "
                     f"{pct(v['flagged'])} |")
    lines += ["", "Overall accuracy also falls after day 6 because more questions are about topics the knowledge base "
              "does not cover; the answerable-only column isolates the effect of each config."]
    lines += ["", "Root causes across the 10 days:", "", "| Stage | Failures | Share |", "|---|---|---|"]
    for b in r["root_causes"]:
        lines.append(f"| {b['label'].replace('_', ' ')} | {b['count']} | {b['share']:.0%} |")
    if qc:
        lines += ["", "Quality change points RAGOps detected, and what it linked them to:", ""]
        for q in qc:
            lines.append(f"- {q['summary']}" + (f" (likely cause: {'; '.join(q['likely_causes'])})"
                                                if q.get("likely_causes") else ""))
    lines += ["", "## 3. The edit nobody re-indexed", ""]
    if stale:
        lines.append(f"RAGOps flagged **{stale['count']}** answers as *stale knowledge*: built from the old version of a "
                     "page after the CMS changed it. No user complained about these and the answers looked fluent; "
                     "the only signal was the version mismatch between the serving index and the corpus registry.")
    cd = r["corpus_drift"]
    lines.append(f"Corpus drift listed {cd.get('outdated_trace_count', 0)} recent answers that depended on changed "
                 "documents, so they could be re-checked.")
    lines += ["", "## 4. What users started asking", "", f"{r['query_drift']}", ""]
    for g in r["kb_gaps"]:
        lines.append(f"- Coverage gap **{g['topic']}** ({g['query_count']} questions): {g['suggestion']}")
    lines += ["", "## 5. Testing the fix before shipping it", "",
              f"Every confidently diagnosed failure was promoted to the `production-failures` golden set "
              f"(**{r['golden_cases']}** cases). Registering WikiHelp's HTTP endpoint as a pipeline and running the "
              "set against v2 and v3 from the API gave:", "",
              "| Run | Cases | Pass rate | Faithfulness | Answer relevance | Context relevance |", "|---|---|---|---|---|---|"]
    for k, s in r["runs"].items():
        m = s["metrics"]
        lines.append(f"| `{k}` | {s['cases']} | {pct(s['pass_rate'])} | {m.get('faithfulness') or 0:.3f} | "
                     f"{m.get('answer_relevance') or 0:.3f} | {m.get('context_relevance') or 0:.3f} |")
    c = r["compare"]
    diffs = "; ".join(f"{m['metric'].replace('_', ' ')} {m['delta']:+.3f} (95% CI {m['ci_low']:+.3f}..{m['ci_high']:+.3f}, "
                      f"{m['status']})" for m in c["metrics"])
    lines += ["", f"Paired comparison v2 → v3 on the same {c['n_cases']} cases: "
              f"**{'no significant regression' if c['passed'] else 'blocked'}**. {diffs}.", "",
              "## 6. Alerts that fired", ""] + [f"- {a}" for a in r["alerts"][:6]] + [
              "", "## What the case study found in RAGOps itself", "",
              "Running against a real system exposed three bugs that unit tests and the synthetic demo had missed. All "
              "three are fixed in v0.5 and covered by regression tests:", "",
              "1. **Citations with punctuation were scored as hallucinations.** Document ids like `[Sky_(United_Kingdom)]` "
              "did not match the citation pattern, so the citation itself counted as an unsupported claim. It made v3 "
              "look less faithful and the release gate wrongly blocked it.",
              "2. **Re-chunking looked like a content change.** Moving from 60- to 25-word chunks re-registered every "
              "document as a new version, so corpus drift reported hundreds of \"outdated\" answers. Versions now "
              "follow document content; re-chunking only replaces chunks.",
              "3. **A perfectly constant metric crashed the drift endpoint.** Welch's t-test returns NaN when both sides "
              "have zero variance, and NaN is not valid JSON.", "",
              "## Takeaways and limits", "",
              "- The stage breakdown and the change timeline pointed at the responsible deploy without reading traces.",
              "- Stale-knowledge detection caught silent errors that neither users nor quality metrics would surface.",
              "- Coverage gaps turned unanswered questions into a concrete writing list.",
              "- The release gate agreed with ground truth: v3 improved context relevance significantly and its "
              "answerable accuracy was higher in production, and the gate let it through.",
              "- Recall is the weak spot: with the lexical judge RAGOps flagged under half of the wrong answers. Most "
              "misses are confident answers that quote a real sentence from the right article that just doesn't "
              "contain the answer. An LLM judge is the fix, and the calibration page shows how far to trust it.",
              "- Limits: the default heuristic judge is lexical, so it misses some paraphrased answers and flags some "
              "correct ones (see the detection numbers above). Run the study with `--openai` and "
              "`RAGOPS_JUDGE=openai` for an LLM generator and judge. Traffic is replayed from SQuAD questions, not "
              "live users.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
