"""RAG service-level objectives and error budgets.

Example SLO: "faithfulness >= 0.85 on 95% of sampled traffic over 7 days".
    good sample    = score >= threshold
    error budget   = (1 - target) * n_samples   bad samples allowed in the window
    budget left    = 1 - bad / error_budget
    burn rate      = bad_rate_last_24h / (1 - target)    (>1 means the budget will run out early)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class SLOStatus:
    name: str
    metric: str
    threshold: float
    target: float
    window_days: int
    n: int
    good_ratio: float | None
    budget_remaining: float | None
    burn_rate_24h: float | None
    status: str  # ok | at_risk | breached | no_data

    def to_dict(self):
        return asdict(self)


def evaluate_slo(name: str, metric: str, threshold: float, target: float, window_days: int,
                 window_scores: list[float], last_24h_scores: list[float], min_samples: int = 20) -> SLOStatus:
    n = len(window_scores)
    if n < min_samples:
        return SLOStatus(name, metric, threshold, target, window_days, n, None, None, None, "no_data")
    bad = sum(s < threshold for s in window_scores)
    good_ratio = 1 - bad / n
    allowed = max(1e-9, (1 - target) * n)
    remaining = 1 - bad / allowed
    burn = None
    if last_24h_scores:
        bad24 = sum(s < threshold for s in last_24h_scores) / len(last_24h_scores)
        burn = bad24 / max(1e-9, 1 - target)
    if good_ratio < target:
        status = "breached"
    elif remaining < 0.25 or (burn is not None and burn > 2):
        status = "at_risk"
    else:
        status = "ok"
    return SLOStatus(name, metric, threshold, target, window_days, n, round(good_ratio, 4),
                     round(max(remaining, -9.99), 4), None if burn is None else round(burn, 2), status)
