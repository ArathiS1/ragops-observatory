# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com) and [SemVer](https://semver.org).

## 0.10.1

- README images and links now use full URLs, so they show on PyPI.
- New project website; docs moved to /docs/.
- Release workflow: lowercase Docker image name, safe re-runs.

## 0.10.0

First public release.

- **Licence:** MIT (previously Apache-2.0).
- **Packaging:** PyPI metadata, classifiers and project links; the release workflow builds the dashboard into the
  wheel, checks the tag against the version, publishes with PyPI trusted publishing, creates a GitHub release from
  this changelog and pushes a multi-architecture Docker image to GHCR. npm publishing is opt-in.
- **Docs website** on GitHub Pages (`docs.yml`), a release guide (`docs/releasing.md`), a case study of testing a
  real RAG assistant (`docs/case-studies/my-rag.md`) and refreshed screenshots.
- **Community files:** code of conduct, support guide, citation file, CODEOWNERS and Dependabot.
- **Fix:** the `server` extra now installs `python-multipart`; without it `ragops serve` failed on a clean
  `pip install` (found by testing the built wheel in an empty environment). The wheel and sdist now include the
  built dashboard.
- **CLI:** a `--file` path that does not exist now says "config file not found" with the folder it looked in,
  instead of "no pipeline".

## 0.9.5

- Benchmark lab: when the selected runs have no question in common, cards say "on its own N questions (not
  comparable)" instead of "N shared questions", and the warning says which run to unselect.

## 0.9.4

- Benchmark lab scores every run on the questions all selected runs share (each card shows how many the run had), and
  picks no "best" run when they share none. A 6-question run could be marked best against 21-question runs.
- Drift: topic examples are questions that contain the topic's words (an example could belong to another topic);
  retrieval drift with higher scores is labelled "changed (scores up)" instead of "degraded".
- Cost: serving cost shows "not reported" when no request carries a cost; judge cost is labelled as an estimate.
- Reports no longer use the deprecated `datetime.utcnow()`.

## 0.9.3

- Dashboard times are shown in your local time zone everywhere (alerts on the Overview, run pickers on Assurance and
  Release guardian, decision history, first / last seen). Some new pages showed UTC, 5½ hours off in India.
- Overview: tokens and cost show "–" / "not reported" when the app never sends them, instead of 0 and $0.0000; a
  metric with no data in the previous period says so instead of "no change".

## 0.9.2

- Tests are isolated from the developer's shell: judge, provider, server and key variables (`RAGOPS_JUDGE*`,
  `RAG_LLM`, `RAGOPS_URL`, ...) are cleared for each test, so `pytest` no longer calls a paid LLM or fails when a real
  judge is configured.
- `pip install -e ".[dev]"` works on Windows: `chdb` (only used by the ClickHouse test) is skipped there.

## 0.9.1

- LLM judge: a reply without a `score` field (seen with Sarvam: `KeyError: 'score'`) is read from `rating`, `value`
  or per-statement verdicts before falling back to word matching, and the fallback reason names the keys received.
- `ragops demo` uses the free rule-based judge unless `--llm-judge` is given: the demo storyline is calibrated for it,
  and an LLM judge spent hundreds of paid calls on made-up traffic.

## 0.9.0

The dashboard now follows observe → investigate → fix → test → approve release → prove. No schema change.

- **Overview cockpit:** KPI cards with trend lines and change vs the previous window, pipeline flow with p50 / p95
  per stage, Hit@K donut, failure / quality / feedback panels, recent alerts, traffic by version, environment filter
  (`GET /cockpit`).
- **Evidence graph** on every trace: documents → passages → answer claims, supported / contradicted / no source,
  interactive (`GET /traces/{id}/evidence-graph`). Span timeline from per-stage timings.
- **Failure intelligence:** patterns with trend (new, growing, steady, shrinking), first / last seen, daily series,
  likely cause with confidence, closest and outdated documents, dominant version (`GET /failure-intelligence`).
- **Release guardian:** Pass / Review / Block against configurable gates (`release_gates` setting,
  `GET|PUT /release/gates`), case-by-case diff, approve / reject / override with reason and admin rule, decisions in
  the audit log (`/release/check`, `/release/decisions`).
- **Benchmark lab:** 2–5 runs side by side with best-run selection, charts and a disagreement grid
  (`GET /runs/benchmark`).
