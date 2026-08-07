"""Strict loading and validation for review-controlled ranking rules."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import EligibilityRule, MetricRule, RankingRules


class RuleConfigurationError(ValueError):
    """Raised for absent, malformed, or unreviewed ranking policy."""


def load_ranking_rules(path: Path, *, allow_proposed: bool = False) -> RankingRules:
    """Load a YAML policy without supplying financial defaults.

    Only ``reviewed`` and ``approved`` policies are executable by default. The
    explicit ``allow_proposed`` escape hatch is intended for controlled testing
    and evaluation of a proposed policy, never silent production use.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuleConfigurationError(f"Could not read rules: {path}") from error
    except yaml.YAMLError as error:
        raise RuleConfigurationError(f"Invalid YAML rules: {path}") from error
    if not isinstance(data, dict):
        raise RuleConfigurationError("Rules document must be a mapping")
    status = data.get("status")
    if status not in {"reviewed", "approved", "proposed"}:
        raise RuleConfigurationError("Rules status must be reviewed, approved, or proposed")
    if status == "proposed" and not allow_proposed:
        raise RuleConfigurationError(
            "Ranking rules are proposed, not reviewed. Review the policy or explicitly opt in with --allow-proposed-rules."
        )
    try:
        eligibility_data = data["eligibility"]
        scoring_data = data["scoring"]["metrics"]
        eligibility = EligibilityRule(
            target_allocation=float(eligibility_data["target_allocation"]),
            allocation_tolerance=float(eligibility_data["allocation_tolerance"]),
            minimum_metric_coverage=float(eligibility_data["minimum_metric_coverage"]),
            required_metrics=tuple(eligibility_data["required_metrics"]),
        )
        metrics = {
            str(name): MetricRule(weight=float(rule["weight"]), direction=rule["direction"])
            for name, rule in scoring_data.items()
        }
        rules = RankingRules(
            version=_required_version(data),
            status=status,
            purpose=str(data["purpose"]),
            eligibility=eligibility,
            metrics=metrics,
            source_references=tuple(data.get("source_references", [])),
            assumptions=tuple(data.get("assumptions", [])),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuleConfigurationError(f"Rules document is incomplete: {error}") from error
    _validate_rules(rules)
    return rules


def _required_version(data: dict[object, object]) -> str:
    """Return the policy version; executable policies must be traceable."""
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuleConfigurationError("Rules document must define a non-empty version")
    return version


def _validate_rules(rules: RankingRules) -> None:
    if rules.eligibility.allocation_tolerance < 0.0:
        raise RuleConfigurationError("allocation_tolerance must be non-negative")
    if not 0.0 <= rules.eligibility.minimum_metric_coverage <= 1.0:
        raise RuleConfigurationError("minimum_metric_coverage must be between 0 and 1")
    if not rules.metrics:
        raise RuleConfigurationError("At least one scoring metric is required")
    if any(metric.weight < 0.0 for metric in rules.metrics.values()):
        raise RuleConfigurationError("Metric weights must be non-negative")
    if sum(metric.weight for metric in rules.metrics.values()) <= 0.0:
        raise RuleConfigurationError("At least one metric weight must be positive")
    invalid_directions = [
        name for name, rule in rules.metrics.items() if rule.direction not in {"higher", "lower"}
    ]
    if invalid_directions:
        raise RuleConfigurationError(f"Invalid metric directions: {', '.join(invalid_directions)}")
