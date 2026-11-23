from datetime import date, timedelta

import numpy as np

from ragops.drift import change_point, cusum, query_drift, retrieval_drift
from ragops.embeddings import embed
from ragops.eval.calibration import calibrate, cohen_kappa
from ragops.eval.sampler import decide, stratum_for
from ragops.redaction import hash_text, redact
from ragops.security import generate_key, role_allows, verify
from ragops.slo import evaluate_slo


def test_redaction():
    text, counts = redact("mail a.b@c.io card 4111 1111 1111 1111 ssn 123-45-6789 ip 10.0.0.1")
    assert "a.b@c.io" not in text and "4111" not in text and "123-45-6789" not in text and "10.0.0.1" not in text
    assert counts["EMAIL"] == 1 and counts["CREDIT_CARD"] == 1
    assert hash_text("x", "s1") != hash_text("x", "s2")


def test_keys_and_roles():
    raw, prefix, h = generate_key()
    assert raw.startswith("rgo_") and prefix == raw[:12] and verify(raw, h) and not verify(raw + "x", h)
    assert role_allows("ingest", "ingest") and not role_allows("ingest", "viewer")
    assert role_allows("admin", "editor") and not role_allows("viewer", "editor")


def test_sampler_budget_and_feedback():
    s = stratum_for("v1", 0.8, 3, None)
    assert decide("t", s, None, 1.0, 1.0, 1.0, 0.1, {}).evaluate is False  # budget cap is hard
    assert decide("t", s, -1, 0.0, 10, 0, 0.1, {}).evaluate is True  # negative feedback always
    assert decide("t", s, None, 0.0, 10, 0, 0.1, {}).evaluate is False
    # deterministic per trace id
    assert decide("abc", s, None, 0.5, 10, 0, 0, {}).evaluate == decide("abc", s, None, 0.5, 10, 0, 0, {}).evaluate
    # under-represented strata are boosted
    rare = decide("abc", "x|empty|none", None, 0.1, 10, 0, 0, {"x|high|none": 50, "x|empty|none": 0})
    assert "p=0.50" in rare.reason  # 0.1 base rate boosted x5 for the starved stratum


def test_kappa_and_calibration():
    assert cohen_kappa([True, False] * 10, [True, False] * 10) == 1.0
    assert abs(cohen_kappa([True, True, False, False], [True, False, True, False])) < 1e-9
    r = calibrate("faithfulness", [(0.9, True)] * 8 + [(0.2, False)] * 8 + [(0.9, False)] * 2, 0.7)
    assert r.n == 18 and r.fp == 2 and r.kappa > 0.7


def test_slo_error_budget():
    ok = evaluate_slo("s", "m", 0.7, 0.9, 7, [0.9] * 95 + [0.1] * 5, [0.9] * 10)
    assert ok.status == "ok" and 0 < ok.budget_remaining < 1
    bad = evaluate_slo("s", "m", 0.7, 0.9, 7, [0.9] * 70 + [0.1] * 30, [0.1] * 10)
    assert bad.status == "breached" and bad.burn_rate_24h > 1
    assert evaluate_slo("s", "m", 0.7, 0.9, 7, [], []).status == "no_data"


def test_cusum_and_change_point():
    vals = np.r_[np.full(10, 0.9), np.full(6, 0.6)] + np.random.default_rng(0).normal(0, 0.01, 16)
    assert cusum(vals, baseline_n=7) is not None
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(16)]
    cp = change_point("faithfulness", days, list(vals))
    assert cp.detected and cp.direction == "degraded" and cp.change_date == "2026-01-11"
    flat = change_point("x", days, [0.8] * 16)
    assert not flat.detected


def test_query_drift_new_uncovered_topic():
    ref_q = [f"How do I reset my password number {i}" for i in range(40)] + [f"refund for annual plan {i}" for i in range(40)]
    cur_q = ref_q[:30] + [f"does the mobile app work offline on android {i}" for i in range(20)]
    cov = [0.6] * 30 + [0.1] * 20
    r = query_drift(embed(ref_q), embed(cur_q), cur_q, cov)
    assert r.drift_detected and any(t.covered is False and "mobile" in t.label for t in r.new_topics)
    assert r.uncovered_share > 0.3


def test_retrieval_drift_detects_score_drop():
    ref = [{"query": f"q{i}", "top1": 0.8, "n": 3, "chunk_ids": ["a", "b"]} for i in range(50)]
    cur = [{"query": f"q{i}", "top1": 0.4, "n": 3, "chunk_ids": ["c", "d"]} for i in range(50)]
    r = retrieval_drift(ref, cur)
    assert r.drift_detected and r.topk_overlap == 0.0


def test_change_point_constant_metric_is_json_safe():
    import json

    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(10)]
    cp = change_point("faithfulness", days, [1.0] * 10, [[1.0, 1.0]] * 10)
    assert not cp.detected and cp.p_value == 1.0
    json.dumps(cp.to_dict(), allow_nan=False)