- **Integrations and security** page (`GET /integrations`).
- Dark theme (default) and light theme; traces show environment and cost; navigation regrouped by workflow.
- The demo seeds per-stage timings, expected documents and a staging environment.
- Docs: `docs/dashboard.md` with screenshots.

## 0.8.0

Reliability analytics, built into the existing platform (no schema migration; existing databases keep working).

- **Environments:** `environment` on traces, the Python client (`environment=` or `RAGOPS_ENVIRONMENT`), filters on
  traces and costs, `GET /environments`.
- **Cost:** versioned price table (`ragops.reliability`), estimated cost when an app sends tokens but no cost
  (recorded with `pricing_version`), per-project prices (`pricing` setting) or `RAGOPS_PRICING_FILE`;
  `GET /costs` by model, version, environment and day, serving vs. evaluation.
- **Ranking metrics:** Precision@K, Recall@K, MRR and nDCG when expected documents are labelled (traces and runs).
- **Claim-level evidence:** every answer split into claims marked supported, contradicted or not found, with the
  source passage; shown on the trace page and in run evidence.
- **Failure patterns:** `GET /failure-patterns` and a dashboard page grouping failures by stage and topic.
- **Incidents:** SLO alerts include related signals, recent changes, affected traces and checks to run first;
  `GET /incident`.
- **Release gate:** `--max-p95-ms`, `--max-latency-increase`, `--max-error-rate` budgets and `--junit` XML.
- **Security probes:** `ragops generate --security`; leaks fail as `security_leak` and add a blocking assurance check.
- **Data rights:** `GET /export` and `DELETE /data` (by session, by date, or all), admin only and audited.
- **Dashboard:** new Assurance (verdict for any finished run, printable report, JSON evidence), Cost and Failure
  patterns pages; claims table on traces. `GET /runs/{id}/assurance?format=json|html`.
- Docs: `docs/reliability.md`, `docs/feature-map.md`.

## 0.7.3

- Abstention is scored from the test's label when there is one: an expected answer means "should answer", an expected
  "I don't know" means "should decline". Word overlap with the corpus is used only for unlabelled traffic. Fixes
  correct answers to paraphrased questions failing with "wrong abstention behaviour", and correct refusals failing
  because the question shared words with the documents.
- Diagnosis no longer labels a failure `knowledge_gap` when the expected document reached the prompt.

## 0.7.2

- A correct answer no longer fails on wording: when a test case's answer matches the reference answer
  (answer correctness >= 0.8), low answer relevance is not a failure reason. Generated questions paraphrase the
  documents ("initial email reply timeframe" vs "first response"), which made right answers fail.
- `ragops assure` prints every failing question with the answer, the expected answer, the stage and the reason.

## 0.7.1

- `ragops assure -f path/to/ragops.yaml` works from any folder: `callable: my_rag:answer` and `corpus: docs/` are
  read relative to the yaml (fixes `ModuleNotFoundError: No module named 'my_rag'`); `--dataset` and `--out` stay
  relative to where you run the command.

## [0.7.0] - 2026-11-20
### Added: RAG assurance (stage 1 of the product roadmap, see docs/assurance.md)
- **`ragops generate`**: builds a test set from your own documents with your LLM: questions and short answers per
  passage plus questions the documents do not answer. Every case is verified against its source passage (answer
  supported, same numbers, not given away by the question, no duplicates; "unanswerable" questions the documents do
  answer are rejected). `--offline` makes template questions without an LLM for CI.
- **`ragops assure`**: assurance report (HTML + JSON evidence) with a verdict (Ready / Ready with conditions / Not
  ready) from explicit, configurable criteria; failures by stage in plain language with fixes and examples;
  documents to write; outdated documents; judge reliability; supporting-evidence mapping to RBI FREE-AI, EU AI Act
  Articles 15 and 72 and ISO/IEC 42001 clause 9; and a SHA-256 fingerprint checked by `ragops assure --verify`.
  Works from a fresh run or from `ragops test --json-out`.
- **Grounding guardrail**: `POST /v1/projects/{project}/guard` and `RagopsClient.guard()` check each statement of an
  answer against its passages before it is shown, and allow, rewrite to a safe fallback, block or flag it. Rules
  first (verbatim support with the same numbers and negations), the LLM judge optionally. The SDK fails open by
  default and can fail closed.
