# Contributing

Thanks for helping make RAG systems more reliable.

```bash
git clone https://github.com/ArathiS1/ragops-observatory && cd ragops-observatory
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check ragops tests && pytest -q
ragops benchmark                    # must not regress (CI checks accuracy >= 85%, false alarms <= 8%)
ragops benchmark --dataset squad    # real-text benchmark; paste both summaries in classifier PRs
```

- **Web UI** (`web/`): `npm install && npm run dev` (proxies to the API on :8000); `npm run build` must pass.
- **TypeScript SDK** (`sdk-ts/`): `npm install && npm test`; `npm run test:integration` against a running server.
- **PostgreSQL tests**: set `RAGOPS_TEST_PG_URL` to a database with the pgvector extension to run them locally.
- **Changing the root-cause classifier?** Run `ragops benchmark` before and after and paste both summaries into the
  PR. Do not tune thresholds against the benchmark alone; add a new injected case if you fix a real-world miss.
- **New metric or judge?** Implement it in `ragops/eval/judges.py` for every judge, and add a unit test.
- **New integration?** Put it in `ragops/sdk/adapters.py` (lazy imports only) or `ragops/server/importers.py`.
- Keep the SDK dependency-light: it must never raise into the host application.
- Commits: small and focused; describe *why* in the PR. Add a CHANGELOG entry under "Unreleased".
