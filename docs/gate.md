# Release gate

Your pipeline is any callable `fn(query: str, config: dict) -> {"answer", "contexts", "retrieved"?}`. The gate
replays each golden case through the baseline and candidate configs, scores both, and computes the **paired
difference** per metric. A metric **blocks** the release only when the mean drop is larger than `--max-drop`
(default 0.02) **and** the 95% paired-bootstrap interval lies entirely below zero.

```bash
ragops gate --pipeline my_app.rag:pipeline --baseline prod.yaml --candidate new.yaml \
  --server $RAGOPS_URL --api-key $RAGOPS_API_KEY --project support-bot --golden-set production-failures \
  --out gate.md --json-out gate.json      # exit code 0 = pass, 1 = blocked
```

The report shows metric deltas with intervals, expected-document hit rate, failures **per pipeline stage** for
both configs, and newly failing / newly fixed cases. The GitHub Action (`action.yml`) posts it as a PR comment and
to the job summary. Pass `--corpus` (docs file or `module:ATTR`) to enable stage diagnosis in the report.
