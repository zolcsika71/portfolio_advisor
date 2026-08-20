"""Metric-level scoring with no implicit weights or missing-value substitution."""

from __future__ import annotations

from math import isfinite

from portfolio_advisor.metrics.models import PortfolioMetrics

from .models import MetricRule, ScoreContribution
from .normalization import normalize_metric


def calculate_contributions(
    candidates: list[PortfolioMetrics], rules: dict[str, MetricRule]
) -> dict[str, tuple[ScoreContribution, ...]]:
    """Calculate normalized, weighted contributions for each supplied candidate.

    A scoring metric unavailable for *any* candidate is excluded from every
    candidate and must be surfaced by the caller as a warning. This avoids
    treating unavailable information as favourable or unfavourable.
    """
    contributions: dict[str, list[ScoreContribution]] = {
        candidate.portfolio_name: [] for candidate in candidates
    }
    for name, rule in rules.items():
        raw: dict[str, float] = {}
        complete = True
        for candidate in candidates:
            metric = getattr(candidate, name, None)
            if (
                metric is None
                or not metric.available
                or metric.value is None
                or not isfinite(metric.value)
            ):
                complete = False
                break
            raw[candidate.portfolio_name] = metric.value
        if not complete:
            continue
        normalized = normalize_metric(raw, rule.direction)
        for candidate in candidates:
            value = raw[candidate.portfolio_name]
            score = normalized[candidate.portfolio_name]
            if not isfinite(score) or not isfinite(score * rule.weight):
                raise ValueError(f"non-finite normalized score for {name}")
            contributions[candidate.portfolio_name].append(
                ScoreContribution(name, value, score, rule.weight, score * rule.weight)
            )
    return {name: tuple(items) for name, items in contributions.items()}
