import json

import numpy as np
from typer.testing import CliRunner

from ragops.cli import app
from ragops.demo.corpus import DOCS
from ragops.demo.pipeline import pipeline
from ragops.gate import paired_bootstrap, run_gate

CASES = [{"query": q, "expected_doc_ids": [d["doc_id"]]} for d in DOCS for q in d["questions"]]
BASE = {"rerank": "good", "context_k": 3}


def test_gate_blocks_regression():
    rep = run_gate(CASES, pipeline, BASE, {"rerank": "bad", "context_k": 2})
    assert not rep.passed and rep.reasons
    assert rep.hit_rate_candidate < rep.hit_rate_baseline
    assert rep.stage_failures_candidate.get("ranking_failure", 0) > rep.stage_failures_baseline.get("ranking_failure", 0)
    assert "BLOCKED" in rep.to_markdown()


def test_gate_passes_identical_config():
    rep = run_gate(CASES[:20], pipeline, BASE, dict(BASE))
    assert rep.passed and all(m.status == "unchanged" for m in rep.metrics)


def test_bootstrap_ci():
    lo, hi = paired_bootstrap(np.full(30, -0.2))
    assert abs(lo + 0.2) < 1e-9 and abs(hi + 0.2) < 1e-9
    lo, hi = paired_bootstrap(np.r_[np.full(15, 0.1), np.full(15, -0.1)])
    assert lo < 0 < hi


def test_cli_gate_exit_codes(tmp_path):
    (tmp_path / "golden.json").write_text(json.dumps({"cases": CASES}), encoding="utf-8")
    (tmp_path / "base.json").write_text(json.dumps(BASE), encoding="utf-8")
    (tmp_path / "bad.json").write_text(json.dumps({"rerank": "bad", "context_k": 2}), encoding="utf-8")
    args = ["gate", "--pipeline", "ragops.demo.pipeline:pipeline", "--golden", str(tmp_path / "golden.json"),
            "--baseline", str(tmp_path / "base.json"), "--corpus", "ragops.demo.corpus:DOCS"]
    r = CliRunner().invoke(app, args + ["--candidate", str(tmp_path / "bad.json"), "--out", str(tmp_path / "pr.md")])
    assert r.exit_code == 1 and "BLOCKED" in (tmp_path / "pr.md").read_text(encoding="utf-8")
    r = CliRunner().invoke(app, args + ["--candidate", str(tmp_path / "base.json")])
    assert r.exit_code == 0


def test_cli_benchmark_and_version(tmp_path):
    r = CliRunner().invoke(app, ["benchmark", "--out", str(tmp_path / "R.md")])
    assert r.exit_code == 0 and (tmp_path / "R.json").exists()
    assert CliRunner().invoke(app, ["version"]).exit_code == 0
