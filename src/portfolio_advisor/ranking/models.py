"""Typed rule, ranking, and score-contribution data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from portfolio_advisor.metrics.models import PortfolioMetrics

Direction = Literal["HIGHER_BETTER", "LOWER_BETTER"]


@dataclass(frozen=True, slots=True)
class MetricRule:
    """One configurable scoring metric and its direction."""

    weight: float
    direction: Direction


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """Auditable definition of one point-in-time policy feature."""

    feature_id: str
    source: str
    availability_timing: str
    direction: str
    weight: float
    normalization: str
    threshold: str
    missing_behavior: str
    nonfinite_behavior: str
    ranking_role: str
    capital_preservation_rationale: str


@dataclass(frozen=True, slots=True)
class ThresholdRule:
    """A policy threshold with explicit operator and effect."""

    threshold_id: str
    feature: str
    value: float
    unit: str
    operator: str
    effect: str
    horizon: str
    approval_status: str
    rationale: str


@dataclass(frozen=True, slots=True)
class EligibilityRule:
    """Configurable completeness and allocation constraints."""

    target_allocation: float
    allocation_tolerance: float
    minimum_metric_coverage: float
    required_metrics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RankingRules:
    """Reviewed configuration used by all ranking components."""

    version: str
    schema_version: int
    policy_name: str
    status: str
    purpose: str
    eligibility: EligibilityRule
    metrics: dict[str, MetricRule]
    features: dict[str, FeatureDefinition]
    thresholds: tuple[ThresholdRule, ...]
    weight_total: float
    weight_tolerance: float
    source_references: tuple[str, ...]
    assumptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScoreContribution:
    """One metric's normalized score and weighted contribution."""

    metric: str
    raw_value: float
    normalized_value: float
    weight: float
    contribution: float


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """Metrics, eligibility, and score information for one candidate."""

    metrics: PortfolioMetrics
    eligible: bool
    rejection_reasons: tuple[str, ...] = field(default_factory=tuple)
    contributions: tuple[ScoreContribution, ...] = field(default_factory=tuple)
    total_score: float | None = None
    rank: int | None = None
