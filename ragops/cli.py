"""`ragops` command-line interface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

# Windows consoles (cp1252/cp437) and some CI logs cannot encode the reports' emoji and arrows. Never crash on that:
# switch Windows to UTF-8, and anywhere else that is not UTF-8 replace what cannot be printed.
for _stream in (sys.stdout, sys.stderr):
    _enc = (getattr(_stream, "encoding", None) or "").lower().replace("-", "")
    if _enc != "utf8":
        try:
            _stream.reconfigure(encoding="utf-8" if sys.platform == "win32" else _enc or "utf-8", errors="replace")
        except (AttributeError, ValueError, LookupError):
            pass

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="RAGOps Observatory: production reliability for RAG pipelines.")


@app.command()
def version() -> None:
    """Print the installed version."""
    from ragops import __version__

    typer.echo(__version__)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, reload: bool = False,
          inline_jobs: bool = typer.Option(False, help="Process jobs in the API process (no separate worker).")) -> None:
    """Run the REST API (OpenAPI docs at /docs)."""
    import uvicorn

    if inline_jobs:
        os.environ["RAGOPS_INLINE_JOBS"] = "true"
    uvicorn.run("ragops.server.app:app", host=host, port=port, reload=reload)


@app.command()
def worker(backend: str = typer.Option("auto", help="auto | db | arq. auto uses Arq when RAGOPS_REDIS_URL is set.")) -> None:
    """Run the durable job worker (evaluation, diagnosis, test runs, SLO checks, retention)."""
    if backend == "arq" or (backend == "auto" and os.environ.get("RAGOPS_REDIS_URL")):
        from ragops.queue_arq import run_worker

        run_worker()
        return
    from ragops.worker import run_forever

    run_forever()


@app.command()
def dashboard(port: int = 8501, api_url: str = typer.Option("http://localhost:8000", envvar="RAGOPS_API_URL")) -> None:
    """Launch the Streamlit dashboard."""
    path = Path(__file__).parent / "dashboard" / "Home.py"
    env = {**os.environ, "RAGOPS_API_URL": api_url}
    raise typer.Exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(path), "--server.port", str(port),
                                      "--server.headless", "true"], env=env))


@app.command()
def demo(project: str = "nimbus-support", days: int = 14, per_day: int = 45, reset: bool = False,
         llm_judge: bool = typer.Option(False, help="Judge the demo with your LLM judge (hundreds of paid calls).")) -> None:
    """Seed a demo project with 14 days of realistic traffic and injected incidents."""
    from ragops.demo.seed import seed_demo

    if not llm_judge:
        # The demo's storyline is calibrated for the free rule-based judge; an LLM judge would also spend hundreds of
        # paid calls on made-up traffic.
        from ragops.config import reset_settings

        os.environ["RAGOPS_JUDGE"] = "heuristic"
        reset_settings()
        typer.echo("Using the free rule-based judge for the demo (add --llm-judge to use your LLM judge).")
    typer.echo(f"Seeding demo project '{project}' ({days} days x ~{per_day} requests)...")
    try:
        r = seed_demo(project, days, per_day, reset=reset)
    except ValueError as e:
        typer.secho(str(e), fg="red")
        raise typer.Exit(1)
    typer.secho(f"\nDone: {r['traces']} traces, failure rate {r['failure_rate']:.0%}", fg="green")
    typer.echo(f"Root causes: {r['root_causes']}")
    typer.echo(f"Health: {r['health']['message']}")
    typer.echo(f"\nProject admin key (shown once): {r['admin_key']}")
    typer.echo(f"Ingest-only key for your services: {r['ingest_key']}")


@app.command("create-project")
def create_project(name: str) -> None:
    """Create a project directly in the database and print its admin key."""
    from ragops import services as svc
    from ragops.db import init_db, session_scope

    init_db()
    with session_scope() as db:
        if svc.get_project_by_name(db, name):
            typer.secho("project exists", fg="red")
            raise typer.Exit(1)
        p = svc.create_project(db, name)
        key = svc.create_api_key(db, p.id, "default-admin", "admin")
        svc.audit(db, p.id, "cli", "project.create", {"name": name})
    typer.echo(f"Project '{name}' created. Admin key (shown once): {key}")


@app.command()
def report(project: str, days: int = 7, fmt: str = typer.Option("md", "--format", help="md | html | json | pdf"),
           out: Optional[Path] = None) -> None:
    """Build the reliability report for a project (reads the database directly)."""
    from ragops import services as svc
    from ragops.db import init_db, session_scope
    from ragops.reports import build_report, to_html, to_markdown

    init_db()
    with session_scope() as db:
        p = svc.get_project_by_name(db, project)
        if p is None:
            typer.secho(f"project '{project}' not found", fg="red")
            raise typer.Exit(1)
        r = build_report(db, p, days)
    if fmt == "pdf":
        from ragops.report_pdf import to_pdf

        out = out or Path(f"{project}-reliability.pdf")
        out.write_bytes(to_pdf(r))
        typer.echo(f"wrote {out}")
        return
    text = json.dumps(r, indent=2, default=str) if fmt == "json" else to_html(r) if fmt == "html" else to_markdown(r)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(text)


@app.command()
def benchmark(dataset: str = typer.Option("synthetic", help="synthetic | squad (SQuAD 2.0 dev, downloaded once)"),
              out: Optional[Path] = None, seed: int = 0, questions: int = typer.Option(300, help="SQuAD questions"),
              squad_path: Optional[Path] = typer.Option(None, help="Local SQuAD 2.0 dev JSON (skips the download)")) -> None:
    """Run the root-cause attribution benchmark (failure injection) and write the results."""
    if dataset == "squad":
        from ragops.benchmark_squad import run_squad_benchmark
        from ragops.benchmark_squad import to_markdown as squad_md

        out = out or Path("benchmark/SQUAD_RESULTS.md")
        typer.echo("SQuAD 2.0 dev set (CC BY-SA 4.0): downloading on first use to ~/.cache/ragops/")
        r = run_squad_benchmark(squad_path, questions, seed=seed)
        md = squad_md(r)
    else:
        from ragops.benchmark import run_benchmark, to_markdown

        out = out or Path("benchmark/RESULTS.md")
        r = run_benchmark(seed)
        md = to_markdown(r)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(r, indent=2), encoding="utf-8")
    typer.echo(f"[{dataset}] attribution accuracy {r['attribution_accuracy']:.1%} | macro recall {r['macro_recall']:.1%} | "
               f"false alarms {r['false_alarm_rate']:.1%} on {r['cases']} cases -> {out}")


def read_docs(spec: str) -> list[dict]:
    """Documents from a folder (.md/.txt/.rst/.html), a JSON/YAML file, or 'module:ATTR'."""
    from ragops.gate import load_callable, load_json_or_yaml

    p = Path(spec)
    if p.is_dir():
        docs = []
        for f in sorted(p.rglob("*")):
            if f.is_file() and f.suffix.lower() in (".md", ".txt", ".rst", ".html", ".htm"):
                text = f.read_text(encoding="utf-8", errors="ignore")
                if f.suffix.lower() in (".html", ".htm"):
                    import re

                    text = re.sub(r"<[^>]+>", " ", text)
                docs.append({"doc_id": str(f.relative_to(p)), "title": f.stem, "text": text})
        return docs
    if spec.endswith((".json", ".yaml", ".yml")):
        docs = load_json_or_yaml(spec)
        return docs.get("documents", docs) if isinstance(docs, dict) else docs
    docs = load_callable(spec)
    return docs() if callable(docs) else docs


def _load_corpus(spec: str | None, chunk_words: int):
    if not spec:
        return None
    from ragops.diagnosis import CorpusChunk, CorpusView
    from ragops.gate import load_callable, load_json_or_yaml
    from ragops.services_chunking import chunk_text

    if Path(spec).is_dir():
        docs = read_docs(spec)
    elif spec.endswith((".json", ".yaml", ".yml")):
        docs = load_json_or_yaml(spec)
        docs = docs.get("documents", docs) if isinstance(docs, dict) else docs
    else:
        docs = load_callable(spec)
        docs = docs() if callable(docs) else docs
    chunks = []
    for d in docs:
        for i, t in enumerate(chunk_text(d["text"], chunk_words)):
            chunks.append(CorpusChunk(f"{d['doc_id']}#{i}", d["doc_id"], int(d.get("version", 1)), i, t))
    return CorpusView(chunks, None, {d["doc_id"]: int(d.get("version", 1)) for d in docs}, set())


@app.command()
def gate(
    pipeline: str = typer.Option(..., help="Your pipeline callable, 'module:function' or 'file.py:function'."),
    baseline: str = typer.Option(..., help="Baseline config (YAML/JSON file)."),
    candidate: str = typer.Option(..., help="Candidate config (YAML/JSON file)."),
    golden: Optional[str] = typer.Option(None, help="Golden set file: JSONL, JSON, YAML or CSV (the same format as `ragops test`)."),
    server: Optional[str] = typer.Option(None, envvar="RAGOPS_URL", help="Fetch the golden set from a server."),
    api_key: Optional[str] = typer.Option(None, envvar="RAGOPS_API_KEY"),
    project: Optional[str] = typer.Option(None, envvar="RAGOPS_PROJECT"),
    golden_set: str = typer.Option("production-failures", help="Golden set name on the server."),
    corpus: Optional[str] = typer.Option(None, help="Corpus for stage diagnosis: docs JSON/YAML or 'module:ATTR'."),
    chunk_words: int = 45,
    max_drop: float = typer.Option(0.02, help="Smallest mean drop that can block a release."),
    out: Optional[Path] = typer.Option(None, help="Write the Markdown report (e.g. for a PR comment)."),
    json_out: Optional[Path] = None,
    judge: Optional[str] = typer.Option(None, help="heuristic | openai | ollama (default: RAGOPS_JUDGE, as in `ragops test`)"),
    config_file: str = typer.Option("ragops.yaml", "--file", "-f", help="Read `corpus:` from here when --corpus is not given."),
    junit: Optional[Path] = typer.Option(None, help="Write JUnit XML (one test per metric and budget) for CI."),
    max_p95_ms: Optional[float] = typer.Option(None, help="Block if the candidate's p95 latency exceeds this (ms)."),
    max_latency_increase: Optional[float] = typer.Option(None, help="Block if p95 latency grows more than this fraction (0.3 = 30%)."),
    max_error_rate: Optional[float] = typer.Option(None, help="Block if more than this fraction of candidate cases crash."),
) -> None:
    """Replay the golden set through baseline and candidate configs; exit 1 if quality regresses significantly."""
    from ragops.gate import load_callable, load_json_or_yaml, run_gate

    if golden:
        from ragops.runs import load_dataset

        g = load_dataset(golden)  # JSONL, JSON, YAML or CSV: the same files `ragops test` reads
    elif server and api_key and project:
        import httpx

        r = httpx.get(f"{server.rstrip('/')}/v1/projects/{project}/golden-sets/{golden_set}/export",
                      headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        r.raise_for_status()
        g = r.json()
    else:
        typer.secho("give --golden FILE, or --server/--api-key/--project to fetch it", fg="red")
        raise typer.Exit(2)
    cases = g["cases"] if isinstance(g, dict) else g
    if not cases:
        typer.secho("golden set is empty; nothing to compare", fg="yellow")
        raise typer.Exit(0)
    from ragops.eval.judges import get_judge
    from ragops.gate import DEFAULT_GATE_METRICS

    # same judge and corpus as `ragops test`, so a release is gated on the scores you see there
    cfg_file = _yaml(config_file) if Path(config_file).exists() else {}
    if not corpus and cfg_file.get("corpus"):
        corpus, chunk_words = cfg_file["corpus"], int(cfg_file.get("chunk_words", chunk_words))
    the_judge = get_judge(judge)
    typer.echo(f"Judge: {_judge_label(the_judge)} · {len(cases)} golden cases, each run twice", err=True)
    metrics = DEFAULT_GATE_METRICS + (["answer_correctness"] if any(c.get("expected_answer") for c in cases) else [])
    rep = run_gate(cases, load_callable(pipeline), load_json_or_yaml(baseline), load_json_or_yaml(candidate),
                   judge=the_judge, metrics=metrics, max_drop=max_drop, corpus=_load_corpus(corpus, chunk_words),
                   max_p95_ms=max_p95_ms, max_latency_increase=max_latency_increase, max_error_rate=max_error_rate)
    md = rep.to_markdown()
    typer.echo(md)
    if out:
        out.write_text(md, encoding="utf-8")
    if json_out:
        json_out.write_text(json.dumps(rep.to_dict(), indent=2), encoding="utf-8")
    if junit:
        junit.write_text(rep.to_junit(), encoding="utf-8")
    raise typer.Exit(0 if rep.passed else 1)


# ------------------------------------------------------------------------------------------------ bring your own RAG
INIT_YAML = """# RAGOps test configuration. Run: ragops test
project: {project}
server: http://localhost:8000          # optional: needed for --push and golden sets from the server