- `ragops test --json-out` also records the pipeline, dataset and judge, for `ragops assure --from-json`.
### Fixed
- **Word-for-word support missed multi-sentence chunks.** Chunks were lower-cased before sentence splitting, so a
  chunk stayed one span and a "not" in a different sentence vetoed a verbatim statement. Faithfulness shortcuts and
  the guard now split first (regression test added).

## [0.6.19] - 2026-11-19
### Fixed
- `send_traffic.py` stopped at the first question the RAG could not answer (an empty LLM reply). It now prints
  an ERROR line for that question, keeps sending the rest, and reports how many failed, like a real app keeps
  serving other users.

## [0.6.18] - 2026-11-19
### Fixed
- **False "retrieval miss" for apps that chunk documents their own way** (LlamaIndex, LangChain, Haystack...).
  Their chunk ids never equal the registered corpus ids, so a correctly retrieved chunk did not count as retrieved.
  A retrieved chunk of the same document that shares most of its content with the gold chunk now counts.
- `compare.py` showed the previous run's numbers when a run failed; it now shows "error".
### Added
- `send_traffic.py` (doctor and frameworks examples) registers the documents itself, so production diagnosis can
  tell "no document covers this" from "the search missed it".
- Framework examples retry once when the LLM returns an empty answer (Sarvam 105B sometimes reasons until the
  token budget runs out).
- `docs/field-report.md`: one day of testing real RAG pipelines, the seven judge failure modes found, the SLO
  incident, the release gate results and the framework comparison, with real numbers.

## [0.6.17] - 2026-11-19
### Fixed
- An answer that contains the whole reference answer word for word, with extra detail from the documents
  ("Acme bills monthly in arrears on the first day of each month. Invoices are emailed...") now scores 1.0 for
  answer correctness without a model call; Sarvam had scored such answers 0.
- Framework examples allow `max_tokens: 8192`: Sarvam 105B sometimes spent the whole 4096-token budget thinking
  about questions the documents do not cover and returned no answer.

## [0.6.16] - 2026-11-19
### Fixed
- **"I don’t know" with a typographic apostrophe was not recognised as declining**, so correct refusals
  (Tokyo office, student discount) failed answer correctness, relevance and faithfulness. Models such as Sarvam
  write ’; abstention detection now accepts it.
- **Faithfulness 0.00 on answers copied from the context.** A statement found nearly word for word in one context
  sentence (same numbers, same negations) is supported without asking the model; paraphrases still go to the
  judge. This removes most judge mistakes on extractive answers and saves judge calls.
- `ragops judge check` still reports "not working" when the model never answers, even though some checks are now
  settled without it.
### Changed
- `ragops test` prints the RAGOps version next to the judge, so a stale install is visible at a glance.

## [0.6.15] - 2026-11-19
### Added
- **`examples/frameworks/`: the same RAG built with LlamaIndex, LangChain, LangGraph and Haystack**, using the
  same documents, test set, LLM (Sarvam, Groq, Gemini, OpenAI or Ollama) and local embeddings (fastembed
  bge-small), so `ragops test`, `doctor`, `gate` and the dashboard compare the frameworks themselves.
  `compare.py` runs all four into one table; `send_traffic.py <framework>` sends live traffic to one project per
  framework for the Organisation page.

## [0.6.14] - 2026-11-19
### Fixed
- **`ragops gate` ignored your LLM judge** and always scored with word matching, so it could disagree with
  `ragops test` (7 baseline failures in the gate, 1 in the test, on the same cases). The gate now uses the same
  judge (`RAGOPS_JUDGE*`, or `--judge`) and the same corpus (`ragops.yaml`) as `ragops test`, prints which judge
  it used, and also gates `answer_correctness` when the golden cases have expected answers.
- An answer that states the reference word for word ("20 requests per second" vs "20 requests per second.")
  scores 1.0 for answer correctness without asking the model.

## [0.6.13] - 2026-11-19
### Fixed
- `ragops gate --golden rag_tests.jsonl` crashed with `JSONDecodeError: Extra data`: the gate only read JSON/YAML.
  It now reads JSONL, JSON, YAML and CSV, the same files `ragops test` reads.

