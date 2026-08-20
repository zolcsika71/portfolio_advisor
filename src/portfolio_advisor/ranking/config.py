"""Strict loading and validation for review-controlled ranking rules."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from pathlib import Path

import yaml

from .models import (
    EligibilityRule,
    FeatureDefinition,
    MetricRule,
    RankingRules,
    ThresholdRule,
)

_SCORING_FEATURES = {
    "annualized_volatility",
    "maximum_drawdown",
    "return_1y",
    "sharpe_ratio",
    "unhedged_allocation",
}
_FEATURE_FIELDS = {
    "feature_id", "source", "availability_timing", "direction", "weight", "normalization",
    "threshold", "missing_behavior", "nonfinite_behavior", "ranking_role",
    "capital_preservation_rationale",
}
_THRESHOLD_FIELDS = {
    "threshold_id", "feature", "value", "unit", "operator", "effect", "horizon",
    "approval_status", "rationale",
}
_DIRECTIONS = {"HIGHER_BETTER", "LOWER_BETTER", "TARGET_RANGE", "DISQUALIFIER_ONLY"}
_SCORING_DIRECTIONS = {"HIGHER_BETTER", "LOWER_BETTER"}
_OPERATORS = {"<", "<=", ">", ">="}


class RuleConfigurationError(ValueError):
    """Raised for absent, malformed, or unreviewed ranking policy."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys instead of overwriting."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise RuleConfigurationError(f"Duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_ranking_rules(path: Path, *, allow_proposed: bool = False) -> RankingRules:
    """Load a versioned policy without defaults, repair, or forward inputs."""
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except OSError as error:
        raise RuleConfigurationError(f"Could not read rules: {path}") from error
    except (yaml.YAMLError, RuleConfigurationError) as error:
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
        eligibility_data = _mapping(data, "eligibility")
        scoring_data = _mapping(_mapping(data, "scoring"), "metrics")
        features = _parse_features(data["feature_definitions"])
        thresholds = _parse_thresholds(data["thresholds"], set(features))
        eligibility = EligibilityRule(
            target_allocation=_finite_number(eligibility_data["target_allocation"], "target_allocation"),
            allocation_tolerance=_finite_number(eligibility_data["allocation_tolerance"], "allocation_tolerance"),
            minimum_metric_coverage=_finite_number(
                eligibility_data["minimum_metric_coverage"], "minimum_metric_coverage"
            ),
            required_metrics=tuple(_string_list(eligibility_data["required_metrics"], "required_metrics")),
        )
        metrics = _parse_metrics(scoring_data, features)
        rules = RankingRules(
            version=_required_string(data, "version"),
            schema_version=_schema_version(data),
            policy_name=_required_string(data, "policy_name"),
            status=status,
            purpose=_required_string(data, "purpose"),
            eligibility=eligibility,
            metrics=metrics,
            features=features,
            thresholds=thresholds,
            weight_total=_finite_number(data["weight_total"], "weight_total"),
            weight_tolerance=_finite_number(data["weight_tolerance"], "weight_tolerance"),
            source_references=tuple(_string_list(data.get("source_references", []), "source_references")),
            assumptions=tuple(_string_list(data.get("assumptions", []), "assumptions")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuleConfigurationError(f"Rules document is incomplete: {error}") from error
    _validate_rules(rules)
    return rules


def _mapping(data: Mapping[object, object], field: str) -> Mapping[object, object]:
    value = data[field]
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _required_string(data: Mapping[object, object], field: str) -> str:
    value = data[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return value


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _schema_version(data: Mapping[object, object]) -> int:
    value = data["schema_version"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 2:
        raise ValueError("schema_version must be an integer of at least 2")
    return value


def _parse_features(value: object) -> dict[str, FeatureDefinition]:
    if not isinstance(value, list) or not value:
        raise ValueError("feature_definitions must be a non-empty list")
    parsed: dict[str, FeatureDefinition] = {}
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _FEATURE_FIELDS:
            raise ValueError("each feature definition must contain exactly the required fields")
        feature_id = _required_string(item, "feature_id")
        if feature_id in parsed:
            raise ValueError(f"duplicate feature: {feature_id}")
        direction = _required_string(item, "direction")
        if direction not in _DIRECTIONS:
            raise ValueError(f"invalid direction for {feature_id}: {direction}")
        parsed[feature_id] = FeatureDefinition(
            feature_id=feature_id,
            source=_required_string(item, "source"),
            availability_timing=_required_string(item, "availability_timing"),
            direction=direction,
            weight=_finite_number(item["weight"], f"weight for {feature_id}"),
            normalization=_required_string(item, "normalization"),
            threshold=_required_string(item, "threshold"),
            missing_behavior=_required_string(item, "missing_behavior"),
            nonfinite_behavior=_required_string(item, "nonfinite_behavior"),
            ranking_role=_required_string(item, "ranking_role"),
            capital_preservation_rationale=_required_string(item, "capital_preservation_rationale"),
        )
    return parsed


def _parse_thresholds(value: object, feature_ids: set[str]) -> tuple[ThresholdRule, ...]:
    if not isinstance(value, list):
        raise TypeError("thresholds must be a list")
    parsed: list[ThresholdRule] = []
    identifiers: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _THRESHOLD_FIELDS:
            raise ValueError("each threshold must contain exactly the required fields")
        threshold_id = _required_string(item, "threshold_id")
        if threshold_id in identifiers:
            raise ValueError(f"duplicate threshold: {threshold_id}")
        identifiers.add(threshold_id)
        feature = _required_string(item, "feature")
        if feature not in feature_ids and feature != "required_metrics":
            raise ValueError(f"unknown threshold feature: {feature}")
        operator = _required_string(item, "operator")
        if operator not in _OPERATORS:
            raise ValueError(f"invalid threshold operator: {operator}")
        parsed.append(ThresholdRule(
            threshold_id=threshold_id, feature=feature,
            value=_finite_number(item["value"], f"threshold value for {threshold_id}"),
            unit=_required_string(item, "unit"), operator=operator,
            effect=_required_string(item, "effect"), horizon=_required_string(item, "horizon"),
            approval_status=_required_string(item, "approval_status"),
            rationale=_required_string(item, "rationale"),
        ))
    return tuple(parsed)


def _parse_metrics(
    data: Mapping[object, object], features: Mapping[str, FeatureDefinition]
) -> dict[str, MetricRule]:
    if set(data) != _SCORING_FEATURES:
        unknown = sorted(str(name) for name in set(data) - _SCORING_FEATURES)
        missing = sorted(_SCORING_FEATURES - set(data))
        raise ValueError(f"unknown or missing scoring features: unknown={unknown}, missing={missing}")
    parsed: dict[str, MetricRule] = {}
    for name, value in data.items():
        if not isinstance(name, str) or not isinstance(value, Mapping):
            raise TypeError("scoring metrics must be feature mappings")
        if set(value) != {"weight", "direction"}:
            raise ValueError(f"invalid scoring rule fields for {name}")
        direction = _required_string(value, "direction")
        if direction not in _SCORING_DIRECTIONS:
            raise ValueError(f"invalid direction for {name}: {direction}")
        definition = features.get(name)
        if definition is None:
            raise ValueError(f"unknown feature definition: {name}")
        weight = _finite_number(value["weight"], f"weight for {name}")
        if definition.direction != direction or definition.weight != weight:
            raise ValueError(f"scoring rule and feature definition disagree for {name}")
        parsed[name] = MetricRule(weight=weight, direction=direction)  # type: ignore[arg-type]
    return parsed


def _validate_rules(rules: RankingRules) -> None:
    if rules.eligibility.allocation_tolerance < 0.0:
        raise RuleConfigurationError("allocation_tolerance must be non-negative")
    if not 0.0 <= rules.eligibility.minimum_metric_coverage <= 1.0:
        raise RuleConfigurationError("minimum_metric_coverage must be between zero and one")
    if rules.weight_total <= 0.0 or rules.weight_tolerance < 0.0:
        raise RuleConfigurationError("weight total/tolerance is invalid")
    if any(metric.weight < 0.0 for metric in rules.metrics.values()):
        raise RuleConfigurationError("Metric weights must be finite and non-negative")
    total = sum(metric.weight for metric in rules.metrics.values())
    if abs(total - rules.weight_total) > rules.weight_tolerance:
        raise RuleConfigurationError(
            f"Metric weights total {total:.12g}, expected {rules.weight_total:.12g} within tolerance"
        )
    if any(name not in rules.features for name in rules.eligibility.required_metrics):
        raise RuleConfigurationError("required metric has no feature definition")
    if any(
        rules.features[name].missing_behavior != "REJECT_CANDIDATE"
        or rules.features[name].nonfinite_behavior != "REJECT_CANDIDATE"
        for name in rules.eligibility.required_metrics
    ):
        raise RuleConfigurationError("required metrics must fail closed for missing and non-finite values")
    threshold_ids = {threshold.threshold_id for threshold in rules.thresholds}
    if any(feature.threshold != "NONE" and feature.threshold not in threshold_ids for feature in rules.features.values()):
        raise RuleConfigurationError("feature references an unknown threshold")