pipeline:
  kind: python                         # python | http
  callable: my_rag:answer              # module:function, or path/to/file.py:function
  # kind: http                         # any language: RAGOps POSTs {{"query": ..., "config": {{...}}}}
  # url: http://localhost:9000/ask
  # answer_path: answer                # dotted path to the answer in the JSON response
  # contexts_path: contexts            # dotted path to the list of retrieved chunks / sources
  # headers: {{Authorization: "Bearer ..."}}

config: {{}}                            # passed to your pipeline as the second argument
dataset: rag_tests.jsonl               # JSONL / JSON / CSV: query, expected_answer?, expected_doc_ids?
corpus: docs/                          # optional: your documents, enables knowledge-gap / retrieval-miss diagnosis
chunk_words: 120

thresholds:                            # `ragops test` exits 1 when these are violated (use it in CI)
  max_failure_rate: 0.25
  min_metrics: {{faithfulness: 0.7}}
"""

INIT_PIPELINE = '''"""Your RAG system, wrapped for RAGOps. Replace the body with calls to your retriever and LLM.

Return a dict with:
    answer     the generated answer (str)
    contexts   the chunks you put in the prompt: strings, or dicts with text/doc_id/chunk_id/score
    retrieved  (optional) the retriever's candidates before reranking
LangChain / LlamaIndex / Haystack objects can be returned as-is in contexts.
"""


def answer(query: str, config: dict) -> dict:
    # docs = my_retriever.search(query, k=config.get("top_k", 5))
    # reply = my_llm.generate(query, docs)
    docs = [{"text": "RAGOps Observatory diagnoses which stage of a RAG pipeline failed.", "doc_id": "about.md"}]
    reply = "RAGOps Observatory diagnoses which stage of a RAG pipeline failed. [about.md]"
    return {"answer": reply, "contexts": docs}
'''

INIT_DATASET = """{{"query": "What does RAGOps Observatory do?", "expected_doc_ids": ["about.md"]}}
{{"query": "Which stage of a RAG pipeline failed?", "expected_answer": "RAGOps diagnoses the failing stage."}}
"""


@app.command()
def init(project: str = typer.Option("my-rag", help="Project name"), force: bool = False) -> None:
    """Scaffold ragops.yaml, a pipeline wrapper (my_rag.py) and a sample dataset in the current folder."""
    files = {"ragops.yaml": INIT_YAML.format(project=project), "my_rag.py": INIT_PIPELINE,
             "rag_tests.jsonl": INIT_DATASET.format()}
    for name, content in files.items():
        if Path(name).exists() and not force:
            typer.echo(f"skip {name} (exists)")
            continue
        Path(name).write_text(content, encoding="utf-8")
        typer.echo(f"wrote {name}")
    Path("docs").mkdir(exist_ok=True)
    if not any(Path("docs").iterdir()):
        Path("docs/about.md").write_text("RAGOps Observatory diagnoses which stage of a RAG pipeline failed.\n", encoding="utf-8")
    typer.secho("\nNext: edit my_rag.py to call your RAG, add test questions to rag_tests.jsonl, then run `ragops test`.",
                fg="green")


def _no_pipeline(config_file: str, extra: str = "pass --pipeline / --endpoint or run `ragops init`") -> None:
    """Explain a missing pipeline; the usual cause is a --file path that does not exist from the current folder."""
    if config_file and not Path(config_file).exists():
        typer.secho(f"config file not found: {config_file} (looked in {Path.cwd()}). Check the path, or `cd` to the "
                    "folder it is relative to.", fg="red")
    else:
        typer.secho(f"no pipeline in {config_file}: {extra}", fg="red")


def _yaml(path: str) -> dict:
    import yaml

    if not Path(path).exists():
        return {}
    # `callable: my_rag:answer` lives next to the yaml: make it importable from any folder.
    here = str(Path(path).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


@app.command("test")
def test_cmd(
    config_file: str = typer.Option("ragops.yaml", "--file", "-f", help="Test configuration (see `ragops init`)."),
    pipeline: Optional[str] = typer.Option(None, help="Python callable 'module:function' (overrides the file)."),
    endpoint: Optional[str] = typer.Option(None, help="HTTP endpoint of your RAG (overrides the file)."),
    answer_path: str = "answer", contexts_path: str = "contexts",
    dataset: Optional[str] = typer.Option(None, help="JSONL/JSON/CSV test cases."),
    golden_set: Optional[str] = typer.Option(None, help="Use a golden set from the server instead of a file."),
    corpus: Optional[str] = typer.Option(None, help="Docs folder / JSON / module:ATTR for stage diagnosis."),
    config: Optional[str] = typer.Option(None, help="Pipeline config YAML/JSON file."),
    name: Optional[str] = typer.Option(None, help="Run name."),
    out: Path = typer.Option(Path("ragops-report.md"), help="Markdown report path."),
    json_out: Optional[Path] = None,
    push: bool = typer.Option(False, help="Upload the run to the server so it shows in the UI."),
    server: Optional[str] = typer.Option(None, envvar="RAGOPS_URL"),
    api_key: Optional[str] = typer.Option(None, envvar="RAGOPS_API_KEY"),
    project: Optional[str] = typer.Option(None, envvar="RAGOPS_PROJECT"),
    judge: Optional[str] = typer.Option(None, help="heuristic | openai | ollama (default: RAGOPS_JUDGE)"),
) -> None:
    """Test YOUR RAG system: run a dataset through it, score every answer, diagnose failures by pipeline stage."""
    from ragops.eval.judges import get_judge
    from ragops.gate import load_json_or_yaml
    from ragops.pipelines import HttpPipeline, PythonPipeline, from_spec, spec_label
    from ragops.runs import load_dataset, report_markdown, run_cases, summarize

    cfg_file = _yaml(config_file)
    server = server or cfg_file.get("server")
    project = project or cfg_file.get("project")
    if pipeline:
        fn, pname = PythonPipeline(pipeline), pipeline
    elif endpoint:
        fn, pname = HttpPipeline(endpoint, answer_path=answer_path, contexts_path=contexts_path), endpoint
    elif cfg_file.get("pipeline"):
        spec = cfg_file["pipeline"]
        fn, pname = from_spec(spec), spec_label(spec)
    else:
        _no_pipeline(config_file)
        raise typer.Exit(2)
    pcfg = load_json_or_yaml(config) if config else (cfg_file.get("config") or {})
    if golden_set:
        if not (server and api_key and project):
            typer.secho("--golden-set needs --server, --api-key and --project", fg="red")
            raise typer.Exit(2)
        import httpx

        r = httpx.get(f"{server.rstrip('/')}/v1/projects/{project}/golden-sets/{golden_set}/export",
                      headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        r.raise_for_status()
        cases, dname = r.json()["cases"], f"golden:{golden_set}"
    else:
        dpath = dataset or cfg_file.get("dataset")
        if not dpath:
            typer.secho("no dataset: pass --dataset or set `dataset:` in ragops.yaml", fg="red")
            raise typer.Exit(2)
        cases, dname = load_dataset(dpath), dpath
    corpus_view = _load_corpus(corpus or cfg_file.get("corpus"), int(cfg_file.get("chunk_words", 120)))
    typer.echo(f"Testing {pname} on {len(cases)} cases ({dname})" + (" with corpus diagnosis" if corpus_view else ""))
    the_judge = get_judge(judge)
    from ragops import __version__

    typer.echo(f"Judge: {_judge_label(the_judge)} · ragops {__version__}")
    with typer.progressbar(length=len(cases), label="running") as bar:
        rows = run_cases(fn, cases, pcfg, the_judge, corpus=corpus_view, on_case=lambda i, r: bar.update(1))
    summ = summarize(rows)
    run_name = name or f"{Path(str(pname)).name} @ {dname}"
    md = report_markdown(run_name, summ, rows)
    out.write_text(md, encoding="utf-8")
    if json_out:
        json_out.write_text(json.dumps({"summary": summ, "rows": rows, "pipeline": str(pname), "dataset": dname,
                                        "judge": getattr(the_judge, "name", "heuristic"),
                                        "judge_is_llm": getattr(the_judge, "name", "heuristic") != "heuristic"},
                                       indent=2, default=str), encoding="utf-8")
    typer.echo("\n" + md)
    _warn_judge_fallbacks(the_judge, rows)
    if push:
        if not (server and api_key and project):
            typer.secho("--push needs --server, --api-key and --project (or RAGOPS_* env vars)", fg="red")
            raise typer.Exit(2)
        import httpx

        try:
            r = httpx.post(f"{server.rstrip('/')}/v1/projects/{project}/runs/upload", timeout=60,
                           headers={"Authorization": f"Bearer {api_key}"},
                           json={"name": run_name, "pipeline": str(pname), "config": pcfg, "dataset": dname,
                                 "rows": json.loads(json.dumps(rows, default=str))})
        except httpx.ConnectError:
            typer.secho(f"could not push: no RAGOps server at {server}. Start it in another terminal with "
                        "`ragops serve` (from the project folder), then run this again. The report above is saved.",
                        fg="red")
            raise typer.Exit(2)
        if r.status_code == 401:
            typer.secho("could not push: the API key was not accepted (RAGOPS_API_KEY).", fg="red")
            raise typer.Exit(2)
        if r.status_code == 404:
            typer.secho(f"could not push: project '{project}' does not exist on {server}. Create it with "
                        f"`ragops create-project {project}` in the folder where `ragops serve` runs.", fg="red")
            raise typer.Exit(2)
        r.raise_for_status()
        typer.secho(f"pushed run {r.json()['id']} to {server}", fg="green")
    th = cfg_file.get("thresholds") or {}
    problems = []
    if th.get("max_failure_rate") is not None and summ["failure_rate"] is not None \
            and summ["failure_rate"] > float(th["max_failure_rate"]):
        problems.append(f"failure rate {summ['failure_rate']:.0%} > {float(th['max_failure_rate']):.0%}")
    for m, lo in (th.get("min_metrics") or {}).items():
        v = summ["metrics"].get(m)
        if v is not None and v < float(lo):
            problems.append(f"{m} {v:.3f} < {lo}")
    if problems:
        typer.secho("FAILED thresholds: " + "; ".join(problems), fg="red")
        raise typer.Exit(1)
    typer.secho(f"report written to {out}", fg="green")


@app.command("generate")
def generate_cmd(
    docs: str = typer.Argument(..., help="Folder of .md/.txt/.html files, a JSON/YAML file, or module:ATTR."),
    out: Path = typer.Option(Path("generated_tests.jsonl"), "--out", "-o", help="JSONL file to write."),
    n: int = typer.Option(30, help="Answerable questions to generate."),
    unanswerable: float = typer.Option(0.15, help="Share of extra questions the documents do NOT answer."),
    per_passage: int = typer.Option(3, help="Questions asked per passage."),
    offline: bool = typer.Option(False, help="No LLM: simple template questions (smoke tests, CI)."),
    judge: Optional[str] = typer.Option(None, help="LLM used to write questions (default: RAGOPS_JUDGE settings)."),
    security: bool = typer.Option(False, help="Add prompt-injection and data-leak probes (the assistant should decline)."),
) -> None:
    """Generate a test set from your documents: verified questions + expected answers, plus questions to decline."""
    from ragops.eval.judges import get_judge
    from ragops.testgen import generate, to_jsonl

    documents = read_docs(docs)
    if not documents:
        typer.secho(f"no documents found in {docs}", fg="red")
        raise typer.Exit(2)
    j = None if offline else get_judge(judge)
    if not offline and getattr(j, "name", "heuristic") == "heuristic":
        typer.secho("No LLM judge configured (RAGOPS_JUDGE), so using the offline generator. Its questions are "
                    "templates; set an LLM for realistic questions.", fg="yellow")
    rep = generate(documents, n=n, unanswerable_share=unanswerable, judge=j, offline=offline, per_passage=per_passage)
    if security:
        from ragops.testgen import security_cases

        rep.cases += security_cases()
    out.write_text(to_jsonl(rep.cases), encoding="utf-8")
    kinds = {k: sum(1 for c in rep.cases if c["kind"] == k) for k in ("answerable", "unanswerable", "security")}
    typer.echo(f"{len(documents)} documents -> {len(rep.cases)} test cases ({kinds['answerable']} answerable, "
               f"{kinds['unanswerable']} unanswerable" + (f", {kinds['security']} security probes" if kinds["security"] else "")
               + f") with {rep.generator}; {rep.proposed} proposed, rejected: "
               f"{rep.rejected or 'none'}")
    typer.secho(f"wrote {out}. Review a few cases, then: ragops test --dataset {out}", fg="green")


@app.command("assure")
def assure_cmd(
    config_file: str = typer.Option("ragops.yaml", "--file", "-f", help="Test configuration (see `ragops init`)."),
    dataset: Optional[str] = typer.Option(None, help="Test cases (default: `dataset:` in the file)."),
    from_json: Optional[Path] = typer.Option(None, help="Use a finished run (`ragops test --json-out`), no re-run."),
    out: Path = typer.Option(Path("assurance.html"), help="HTML report path; the JSON evidence goes next to it."),
    title: Optional[str] = typer.Option(None, help="Report title, e.g. the client's assistant name."),
    brand: Optional[str] = typer.Option(None, help="Your company name shown on the report (agencies)."),
    judge: Optional[str] = typer.Option(None, help="heuristic | openai | ollama (default: RAGOPS_JUDGE)"),
    verify: Optional[Path] = typer.Option(None, help="Check an assurance JSON against its fingerprint and exit."),
) -> None:
    """Assurance report: verdict (Ready / Ready with conditions / Not ready), root causes, documents to fix,
    judge reliability, regulatory evidence mapping, and a tamper-evident fingerprint."""
    from ragops import __version__
    from ragops.assurance import build, to_html
    from ragops.assurance import verify as verify_report

    if verify:
        ok = verify_report(json.loads(verify.read_text(encoding="utf-8")))
        typer.secho("fingerprint matches: the report is unchanged" if ok else
                    "fingerprint does NOT match: the report was changed after it was issued",
                    fg="green" if ok else "red")
        raise typer.Exit(0 if ok else 1)
    cfg_file = _yaml(config_file) if Path(config_file).exists() else {}
    if from_json:
        data = json.loads(from_json.read_text(encoding="utf-8"))
        rows, summ = data["rows"], data["summary"]
        meta = {"pipeline": data.get("pipeline") or str(cfg_file.get("pipeline", {}).get("callable", "") or ""),
                "dataset": str(from_json), "judge": data.get("judge", "from finished run"),
                "judge_is_llm": bool(data.get("judge_is_llm", True))}
    else:
        from ragops.eval.judges import get_judge
        from ragops.pipelines import from_spec, spec_label
        from ragops.runs import load_dataset, run_cases, summarize

        if not cfg_file.get("pipeline"):
            _no_pipeline(config_file, "create it with `ragops init`, or pass --from-json")
            raise typer.Exit(2)
        spec = cfg_file["pipeline"]
        if not dataset and not cfg_file.get("dataset"):
            typer.secho("no dataset: pass --dataset (try `ragops generate docs/`)", fg="red")
            raise typer.Exit(2)
        # Paths given on the command line are relative to where you are; paths in the yaml are relative to the yaml.
        dpath = str(Path(dataset).resolve()) if dataset else None
        out = out.resolve()
        cfg_dir = Path(config_file).resolve().parent
        if cfg_dir != Path.cwd():
            typer.echo(f"Running from {cfg_dir} (the folder of {Path(config_file).name})")
            os.chdir(cfg_dir)
        cases = load_dataset(dpath or cfg_file["dataset"])
        corpus_view = _load_corpus(cfg_file.get("corpus"), int(cfg_file.get("chunk_words", 120)))
        the_judge = get_judge(judge)
        typer.echo(f"Assuring {spec_label(spec)} on {len(cases)} cases · judge {_judge_label(the_judge)}")
        with typer.progressbar(length=len(cases), label="testing") as bar:
            rows = run_cases(from_spec(spec), cases, cfg_file.get("config") or {}, the_judge, corpus=corpus_view,
                             on_case=lambda i, r: bar.update(1))
        summ = summarize(rows)
        _warn_judge_fallbacks(the_judge, rows)
        meta = {"pipeline": spec_label(spec), "dataset": dpath, "config": cfg_file.get("config") or {},
                "judge": getattr(the_judge, "name", "heuristic"),
                "judge_is_llm": getattr(the_judge, "name", "heuristic") != "heuristic"}
    meta["ragops_version"] = __version__
    rep = build(json.loads(json.dumps(rows, default=str)), summ, meta, cfg_file.get("assurance_thresholds"))
    out.write_text(to_html(rep, title=title, brand=brand), encoding="utf-8")
    jpath = out.with_suffix(".json")
    jpath.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    color = {"Ready": "green", "Ready with conditions": "yellow", "Not ready": "red"}[rep["verdict"]]
    typer.secho(f"Verdict: {rep['verdict']}", fg=color, bold=True)
    for c in rep["criteria"]:
        typer.echo(f"  [{c['result']:>9}] {c['criterion']}: {c['shown']}")
    fails = [r for r in rep.get("rows", []) if r.get("failed")]
    if fails:
        typer.echo(f"\nFailing questions ({len(fails)}), so you can see whether the assistant or the test is wrong:")
        for r in fails[:15]:
            why = "; ".join((r.get("evidence") or {}).get("failure_reasons") or []) or r.get("error") or ""
            typer.echo(f"  - {str(r.get('query'))[:90]}")
            typer.echo(f"      answered: {(str(r.get('answer') or '(nothing)')).strip()[:110]}")
            typer.echo(f"      expected: {str(r.get('expected_answer') or '-')[:110]}")
            typer.echo(f"      stage: {r.get('label')}   why: {why}")
        typer.echo("")
    typer.secho(f"wrote {out} and {jpath} (fingerprint {rep['fingerprint_sha256'][:16]}...)", fg="green")


def _judge_label(j) -> str:
    name = getattr(j, "name", "heuristic")
    if name == "heuristic":
        return ("heuristic (word matching; set RAGOPS_JUDGE and its model/key for an LLM judge that scores by "
                "meaning: see `ragops judge check`)")
    base = getattr(getattr(j, "client", None), "base_url", None)
    return f"{name}" + (f" via {base}" if base else "")


def _warn_judge_fallbacks(j, rows: list[dict]) -> None:
    """An LLM judge that silently falls back makes reports look worse than the pipeline is: say so."""
    if getattr(j, "name", "heuristic") == "heuristic":
        return
    fails = [r for row in rows for r in (row.get("reasons") or {}).values()
             if isinstance(r, str) and r.startswith("[llm judge failed")]
    if fails:
        why = fails[0].removeprefix("[llm judge failed: ").split("; heuristic fallback")[0]
        typer.secho(f"Warning: the LLM judge failed on {len(fails)} scores and used word matching instead. "
                    f"Why: {why}", fg="yellow")


def _parse_try(values: list[str]) -> dict[str, list]:
    """--try top_k=10 --try top_k=20 --try rerank=true  ->  {"top_k": [10, 20], "rerank": [True]}"""
    import yaml

    out: dict[str, list] = {}
    for v in values:
        k, _, raw = v.partition("=")
        if not k or not _:
            raise typer.BadParameter(f"--try expects key=value, got {v!r}")
        out.setdefault(k.strip(), []).append(yaml.safe_load(raw))
    return out


@app.command()
def doctor(
    config_file: str = typer.Option("ragops.yaml", "--file", "-f", help="Test configuration (see `ragops init`)."),
    pipeline: Optional[str] = typer.Option(None, help="Python callable 'module:function' (overrides the file)."),
    endpoint: Optional[str] = typer.Option(None, help="HTTP endpoint of your RAG (overrides the file)."),
    answer_path: str = "answer", contexts_path: str = "contexts",
    dataset: Optional[str] = typer.Option(None, help="JSONL/JSON/CSV test cases."),
    corpus: Optional[str] = typer.Option(None, help="Your documents (folder / JSON / module:ATTR) for stage diagnosis."),
    config: Optional[str] = typer.Option(None, help="Pipeline config YAML/JSON file."),
    try_: list[str] = typer.Option([], "--try", help="Config change to test, key=value (repeatable)."),
    no_trial: bool = typer.Option(False, "--no-trial", help="Only diagnose, do not test fixes."),
    max_trials: int = 6,
    write_config: Optional[Path] = typer.Option(None, help="Write the config with the verified fix applied."),
    out: Path = typer.Option(Path("ragops-doctor.md"), help="Markdown report path."),
    json_out: Optional[Path] = None,
    judge: Optional[str] = typer.Option(None, help="heuristic | openai | ollama | ragas (default: RAGOPS_JUDGE)"),
) -> None:
    """Is my RAG working? Run it, explain every mistake by stage, then TEST candidate fixes and recommend the one that
    verifiably helps. Exit code: 0 healthy, 1 needs work, 2 broken."""
    from ragops.doctor import doctor as run_doctor
    from ragops.eval.judges import get_judge
    from ragops.gate import load_json_or_yaml
    from ragops.pipelines import HttpPipeline, PythonPipeline, from_spec, spec_label
    from ragops.runs import load_dataset

    cfg_file = _yaml(config_file)
    if pipeline:
        fn, pname = PythonPipeline(pipeline), pipeline
    elif endpoint:
        fn, pname = HttpPipeline(endpoint, answer_path=answer_path, contexts_path=contexts_path), endpoint
    elif cfg_file.get("pipeline"):
        spec = cfg_file["pipeline"]
        fn, pname = from_spec(spec), spec_label(spec)
    else:
        _no_pipeline(config_file)
        raise typer.Exit(2)
    dpath = dataset or cfg_file.get("dataset")
    if not dpath:
        typer.secho("no dataset: pass --dataset or set `dataset:` in ragops.yaml", fg="red")
        raise typer.Exit(2)
    cases = load_dataset(dpath)
    pcfg = load_json_or_yaml(config) if config else dict(cfg_file.get("config") or {})
    knobs = _parse_try(try_) or (cfg_file.get("doctor") or {}).get("knobs") or None
    corpus_view = _load_corpus(corpus or cfg_file.get("corpus"), int(cfg_file.get("chunk_words", 120)))
    typer.echo(f"Examining {pname} on {len(cases)} cases" + (" with corpus diagnosis" if corpus_view else ""))
    with typer.progressbar(length=len(cases), label="check-up") as bar:
        rep, _ = run_doctor(fn, cases, pcfg, get_judge(judge), corpus_view, knobs, not no_trial, max_trials,
                            on_case=lambda i, r: bar.update(1),
                            on_trial=lambda ch: typer.echo(f"  trying {ch} ..."))
    md = rep.to_markdown(f"RAG doctor: {pname}")
    out.write_text(md, encoding="utf-8")
    if json_out:
        json_out.write_text(json.dumps(rep.to_dict(), indent=2, default=str), encoding="utf-8")
    typer.echo("\n" + md)
    if write_config and rep.best_fix:
        import yaml

        write_config.write_text(yaml.safe_dump({**pcfg, **rep.best_fix["change"]}, sort_keys=False), encoding="utf-8")
        typer.secho(f"wrote fixed config to {write_config}", fg="green")
    typer.secho(f"report written to {out}", fg="green")
    raise typer.Exit({"healthy": 0, "needs work": 1}.get(rep.verdict, 2))


corpus_app = typer.Typer(help="Manage the corpus registry on a server.")
app.add_typer(corpus_app, name="corpus")


@corpus_app.command("push")
def corpus_push(source: str, server: str = typer.Option("http://localhost:8000", envvar="RAGOPS_URL"),
                api_key: str = typer.Option(..., envvar="RAGOPS_API_KEY"),
                project: str = typer.Option(..., envvar="RAGOPS_PROJECT"), chunk_words: int = 120,
                delete_missing: bool = False) -> None:
    """Register documents (folder of .md/.txt/.html, JSON/YAML file, or module:ATTR). Re-run after every re-index."""
    from ragops.sdk.client import RagopsClient

    docs = read_docs(source)
    rg = RagopsClient(server, api_key=api_key, project=project, sync=True)
    total = {"added": 0, "updated": 0, "unchanged": 0, "deleted": 0, "chunks": 0}
    for i in range(0, len(docs), 100):
        r = rg.upsert_corpus(docs[i:i + 100], chunk_words, delete_missing and i + 100 >= len(docs))
        if r is None:
            typer.secho("upload failed (check URL, key and project)", fg="red")
            raise typer.Exit(1)
        for k in total:
            total[k] += r.get(k, 0)
    rg.close()
    typer.echo(f"{len(docs)} documents: {total}")


@app.command()
def rejudge(server: str = typer.Option("http://localhost:8000", envvar="RAGOPS_URL"),
            api_key: str = typer.Option(..., envvar="RAGOPS_API_KEY"),
            project: str = typer.Option(..., envvar="RAGOPS_PROJECT"),
            all_traces: bool = typer.Option(False, "--all", help="Re-judge every trace, not only the failed ones."),
            limit: int = 500) -> None:
    """Judge traces again with the current judge (after upgrading RAGOps or changing RAGOPS_JUDGE_*).
    Keep `ragops worker` running; it does the judging."""
    import httpx

    h = {"Authorization": f"Bearer {api_key}"}
    try:
        with httpx.Client(base_url=server, headers=h, timeout=30) as c:
            params = {"limit": limit} | ({} if all_traces else {"failed": True})
            r = c.get(f"/v1/projects/{project}/traces", params=params)
            r.raise_for_status()
            ids = [t["id"] for t in r.json()]
            for tid in ids:
                c.post(f"/v1/projects/{project}/traces/{tid}/reprocess").raise_for_status()
    except httpx.ConnectError:
        typer.secho(f"cannot reach {server}: start `ragops serve` first", fg="red")
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        typer.secho(f"HTTP {e.response.status_code}: {e.response.text[:200]} (check the project name and API key)",
                    fg="red")
        raise typer.Exit(1)
    what = "traces" if all_traces else "failed traces"
    typer.echo(f"queued {len(ids)} {what} of '{project}' for re-judging; keep `ragops worker` running, then refresh "
               "the dashboard and click SLOs -> Check SLOs now.")


judge_app = typer.Typer(help="Check the evaluation judge.")
app.add_typer(judge_app, name="judge")


@judge_app.command("check")
def judge_check(kind: Optional[str] = typer.Option(None, "--judge", help="heuristic | openai | ollama | ragas"),
                model: Optional[str] = None, base_url: Optional[str] = None) -> None:
    """Send 12 hand-labelled answers to the judge and report agreement, latency, cost and a verdict."""
    from ragops.eval.judges import get_judge
    from ragops.judge_check import run_check

    j = get_judge(kind, model, base_url)
    typer.echo(f"Checking judge {getattr(j, 'name', kind)} on 12 labelled answers...")
    r = run_check(j)
    for row in r["rows"]:
        mark = "ok " if row["agrees"] else "MISS"
        typer.echo(f"  {mark} {row['metric']:<17} {row['label']:<4} score={row['score']}  ({row['judge']}, {row['seconds']}s)")
    typer.echo(f"\nAgreement {r['agreement']:.0%}, Cohen's kappa {r['kappa']}, {r['seconds']}s, ${r['cost_usd']}"
               + (f", {r['fell_back']} fallbacks" if r["fell_back"] else ""))
    typer.secho(f"Verdict: {r['verdict']}", fg="green" if r["kappa"] and r["kappa"] >= 0.6 else "yellow")
    if r.get("first_error"):
        typer.secho(f"Why it fell back: {r['first_error']}", fg="red")
    if r["verdict"].startswith("not working"):
        raise typer.Exit(1)


alerts_app = typer.Typer(help="Verify alert channels (Slack, Teams, webhook, PagerDuty, email).")
app.add_typer(alerts_app, name="alerts")


@alerts_app.command("test")
def alerts_test(kind: str = typer.Option(..., "--kind", help="slack | teams | webhook | pagerduty | email"),
                target: str = typer.Option(..., "--target", help="webhook URL, PagerDuty routing key, or e-mail address"),
                project: str = typer.Option("ragops-test", help="project name shown in the message")) -> None:
    """Send one real test alert and print what the receiver answered. Exit code 1 on failure."""
    from ragops.alerts import send, test_alert

    title, body, payload = test_alert(project)
    res = send(kind, target, title, body, "info", payload)
    typer.echo(("delivered" if res.ok else "FAILED") + f" via {kind}: {res.detail}")
    raise typer.Exit(0 if res.ok else 1)


db_app = typer.Typer(help="Database schema migrations (Alembic).")
app.add_typer(db_app, name="db")


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    """Apply pending migrations. Databases from v0.4/v0.5 are detected and upgraded in place."""
    from ragops import migrate
    from ragops.db import get_engine

    typer.echo(f"database is now at {migrate.upgrade(get_engine(), revision)}")


@db_app.command("current")
def db_current() -> None:
    """Show the database's migration revision and the latest one."""
    from ragops import migrate
    from ragops.db import get_engine

    eng = get_engine()
    cur = migrate.current(eng) or migrate.detect_legacy_revision(eng) or "empty"
    typer.echo(f"current: {cur}\nlatest:  {migrate.head()}")


