"""Whole-candidate reconciliation for normalized constructed portfolios."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from portfolio_advisor.objectives import CapitalDefensiveConstructionPolicy

from .constraints import ConstructionValidationError, policy_decimal, policy_integer
from .models import ConstructedPortfolioCandidate


def validate_constructed_candidate(
    candidate: ConstructedPortfolioCandidate,
    policy: CapitalDefensiveConstructionPolicy,
) -> None:
    """Require exact 8x10% + 20%, one currency, and governed diversification."""
    required = policy_integer(policy, "allocation", "security_count")
    if len(candidate.holdings) != required:
        raise ConstructionValidationError("constructed candidate must contain exactly eight holdings")
    if len({holding.isin for holding in candidate.holdings}) != required:
        raise ConstructionValidationError("constructed candidate instruments must be unique")
    holding_weight = policy_decimal(policy, "allocation", "weight_per_security")
    cash_weight = policy_decimal(policy, "allocation", "cash_reserve_weight")
    if any(holding.weight != holding_weight for holding in candidate.holdings):
        raise ConstructionValidationError("holding weight differs from reviewed 10% allocation")
    if candidate.cash_weight != cash_weight:
        raise ConstructionValidationError("cash weight differs from reviewed 20% reserve")
    if sum((holding.weight for holding in candidate.holdings), Decimal(0)) + cash_weight != Decimal(
        "1.00"
    ):
        raise ConstructionValidationError("constructed weights do not reconcile to 100%")
    if any(holding.currency != candidate.currency for holding in candidate.holdings):
        raise ConstructionValidationError("constructed candidate contains an FX currency mismatch")
    groups = Counter(holding.group for holding in candidate.holdings)
    if len(groups) < policy_integer(
        policy, "diversification", "minimum_distinct_conflict_free_groups"
    ):
        raise ConstructionValidationError("constructed candidate has fewer than three groups")
    if max(groups.values()) > policy_integer(
        policy, "diversification", "maximum_holdings_per_group"
    ):
        raise ConstructionValidationError("constructed candidate exceeds the 40% group cap")
