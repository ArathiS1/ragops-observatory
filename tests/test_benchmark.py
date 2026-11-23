from ragops.benchmark import run_benchmark, to_markdown


def test_benchmark_regression_guard():
    r = run_benchmark()
    # guard rails: a change that makes attribution worse than the published baseline fails CI
    assert r["attribution_accuracy"] >= 0.85
    assert r["false_alarm_rate"] <= 0.08
    assert all(r["per_label"][lab]["recall"] >= 0.7 for lab in r["per_label"] if lab != "ok")
    md = to_markdown(r)
    assert "Confusion matrix" in md
