"""Versioned unweighted comparison policy for unlike capital finalists."""

from __future__ import annotations

from math import isfinite

from .models import (
    ComparisonAvailability,
    ComparisonDimension,
    DimensionPreference,
    FinalistComparisonPolicy,
    GovernedRecommendation,
    RecommendationStatus,
    WorkflowFinalist,
)

COMPARISON_POLICY_ID = "CAPITAL_CONSERVATION_FINALIST_COMPARISON_POLICY"
COMPARISON_POLICY_VERSION = "1.0.0"


class FinalistComparisonError(RuntimeError):
    """Raised when the reviewed comparison contract cannot be applied."""


def build_finalist_comparison_policy() -> FinalistComparisonPolicy:
    """Return the reviewed, weight-free strict-Pareto comparison contract."""
    return FinalistComparisonPolicy(
        policy_id=COMPARISON_POLICY_ID,
        version=COMPARISON_POLICY_VERSION,
        schema_version=1,
        status="APPROVED_ACTIVE",
        objective="capital_conservation",
        method="UNWEIGHTED_STRICT_PARETO",
        dimensions=(
            ("annualized_volatility", "LOWER_BETTER", "decimal", "1Y"),
            ("maximum_drawdown", "HIGHER_BETTER", "decimal", "1Y"),
            ("return_1y", "HIGHER_BETTER", "decimal", "1Y"),
            ("sharpe_ratio", "HIGHER_BETTER", "ratio", "1Y"),
            ("unhedged_allocation", "LOWER_BETTER", "decimal", "SNAPSHOT"),
        ),
        minimum_comparable_dimensions=5,
        score_comparison="PROHIBITED_DIFFERENT_NORMALIZATION_UNIVERSES",
    )


def compare_finalists(
    model: WorkflowFinalist,
    shortlist: WorkflowFinalist,
    policy: FinalistComparisonPolicy,
    *,
    expected_policy_fingerprint: str | None = None,
) -> GovernedRecommendation:
    """Compare common raw features without comparing cross-universe scores."""
    expected = build_finalist_comparison_policy()
    if policy != expected or policy.fingerprint != expected.fingerprint:
        raise FinalistComparisonError("comparison policy identity or fingerprint mismatch")
    if expected_policy_fingerprint is not None and policy.fingerprint != expected_policy_fingerprint:
        raise FinalistComparisonError("stale comparison policy fingerprint")
    model_values = dict(model.feature_values)
    shortlist_values = dict(shortlist.feature_values)
    dimensions: list[ComparisonDimension] = []
    model_better = shortlist_better = False
    comparable = 0
    for name, direction, unit, horizon in policy.dimensions:
        left = model_values.get(name)
        right = shortlist_values.get(name)
        if left is None or right is None or not isfinite(left) or not isfinite(right):
            dimensions.append(
                ComparisonDimension(
                    name,
                    ComparisonAvailability.UNAVAILABLE,
                    left,
                    right,
                    unit,
                    horizon,
                    direction,
                    DimensionPreference.UNAVAILABLE,
                    "MISSING_OR_NONFINITE_COMMON_EVIDENCE",
                )
            )
            continue
        comparable += 1
        preference = _preference(left, right, direction)
        model_better |= preference is DimensionPreference.MODEL_PORTFOLIO
        shortlist_better |= preference is DimensionPreference.SHORTLIST_INSTRUMENT
        dimensions.append(
            ComparisonDimension(
                name,
                ComparisonAvailability.PARTIALLY_COMPARABLE,
                left,
                right,
                unit,
                horizon,
                direction,
                preference,
                "COMMON_DEFINITION_DIFFERENT_AGGREGATION_SCOPE",
            )
        )
    unavailable = tuple(
        dimension.dimension_id
        for dimension in dimensions
        if dimension.availability is ComparisonAvailability.UNAVAILABLE
    ) + ("cross_universe_total_score",)
    if comparable < policy.minimum_comparable_dimensions:
        status = RecommendationStatus.INSUFFICIENT_COMPARABLE_EVIDENCE
        recommended = alternative = None
        reasons = ("MINIMUM_COMPARABLE_DIMENSIONS_NOT_MET",)
    elif model_better and not shortlist_better:
        status = RecommendationStatus.RECOMMEND_MODEL_PORTFOLIO
        recommended, alternative = model.stable_id, shortlist.stable_id
        reasons = ("MODEL_STRICT_PARETO_DOMINANCE",)
    elif shortlist_better and not model_better:
        status = RecommendationStatus.RECOMMEND_SHORTLIST_CANDIDATE
        recommended, alternative = shortlist.stable_id, model.stable_id
        reasons = ("SHORTLIST_STRICT_PARETO_DOMINANCE",)
    elif not model_better and not shortlist_better:
        status = RecommendationStatus.NO_CLEAR_RECOMMENDATION
        recommended = alternative = None
        reasons = ("ALL_COMPARABLE_DIMENSIONS_TIED",)
    else:
        status = RecommendationStatus.NO_CLEAR_RECOMMENDATION
        recommended = alternative = None
        reasons = ("MIXED_COMPARABLE_SIGNALS",)
    return GovernedRecommendation(
        status=status,
        recommended_finalist_id=recommended,
        alternative_finalist_id=alternative,
        reason_codes=reasons,
        unavailable_dimensions=unavailable,
        comparison_policy_id=policy.policy_id,
        comparison_policy_version=policy.version,
        comparison_policy_fingerprint=policy.fingerprint,
        dimensions=tuple(dimensions),
    )


def _preference(left: float, right: float, direction: str) -> DimensionPreference:
    if left == right:
        return DimensionPreference.TIE
    if direction == "HIGHER_BETTER":
        return (
            DimensionPreference.MODEL_PORTFOLIO
            if left > right
            else DimensionPreference.SHORTLIST_INSTRUMENT
        )
    if direction == "LOWER_BETTER":
        return (
            DimensionPreference.MODEL_PORTFOLIO
            if left < right
            else DimensionPreference.SHORTLIST_INSTRUMENT
        )
    raise FinalistComparisonError(f"unsupported comparison direction: {direction}")
