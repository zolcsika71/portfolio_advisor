"""Pure deterministic eligibility checks with explicit rejection reasons."""

from __future__ import annotations

from portfolio_advisor.metrics.models import PortfolioMetrics

from .models import EligibilityRule


def evaluate_eligibility(metrics: PortfolioMetrics, rule: EligibilityRule) -> tuple[str, ...]:
    """Return all failures; no candidate is discarded without an explanation."""
    reasons: list[str] = []
    difference = abs(metrics.allocation_total - rule.target_allocation)
    if difference > rule.allocation_tolerance:
        reasons.append(
            f"allocation total {metrics.allocation_total:.6g} differs from target "
            f"{rule.target_allocation:.6g} by more than {rule.allocation_tolerance:.6g}"
        )
    for metric_name in rule.required_metrics:
        value = getattr(metrics, metric_name, None)
        if value is None or not hasattr(value, "available"):
            reasons.append(f"required metric is not supported by the application: {metric_name}")
        elif not value.available:
            reasons.append(f"required metric is unavailable: {metric_name}")
        elif value.coverage < rule.minimum_metric_coverage:
            reasons.append(
                f"required metric {metric_name} coverage {value.coverage:.1%} is below "
                f"{rule.minimum_metric_coverage:.1%}"
            )
    return tuple(reasons)
