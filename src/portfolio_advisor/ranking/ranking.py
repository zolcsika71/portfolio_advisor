"""Combine eligibility and scores into a stable deterministic ranking."""

from __future__ import annotations

from dataclasses import replace

from portfolio_advisor.metrics.models import PortfolioMetrics

from .eligibility import evaluate_eligibility
from .models import CandidateEvaluation, RankingRules
from .scoring import calculate_contributions


def rank_portfolios(
    candidates: list[PortfolioMetrics], rules: RankingRules
) -> tuple[list[CandidateEvaluation], tuple[str, ...]]:
    """Rank eligible candidates by score, then portfolio name; retain rejections."""
    preliminary = [
        CandidateEvaluation(
            metrics=candidate,
            eligible=not (reasons := evaluate_eligibility(candidate, rules.eligibility)),
            rejection_reasons=reasons,
        )
        for candidate in candidates
    ]
    eligible_metrics = [item.metrics for item in preliminary if item.eligible]
    contributions = calculate_contributions(eligible_metrics, rules.metrics)
    applied = {
        contribution.metric
        for values in contributions.values()
        for contribution in values
    }
    warnings = tuple(
        f"Scoring metric {name} was unavailable for at least one eligible portfolio and was excluded."
        for name in sorted(set(rules.metrics) - applied)
    )
    scored = [
        replace(
            item,
            contributions=contributions[item.metrics.portfolio_name],
            total_score=sum(value.contribution for value in contributions[item.metrics.portfolio_name]),
        )
        if item.eligible
        else item
        for item in preliminary
    ]
    ordered_eligible = sorted(
        (item for item in scored if item.eligible),
        key=lambda item: (-(item.total_score or 0.0), item.metrics.portfolio_name),
    )
    ranked = [replace(item, rank=index) for index, item in enumerate(ordered_eligible, start=1)]
    rejected = sorted(
        (item for item in scored if not item.eligible), key=lambda item: item.metrics.portfolio_name
    )
    return [*ranked, *rejected], warnings