## [0.6.12] - 2026-11-19
### Fixed
- **Faithfulness 0.00 on correct answers with Sarvam** ("The context states that refunds are available within
  30 days", score 0). The judge was splitting the CONTEXT into claims and marking facts the answer left out as
  unsupported. RAGOps now splits the ANSWER into numbered statements itself and asks for one verdict per
  statement; verdicts written as "yes"/"supported"/1 are read correctly, and an "I don't know" answer is
  faithful without a model call.
### Added
- `ragops rejudge`: judge a project's failed traces (or `--all`) again with the current judge, so an upgrade
  does not need a new project and fresh traffic.

## [0.6.11] - 2026-11-19
### Fixed
- **Judge scores that contradicted their own explanation** ("directly supported by the context", score 0.00).
  Faithfulness now asks for a supported/unsupported verdict per claim and RAGOps counts them, so the score
  always matches what the judge found. Other metrics ask for the reason before the score.
- **Answer relevance no longer punishes short, complete answers** for leaving out details the question did not
  ask for ("The Free plan includes 3 projects." scored 0).
- Scores returned on a 0-10 or 0-100 scale are rescaled instead of clipped to 1.

## [0.6.10] - 2026-11-19
### Fixed
- **Faithfulness no longer sees the reference answer.** Test cases sent the expected answer to every judge prompt,
  so judges (notably Sarvam 105B) marked short but correct answers ("Every 6 hours.") as unfaithful because they
  were less complete than the reference. The reference now goes only to `answer_correctness` and `context_recall`.
- **A correct "I don't know" scores 1.0 for answer correctness without a model call** when the reference also
  declines (the Tokyo-office case was failing on a judge mistake).
- **Broken judge JSON** (unescaped quotes, cut-off reason) no longer falls back to word matching when the score
  itself is readable.

## [0.6.9] - 2026-11-18
### Added
- **Reasoning models as the judge (Sarvam 105B, Qwen, DeepSeek).** The judge ignores `<think>...</think>` blocks
  before reading the JSON score, and `RAGOPS_JUDGE_MAX_TOKENS` gives the model room to think. An empty reply now
  fails with a clear reason (`finish_reason`) instead of a confusing JSON error.

## [0.6.8] - 2026-11-18
### Fixed
- **A correct "I don't know" could be counted as a mistake.** The abstention check treated a question as covered
  by the corpus at relevance >= 0.18 while the diagnosis called anything < 0.30 a knowledge gap; between the two the
  same request was "uncovered" and "wrongly refused". Both now use 0.30. Benchmarks: synthetic false alarms
  3.6% -> 1.8% and knowledge-gap precision 54% -> 64% (accuracy unchanged at 89.6%); SQuAD false alarms
  21.7% -> 22.3% (accuracy unchanged at 73.8%).
- **Answer-relevance SLOs no longer count correct refusals as bad requests** (they score 0 relevance by definition),
  matching the failure rule.
- A correct refusal is no longer counted under "Where the pipeline breaks" (failures only), but it is still recorded
  as a knowledge gap, so the missing content stays on the knowledge-base to-do list ("Write these next").
- Benchmarks count an alarm only when a request is flagged as a failure.
- The demo bot now sometimes guesses on questions it cannot answer, so the demo shows a real knowledge-gap failure.

## [0.6.7] - 2026-11-18
### Fixed
- **Ingestion failed with HTTP 500 ("database is locked") while the worker was judging on SQLite.** Trace
  evaluation kept its write transaction open during LLM judge calls, holding SQLite's database-wide write lock for
  seconds or minutes. The worker now commits before judging and writes results in a short second transaction.
### Added
- `examples/doctor/send_traffic.py` retries briefly on server errors.

## [0.6.6] - 2026-11-18
### Added
- `ragops test` prints which judge scored the run, and warns (with the reason) when an LLM judge fell back to word
  matching, so a weak report caused by the judge is never mistaken for a weak pipeline.

## [0.6.5] - 2026-11-18
### Fixed
- **The LLM judge now waits and retries on rate limits (429) and temporary server errors** (honouring
  Retry-After, exponential back-off, `RAGOPS_JUDGE_RETRIES`, default 6). Before, a worker evaluating a burst of traces
  on a free tier (Gemini, Groq) silently fell back to the heuristic judge for most of them.
- `ragops test --push` explains a missing server, a rejected key or a missing project in one line instead of a
  traceback.

## [0.6.4] - 2026-11-18
### Fixed
- With an LLM judge, **answer correctness is now judged by meaning**: a correct paraphrase of the expected answer
  scores full marks instead of failing on word overlap. The heuristic judge still compares key terms and numbers.
### Added
- Test reports show what the pipeline answered and what was expected for every failed case.

## [0.6.3] - 2026-11-17
### Added
- `RAGOPS_JUDGE_API_KEY`: the LLM judge can use a different provider's key than your application (e.g. Gemini).
### Fixed
- When the LLM judge fails, the HTTP status and the provider's message are recorded, and `ragops judge check` prints
  "Why it fell back: …" instead of only "check the URL, model name and key".
- The LLM judge accepts JSON replies wrapped in Markdown fences or surrounded by text (Gemini, some Ollama models)
  and retries without `response_format` for providers that do not support JSON mode.

## [0.6.2] - 2026-11-17
### Fixed
- HTTP pipelines that cannot be reached now fail with "could not connect to <url>: is your RAG server running on
  that address?" instead of a raw `ConnectError`, and timeouts say which setting to raise.
- `ragops test` / `ragops doctor` named the `callable:` in reports even when `kind: http` was in effect; reports now
  name what is actually called.
- The doctor no longer suggests passing `--corpus` when every case crashed.

## [0.6.1] - 2026-11-17
### Fixed
- Windows: `ragops doctor`, `ragops test`, `ragops report`, `ragops init` and the gate crashed with
  `UnicodeEncodeError: 'charmap' codec can't encode character` because files were written in the system code page
  (cp1252), which cannot hold the reports' emoji. All files are now read and written as UTF-8, and console output
  switches to UTF-8 on Windows (and never crashes on other non-UTF-8 consoles).

## [0.6.0] - 2026-11-16
### Added
- **RAG doctor** (`ragops doctor`, UI page, `/doctor` endpoints): a verdict on whether your RAG works, its mistakes
  grouped by cause with real examples, how to confirm and fix each, and **treatment trials** that re-run your pipeline
  with candidate config changes and recommend only fixes verified by a paired comparison (fewer mistakes, more fixed
  than broken, no new errors, no significant metric regression). Combined fixes are tested too; `--write-config`
  writes the fixed config. Server-side trials for registered HTTP pipelines run in the worker. Walkthrough in
  `examples/doctor/`.
- **Answer correctness** metric against the reference answer (number-aware, abstention-aware), and a new
  *right document, wrong passage* (`incomplete_context`) diagnosis for test runs.
- **Organisations** above projects: org-scoped keys and roles, org overview of every project's health, moving projects.
- **Single sign-on (OIDC)**: authorization code + PKCE, JWKS-verified ID tokens, group → role mapping, allowed e-mail
  domains, revocable session tokens; "Sign in with …" in the UI.
- **Alembic migrations** with automatic upgrade on start, in-place upgrades of v0.4/v0.5 databases (JSON embeddings
  converted to pgvector), `ragops db upgrade|current|history`, Helm pre-upgrade migration Job.
- **Arq + Redis** job dispatch with a Postgres transactional outbox and periodic sweep (no lost jobs on Redis restart).
- **OTLP/HTTP protobuf** (and gzip) on `/otlp/v1/traces`.
- **Native PDF** reliability report (`format=pdf`, `ragops report --format pdf`, UI download).
- **Ragas 0.4** judge rewrite, `ragops judge check` (agreement and Cohen's kappa on labelled cases, exits 1 when the
  judge is not working) and a deterministic OpenAI-compatible stub server for offline tests.
- **Evidently 0.7** drift report (columns + query-embedding drift) at `/drift/evidently` and in the UI.
- **Microsoft Presidio** redaction (`RAGOPS_REDACTOR=presidio`): names and locations in addition to the regex
  entities; runs offline.
- Alerts: **Microsoft Teams**, Slack Block Kit messages, PagerDuty `dedup_key`/links, SMTP `starttls|ssl|none`,
  `ragops alerts test` and `POST /alert-channels/{id}/test` that report the receiver's exact answer.
- Trace ingestion accepts common field names from other stacks (`question`, `response`, `chunks`, `page_content`,
  `ground_truth`, plain-string chunks).
- Docker: overridable base images, `EXTRAS` build arg, venv, `/data` volume. Compose: Redis, SSO and SMTP settings.
  Helm: Redis, SSO, SMTP, Presidio, security contexts, `helm test`, NOTES.
### Fixed
- Context recall now checks the reference answer's facts, not only whether the expected document was retrieved, so
  a fragment of the right document no longer scores 1.0.
- A correct "I don't know" on a question the corpus does not cover is no longer counted as a failure.
- Trace fields with unrecognised names were silently dropped; the common aliases above are now mapped.

## [0.5.0] - 2026-11-13
### Added
- **Test your own RAG**: register any RAG system's HTTP endpoint (any language) with JSON field mapping, check one
  question, run golden sets or pasted questions from the UI/API (executed by the worker with live progress), and
  compare runs with paired-bootstrap intervals. `ragops init`, `ragops test` (thresholds for CI, `--push` to the
  server), `ragops corpus push`. Adapters for HTTP, Python callables, LangChain, LlamaIndex and Haystack 2.x.
  SSRF protection for server-side calls (`RAGOPS_PIPELINE_ALLOWED_HOSTS`). Built-in demo endpoint.
- **React web UI** at `/ui` (Vite + TypeScript + Recharts): overview with the pipeline schematic, root causes,
  traces with evidence, test your RAG, golden sets, calibration, drift, knowledge base, SLOs and alerts, report,
  settings with key management and audit log. Built into the Docker image.
- **TypeScript SDK** (`sdk-ts`, `@ragops/sdk`): tracing with per-step timing, `observe()`, batching, retries,
  feedback, corpus, pipelines and runs. Unit and live-server integration tests.
- **pgvector** storage: `vector(N)` columns with HNSW cosine indexes, index-backed corpus search
  (`/corpus/search`) and index-candidate diagnosis for corpora above `RAGOPS_INMEMORY_CORPUS_LIMIT`.
- **ClickHouse** analytics mirror with async inserts, `/analytics` endpoint and `ragops clickhouse init|backfill`;
  SQL verified against the embedded ClickHouse engine (chdb) in tests.
- **Public benchmark on SQuAD 2.0** (`ragops benchmark --dataset squad`) and a **case study** on a real RAG service.
- Per-step SDK context managers, Haystack logging adapter, draft reference answers extracted for golden cases.
- GitHub issue templates (bug, wrong diagnosis, feature) and a PR template.
### Fixed (found by the SQuAD benchmark and the case study)
- Citations containing punctuation, e.g. `[Sky_(United_Kingdom)]`, were scored as unsupported claims.
- Re-chunking a corpus created new document versions and made past answers look outdated.
- A perfectly constant metric produced a NaN p-value and crashed the drift endpoint.
- Chunking defects are now recognised by a chunk boundary that cuts the best-matching sentence (recall 15% → 42%).
- An outdated chunk only marks an answer stale when the answer cites or uses it; otherwise it is a warning.
- Quality change points are linked to deploys made later on the following day.

## [0.4.0] - 2026-11-13
### Added
- Stage-level root-cause classifier (7 stages) with evidence, plus a reproducible failure-injection benchmark.
- Four-layer drift detection: query (domain classifier + MMD + uncovered-topic discovery), retrieval, corpus, quality
  (change-point detection linked to the deploy that likely caused it).
- Knowledge-base health: coverage gaps, hot chunks, never-retrieved chunks, duplicates and contradictions.
- Failure → golden set loop (manual and automatic), versioned and de-duplicated.
- `ragops gate` CLI and GitHub Action: paired-bootstrap release gate with per-stage PR comment.
- Online evaluation with daily judge budget and stratified sampling; judge calibration with Cohen's κ.
- RAG SLOs with error budgets, burn rate and alerts to Slack, webhooks, PagerDuty and e-mail.
- PII redaction, hash-only storage mode, retention, project API keys with roles, audit log.
- OTLP/OpenInference receiver, Langfuse and Phoenix importers, Python SDK, LangChain and LlamaIndex adapters.
- Durable DB-backed job queue and worker; Streamlit dashboard; weekly reliability report (Markdown/HTML).
- Docker Compose, Helm chart, CI, release workflow.
