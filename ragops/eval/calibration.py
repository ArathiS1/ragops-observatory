"""Judge calibration: how far can you trust the automated judge?

Compares judge pass/fail (score >= threshold) with human labels and reports Cohen's kappa.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CalibrationResult:
    metric: str
    n: int
    agreement: float | None
    kappa: float | None
    tp: int
    tn: int
    fp: int  # judge pass, human fail  (judge too lenient)
    fn: int  # judge fail, human pass  (judge too strict)
    verdict: str


def cohen_kappa(judge: list[bool], human: list[bool]) -> float | None:
    n = len(judge)
    if n == 0:
        return None
    po = sum(j == h for j, h in zip(judge, human)) / n
    pj, ph = sum(judge) / n, sum(human) / n
    pe = pj * ph + (1 - pj) * (1 - ph)
    if pe == 1:
        return 1.0 if po == 1 else 0.0
    return (po - pe) / (1 - pe)


def interpret(kappa: float | None, n: int) -> str:
    if kappa is None or n < 10:
        return "not enough labels (need at least 10)"
    if kappa >= 0.8:
        return "almost perfect: judge scores are trustworthy"
    if kappa >= 0.6:
        return "substantial: trustworthy for trends, spot-check individual failures"
    if kappa >= 0.4:
        return "moderate: use for trends only"
    return "poor: do not gate releases on this judge"


def calibrate(metric: str, pairs: list[tuple[float, bool]], threshold: float) -> CalibrationResult:
    judge = [s >= threshold for s, _ in pairs]
    human = [h for _, h in pairs]
    tp = sum(j and h for j, h in zip(judge, human))
    tn = sum((not j) and (not h) for j, h in zip(judge, human))
    fp = sum(j and not h for j, h in zip(judge, human))
    fn = sum((not j) and h for j, h in zip(judge, human))
    n = len(pairs)
    kappa = cohen_kappa(judge, human)
    return CalibrationResult(
        metric, n, (tp + tn) / n if n else None, None if kappa is None else round(kappa, 3),
        tp, tn, fp, fn, interpret(kappa, n),
    )
