"""Orchestrate repository, metrics, rules, and ranking without formulas here."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.metrics.portfolio import calculate_all_portfolio_metrics
from portfolio_advisor.ranking.config import RuleConfigurationError, load_ranking_rules
from portfolio_advisor.ranking.ranking import rank_portfolios

from .models import AdvisorResult


class CapitalPreservationAdvisor:
    """Run the fixed read → calculate → filter → score → rank workflow."""

    def __init__(self, repository: ModelPortfolioRepository, rules_path: Path) -> None:
        self.repository = repository
        self.rules_path = rules_path

    def evaluate(
        self,
        *,
        observation_date: date | None = None,
        allow_proposed_rules: bool = False,
        alternative_count: int = 3,
    ) -> AdvisorResult:
        """Return a structured result for the requested point in time.

        Omitting ``observation_date`` retains the Milestone 2 latest-date
        behavior. A caller that supplies a date receives rankings calculated
        from only that date's holdings.
        """
        if alternative_count < 0:
            raise ValueError("alternative_count must not be negative")
        resolved_date = observation_date or self.repository.latest_observation_date()
        holdings = self.repository.load_holdings(resolved_date)
        metrics = calculate_all_portfolio_metrics(holdings)
        metric_warnings = tuple(warning for candidate in metrics for warning in candidate.warnings)
        unavailable = tuple(item for candidate in metrics for item in candidate.unavailable_metrics)
        try:
            rules = load_ranking_rules(self.rules_path, allow_proposed=allow_proposed_rules)
        except RuleConfigurationError as error:
            return AdvisorResult(
                observation_date=resolved_date,
                calculated_metrics=tuple(metrics),
                selected_portfolio=None,
                ranking=(),
                alternative_top_ranked=(),
                warnings=(str(error), *sorted(set(metric_warnings)), *sorted(set(unavailable))),
                assumptions=(
                    "No selection was made because no reviewed or approved ranking policy was supplied.",
                ),
                rules_status="unavailable",
                rule_set_version="unavailable",
                proposed_rules_explicitly_enabled=allow_proposed_rules,
            )
        ranking, ranking_warnings = rank_portfolios(metrics, rules)
        selected = next((item for item in ranking if item.rank == 1), None)
        alternatives = tuple(
            item for item in ranking if item.rank is not None and 1 < item.rank <= alternative_count + 1
        )
        return AdvisorResult(
            observation_date=resolved_date,
            calculated_metrics=tuple(metrics),
            selected_portfolio=selected,
            ranking=tuple(ranking),
            alternative_top_ranked=alternatives,
            warnings=(*sorted(set(metric_warnings)), *sorted(set(unavailable)), *ranking_warnings),
            assumptions=(
                *rules.assumptions,
                "Portfolio metrics are allocation-weighted indicators from reported constituent fields, not reconstructed return-series metrics.",
            ),
            rules_status=rules.status,
            rule_set_version=rules.version,
            proposed_rules_explicitly_enabled=allow_proposed_rules,
        )
