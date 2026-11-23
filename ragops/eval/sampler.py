"""Online evaluation sampler with a daily cost budget and stratified sampling.

Rules, in order:
1. Negative user feedback is always evaluated (if the budget allows).
2. The daily judge budget is a hard cap.
3. Otherwise sample at ``sample_rate``, boosted for strata that are under-represented today, so rare
   query types (e.g. a new config version or empty retrievals) are not drowned out by common traffic.
Decisions are deterministic per trace id, so re-processing gives the same result.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class SampleDecision:
    evaluate: bool
    reason: str
    stratum: str


def stratum_for(config_label: str | None, top_score: float | None, n_chunks: int, feedback: float | None) -> str:
    if n_chunks == 0:
        bucket = "empty"
    elif top_score is None:
        bucket = "unscored"
    else:
        bucket = "low" if top_score < 0.3 else "mid" if top_score < 0.6 else "high"
    fb = "neg" if feedback is not None and feedback < 0 else "pos" if feedback and feedback > 0 else "none"
    return f"{config_label or 'default'}|{bucket}|{fb}"


def _unit(trace_id: str) -> float:
    return int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def decide(
    trace_id: str,
    stratum: str,
    feedback: float | None,
    sample_rate: float,
    budget_usd: float,
    spent_today_usd: float,
    est_cost_usd: float,
    evaluated_today_by_stratum: dict[str, int],
    always_eval_negative: bool = True,
) -> SampleDecision:
    if spent_today_usd + est_cost_usd > budget_usd and est_cost_usd > 0:
        return SampleDecision(False, f"daily budget reached (${spent_today_usd:.2f}/${budget_usd:.2f})", stratum)
    if always_eval_negative and feedback is not None and feedback < 0:
        return SampleDecision(True, "negative user feedback", stratum)
    counts = evaluated_today_by_stratum
    mean = (sum(counts.values()) / len(counts)) if counts else 0.0
    mine = counts.get(stratum, 0)
    boost = 1.0 if mean == 0 else max(1.0, min(5.0, (mean + 1) / (mine + 1)))
    p = min(1.0, sample_rate * boost)
    if _unit(trace_id) < p:
        why = f"sampled (p={p:.2f}" + (f", stratum boost x{boost:.1f})" if boost > 1 else ")")
        return SampleDecision(True, why, stratum)
    return SampleDecision(False, f"not sampled (p={p:.2f})", stratum)
