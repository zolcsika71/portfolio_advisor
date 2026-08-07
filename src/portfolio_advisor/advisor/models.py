"""Structured, serializable output from the deterministic advisor workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from portfolio_advisor.metrics.models import PortfolioMetrics
from portfolio_advisor.ranking.models import CandidateEvaluation


@dataclass(frozen=True, slots=True)
class AdvisorResult:
    """Complete outcome, including selection, alternatives, and warnings."""

    observation_date: date | None
    calculated_metrics: tuple[PortfolioMetrics, ...]
    selected_portfolio: CandidateEvaluation | None
    ranking: tuple[CandidateEvaluation, ...]
    alternative_top_ranked: tuple[CandidateEvaluation, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    rules_status: str | None = None
    rule_set_version: str = "unavailable"
    proposed_rules_explicitly_enabled: bool = False