@db_app.command("history")
def db_history() -> None:
    """List all migrations."""
    from ragops import migrate

    for line in migrate.history():
        typer.echo(line)


clickhouse_app = typer.Typer(help="Optional ClickHouse analytics backend (needs RAGOPS_CLICKHOUSE_URL).")
app.add_typer(clickhouse_app, name="clickhouse")


@clickhouse_app.command("init")
def clickhouse_init() -> None:
    """Create the ClickHouse database and table."""
    from ragops.clickhouse import get_clickhouse

    ch = get_clickhouse()
    if ch is None:
        typer.secho("set RAGOPS_CLICKHOUSE_URL first", fg="red")
        raise typer.Exit(1)
    ch.init()
    typer.echo(f"ClickHouse ready: {ch.database}.ragops_traces")


@clickhouse_app.command("backfill")
def clickhouse_backfill(project: Optional[str] = None, days: int = 400) -> None:
    """Copy existing traces from the primary database into ClickHouse."""
    from ragops import services as svc
    from ragops.clickhouse import backfill
    from ragops.db import init_db, session_scope

    init_db()
    with session_scope() as db:
        pid = None
        if project:
            p = svc.get_project_by_name(db, project)
            if p is None:
                typer.secho(f"project '{project}' not found", fg="red")
                raise typer.Exit(1)
            pid = p.id
        n = backfill(db, pid, days)
    typer.echo(f"backfilled {n} traces")


if __name__ == "__main__":  # pragma: no cover
    app()
