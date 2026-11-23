

def test_gate_cli_accepts_jsonl_golden_file(tmp_path, monkeypatch):
    """`ragops gate --golden rag_tests.jsonl` must read the same JSONL file `ragops test` uses."""
    import json

    from typer.testing import CliRunner

    from ragops.cli import app

    (tmp_path / "golden.jsonl").write_text("\n".join(json.dumps(c) for c in [
        {"query": "How often are backups taken?", "expected_answer": "Every 6 hours.", "expected_doc_ids": ["data"]},
        {"query": "Refund window?", "expected_answer": "30 days.", "expected_doc_ids": ["billing"]},
    ]), encoding="utf-8")
    (tmp_path / "pipe.py").write_text(
        "DOCS = {'data': 'Backups are taken every 6 hours.', 'billing': 'Refunds within 30 days.'}\n"
        "def answer(q, config):\n"
        "    docs = list(DOCS.items())[: config.get('top_k', 2)]\n"
        "    return {'answer': docs[0][1], 'contexts': [{'text': t, 'doc_id': d} for d, t in docs]}\n",
        encoding="utf-8")
    (tmp_path / "base.yaml").write_text("top_k: 2\n", encoding="utf-8")
    (tmp_path / "cand.yaml").write_text("top_k: 2\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAGOPS_JUDGE", "heuristic")
    r = CliRunner().invoke(app, ["gate", "--pipeline", "pipe:answer", "--baseline", "base.yaml",
                                 "--candidate", "cand.yaml", "--golden", "golden.jsonl"])
    assert r.exception is None or isinstance(r.exception, SystemExit), r.output
    assert "golden cases" in r.output


def test_gate_cli_uses_configured_judge(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import ragops.eval.judges as judges
    from ragops.cli import app

    used = {}
    real = judges.get_judge

    def spy(name=None):
        used["name"] = name
        return real("heuristic")

    monkeypatch.setattr(judges, "get_judge", spy)
    (tmp_path / "g.jsonl").write_text('{"query": "q", "expected_answer": "a"}\n', encoding="utf-8")
    (tmp_path / "pipe.py").write_text("def answer(q, config):\n    return {'answer': 'a', 'contexts': [{'text': 'a'}]}\n",
                                      encoding="utf-8")
    (tmp_path / "b.yaml").write_text("top_k: 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["gate", "--pipeline", "pipe:answer", "--baseline", "b.yaml", "--candidate", "b.yaml",
                                 "--golden", "g.jsonl", "--judge", "openai"])
    assert used.get("name") == "openai", r.output
    assert "Judge:" in r.output
