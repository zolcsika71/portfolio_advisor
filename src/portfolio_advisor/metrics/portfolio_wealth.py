"""Synthetic-only Phase F3A portfolio-wealth derivation and trusted lineage.

The builder in this module is deliberately disconnected from database readers,
Phase E evidence, construction runtime, ranking, and the historical portfolio-NAV
reconstruction path.  It proves the approved F1 wealth arithmetic with synthetic
fixtures and hands only a recomputed, validated derivation to the existing F2
``SYNTHETIC_FIXTURE`` interface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, DecimalException, localcontext
from enum import StrEnum
from typing import Final

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.metrics.governed import (
    GovernedMetricRun,
    GovernedMetricSeries,
    GovernedObservation,
    MetricSuitabilityState,
    ObservationFingerprintScheme,
    ObservationSemantics,
    PhaseF2ExecutionMode,
    SourceApprovalState,
    bind_series_provenance,
    compute_governed_metrics,
)
from portfolio_advisor.metrics.policy_contract import (
    PHASE_F1_DECISION_TOKENS,
    PHASE_F1_POLICY_ARTIFACT,
    PHASE_F1_POLICY_FINGERPRINT,
    PHASE_F1_POLICY_ID,
    PHASE_F1_POLICY_VERSION,
    PhaseF1PortfolioMetricsPolicy,
)
from portfolio_advisor.objectives.construction_policy import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ID,
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_VERSION,
    CapitalDefensiveConstructionPolicy,
)

PHASE_F3A_IMPLEMENTATION_ID: Final = (
    "PHASE_F3A_GOVERNED_EUR_PORTFOLIO_WEALTH_AND_TRUSTED_LINEAGE_FOUNDATION"
)
PHASE_F3A_IMPLEMENTATION_VERSION: Final = "1.0.0"
PHASE_F3A_ACTIVATION_STATE: Final = "SYNTHETIC_WEALTH_FOUNDATION_IMPLEMENTED"

_DECIMAL_PRECISION: Final = 50
_OUTPUT_QUANTUM: Final = Decimal("0.000000000000000001")
_PERSISTED_HALF_QUANTUM_TOLERANCE: Final = Decimal("0.0000000000000000005")
_SERIALIZED_NINE_WEIGHT_TOLERANCE: Final = Decimal("0.0000000000000000045")
_APPROVED_CONSTRUCTION_POLICY_FINGERPRINT: Final = (
    "a5dc75f07eac4e0ab615f1669a95f7eecdbb3f0e31e1c6bb174dd000097ccbbf"
)
_SYNTHETIC_SOURCE_IDENTITY: Final = "PHASE_F3A_SYNTHETIC_CONSTITUENT_FIXTURE"
_SYNTHETIC_SOURCE_GOVERNANCE: Final = "SYNTHETIC_FIXTURE_ONLY"
_SYNTHETIC_REFERENCE_PREFIX: Final = "SYNTHETIC_FIXTURE:PHASE_F3A:"
_SYNTHETIC_FINGERPRINT_SCHEME: Final = "PHASE_F3A_SYNTHETIC_NAV_V1"
_SECURITY_COUNT: Final = 8
_HASH_LENGTH: Final = 64


class PhaseF3AValidationError(ValueError):
    """A synthetic fixture or derived lineage failed the governed F3A boundary."""


class SyntheticDistributionState(StrEnum):
    """Explicit fixture-only distribution semantics."""

    SIMULATED_ACCUMULATING_SHARE_CLASS = "SIMULATED_ACCUMULATING_SHARE_CLASS"
    SIMULATED_DISTRIBUTING_SHARE_CLASS = "SIMULATED_DISTRIBUTING_SHARE_CLASS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SyntheticNavObservation:
    """One synthetic, exact Decimal NAV endpoint with deterministic provenance."""

    observation_date: str
    nav: Decimal
    evidence_reference: str
    observation_fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        constituent_identity: str,
        observation_date: str,
        nav: Decimal,
        evidence_reference: str,
    ) -> SyntheticNavObservation:
        """Create a synthetic observation and bind its exact constituent identity."""
        observation = cls(
            observation_date=observation_date,
            nav=nav,
            evidence_reference=evidence_reference,
            observation_fingerprint="",
        )
        return replace(
            observation,
            observation_fingerprint=_observation_fingerprint(constituent_identity, observation),
        )

    def payload(self, constituent_identity: str) -> dict[str, object]:
        return {
            "constituent_identity": constituent_identity,
            "evidence_reference": self.evidence_reference,
            "fingerprint_scheme": _SYNTHETIC_FINGERPRINT_SCHEME,
            "nav": _source_decimal_text(self.nav),
            "observation_date": self.observation_date,
        }


@dataclass(frozen=True, slots=True)
class SyntheticConstituentSeries:
    """A complete supplied synthetic constituent history before alignment."""

    constituent_identity: str
    currency_code: str
    source_identity: str
    source_governance: str
    distribution_state: SyntheticDistributionState
    evidence_available_at_utc: str
    series_evidence_reference: str
    observations: tuple[SyntheticNavObservation, ...]
    provenance_fingerprint: str = ""

    def provenance_payload(self) -> dict[str, object]:
        return {
            "constituent_identity": self.constituent_identity,
            "currency_code": self.currency_code,
            "distribution_state": _enum_text(self.distribution_state),
            "evidence_available_at_utc": self.evidence_available_at_utc,
            "fingerprint_scheme": _SYNTHETIC_FINGERPRINT_SCHEME,
            "observations": [
                item.payload(self.constituent_identity) for item in self.observations
            ],
            "series_evidence_reference": self.series_evidence_reference,
            "source_governance": self.source_governance,
            "source_identity": self.source_identity,
        }

    def expected_provenance_fingerprint(self) -> str:
        return canonical_fingerprint(self.provenance_payload())


def bind_synthetic_constituent_provenance(
    series: SyntheticConstituentSeries,
) -> SyntheticConstituentSeries:
    """Return an immutable series bearing the hash of its full synthetic payload."""
    return replace(series, provenance_fingerprint=series.expected_provenance_fingerprint())


def create_synthetic_constituent_series(
    *,
    constituent_identity: str,
    values: tuple[tuple[str, Decimal], ...],
    evidence_available_at_utc: str,
    distribution_state: SyntheticDistributionState = (
        SyntheticDistributionState.SIMULATED_ACCUMULATING_SHARE_CLASS
    ),
) -> SyntheticConstituentSeries:
    """Create, but do not validate, one explicit synthetic constituent fixture."""
    observations = tuple(
        SyntheticNavObservation.create(
            constituent_identity=constituent_identity,
            observation_date=observation_date,
            nav=nav,
            evidence_reference=(
                f"{_SYNTHETIC_REFERENCE_PREFIX}{constituent_identity}:OBSERVATION:{index}"
            ),
        )
        for index, (observation_date, nav) in enumerate(values)
    )
    return bind_synthetic_constituent_provenance(
        SyntheticConstituentSeries(
            constituent_identity=constituent_identity,
            currency_code="EUR",
            source_identity=_SYNTHETIC_SOURCE_IDENTITY,
            source_governance=_SYNTHETIC_SOURCE_GOVERNANCE,
            distribution_state=distribution_state,
            evidence_available_at_utc=evidence_available_at_utc,
            series_evidence_reference=(
                f"{_SYNTHETIC_REFERENCE_PREFIX}{constituent_identity}:SERIES"
            ),
            observations=observations,
        )
    )


@dataclass(frozen=True, slots=True)
class SyntheticPortfolioWealthRequest:
    """One explicit synthetic EUR derivation request; it is never a trade instruction."""

    portfolio_identity: str
    initial_capital: Decimal
    decision_as_of_utc: str
    nav_evidence_cutoff: str
    constituents: tuple[SyntheticConstituentSeries, ...]


@dataclass(frozen=True, slots=True)
class SelectedWindowProof:
    """Proof that the selected observed-date window is the latest minimal one."""

    complete_common_date_count: int
    selected_start_date: str
    selected_end_date: str
    selected_observation_count: int
    selected_return_interval_count: int
    selected_calendar_span_days: int
    staleness_calendar_days: int
    excluded_leading_common_dates: int
    next_later_start_date: str | None
    next_later_start_interval_count: int | None
    next_later_start_span_days: int | None
    next_later_start_failed_minima: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "complete_common_date_count": self.complete_common_date_count,
            "excluded_leading_common_dates": self.excluded_leading_common_dates,
            "next_later_start_date": self.next_later_start_date,
            "next_later_start_failed_minima": list(self.next_later_start_failed_minima),
            "next_later_start_interval_count": self.next_later_start_interval_count,
            "next_later_start_span_days": self.next_later_start_span_days,
            "selected_calendar_span_days": self.selected_calendar_span_days,
            "selected_end_date": self.selected_end_date,
            "selected_observation_count": self.selected_observation_count,
            "selected_return_interval_count": self.selected_return_interval_count,
            "selected_start_date": self.selected_start_date,
            "staleness_calendar_days": self.staleness_calendar_days,
        }


@dataclass(frozen=True, slots=True)
class ConstituentDerivation:
    """Initial fixed-unit derivation for one synthetic constituent."""

    constituent_identity: str
    complete_series_fingerprint: str
    complete_observation_count: int
    complete_first_date: str
    complete_last_date: str
    distribution_state: str
    initial_nav: Decimal
    initial_allocation: Decimal
    fixed_mathematical_units: Decimal
    initial_reconciliation_relative_error: Decimal

    def internal_payload(self) -> dict[str, object]:
        return {
            "complete_first_date": self.complete_first_date,
            "complete_last_date": self.complete_last_date,
            "complete_observation_count": self.complete_observation_count,
            "complete_series_fingerprint": self.complete_series_fingerprint,
            "constituent_identity": self.constituent_identity,
            "distribution_state": self.distribution_state,
            "fixed_mathematical_units": _source_decimal_text(self.fixed_mathematical_units),
            "initial_allocation": _source_decimal_text(self.initial_allocation),
            "initial_nav": _source_decimal_text(self.initial_nav),
            "initial_reconciliation_relative_error": _source_decimal_text(
                self.initial_reconciliation_relative_error
            ),
        }

    def to_dict(self) -> dict[str, object]:
        payload = self.internal_payload()
        payload.update(
            {
                "fixed_mathematical_units": _canonical_output_text(
                    self.fixed_mathematical_units
                ),
                "initial_allocation": _canonical_output_text(self.initial_allocation),
                "initial_reconciliation_relative_error": _canonical_output_text(
                    self.initial_reconciliation_relative_error
                ),
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class ComponentValue:
    """One constituent value and drifted weight at an aligned observed endpoint."""

    constituent_identity: str
    nav: Decimal
    nav_observation_fingerprint: str
    fixed_mathematical_units: Decimal
    component_value: Decimal
    derived_weight: Decimal

    def internal_payload(self) -> dict[str, object]:
        return {
            "component_value": _source_decimal_text(self.component_value),
            "constituent_identity": self.constituent_identity,
            "derived_weight": _source_decimal_text(self.derived_weight),
            "fixed_mathematical_units": _source_decimal_text(self.fixed_mathematical_units),
            "nav": _source_decimal_text(self.nav),
            "nav_observation_fingerprint": self.nav_observation_fingerprint,
        }

    def to_dict(self) -> dict[str, object]:
        payload = self.internal_payload()
        payload.update(
            {
                "component_value": _canonical_output_text(self.component_value),
                "derived_weight": _canonical_output_text(self.derived_weight),
                "fixed_mathematical_units": _canonical_output_text(
                    self.fixed_mathematical_units
                ),
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class PortfolioWealthPoint:
    """Derived wealth on one actual eight-way common valuation date."""

    valuation_date: str
    components: tuple[ComponentValue, ...]
    nominal_cash: Decimal
    cash_weight: Decimal
    total_wealth: Decimal
    weight_sum_absolute_error: Decimal
    serialized_weight_sum_absolute_error: Decimal

    def internal_payload_without_fingerprint(self) -> dict[str, object]:
        return {
            "cash_weight": _source_decimal_text(self.cash_weight),
            "components": [item.internal_payload() for item in self.components],
            "nominal_cash": _source_decimal_text(self.nominal_cash),
            "serialized_weight_sum_absolute_error": _source_decimal_text(
                self.serialized_weight_sum_absolute_error
            ),
            "total_wealth": _source_decimal_text(self.total_wealth),
            "valuation_date": self.valuation_date,
            "weight_sum_absolute_error": _source_decimal_text(self.weight_sum_absolute_error),
        }

    def payload_without_fingerprint(self) -> dict[str, object]:
        return {
            "cash_weight": _canonical_output_text(self.cash_weight),
            "components": [item.to_dict() for item in self.components],
            "nominal_cash": _canonical_output_text(self.nominal_cash),
            "serialized_weight_sum_absolute_error": _canonical_output_text(
                self.serialized_weight_sum_absolute_error
            ),
            "total_wealth": _canonical_output_text(self.total_wealth),
            "valuation_date": self.valuation_date,
            "weight_sum_absolute_error": _canonical_output_text(
                self.weight_sum_absolute_error
            ),
        }

    @property
    def point_fingerprint(self) -> str:
        return canonical_fingerprint(self.internal_payload_without_fingerprint())

    def to_dict(self) -> dict[str, object]:
        return {
            **self.payload_without_fingerprint(),
            "point_fingerprint": self.point_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class SyntheticPortfolioWealthLineage:
    """Immutable derivation record; validation always recomputes it from inputs."""

    implementation_id: str
    implementation_version: str
    activation_state: str
    portfolio_identity: str
    currency_code: str
    metrics_policy_id: str
    metrics_policy_version: str
    metrics_policy_fingerprint: str
    metrics_policy_artifact: str
    construction_policy_id: str
    construction_policy_version: str
    construction_policy_fingerprint: str
    construction_policy_artifact: str
    decision_as_of_utc: str
    nav_evidence_cutoff: str
    evidence_available_at_utc: str
    source_semantics: str
    initial_capital: Decimal
    security_weight: Decimal
    initial_security_allocation: Decimal
    cash_weight: Decimal
    nominal_cash: Decimal
    window_proof: SelectedWindowProof
    constituents: tuple[ConstituentDerivation, ...]
    wealth_points: tuple[PortfolioWealthPoint, ...]
    real_evidence_execution: str = "BLOCKED_NOT_AUTHORIZED"
    construction_runtime_activation: str = "NOT_ACTIVATED"
    reconstruction_freeze_change: str = "NOT_PERFORMED"
    ranking_activation: str = "NOT_ACTIVATED"
    selection_eligibility: str = "NOT_EVALUATED"
    database_persistence: str = "NOT_PERFORMED"
    production_cutover: str = "NOT_AUTHORIZED"

    def internal_payload_without_fingerprint(self) -> dict[str, object]:
        return {
            "activation_state": self.activation_state,
            "cash_weight": _source_decimal_text(self.cash_weight),
            "constituents": [item.internal_payload() for item in self.constituents],
            "construction_policy": {
                "artifact": self.construction_policy_artifact,
                "fingerprint": self.construction_policy_fingerprint,
                "policy_id": self.construction_policy_id,
                "version": self.construction_policy_version,
            },
            "construction_runtime_activation": self.construction_runtime_activation,
            "currency_code": self.currency_code,
            "database_persistence": self.database_persistence,
            "decision_as_of_utc": self.decision_as_of_utc,
            "evidence_available_at_utc": self.evidence_available_at_utc,
            "implementation_id": self.implementation_id,
            "implementation_version": self.implementation_version,
            "initial_capital": _source_decimal_text(self.initial_capital),
            "initial_security_allocation": _source_decimal_text(
                self.initial_security_allocation
            ),
            "metrics_policy": {
                "artifact": self.metrics_policy_artifact,
                "fingerprint": self.metrics_policy_fingerprint,
                "policy_id": self.metrics_policy_id,
                "version": self.metrics_policy_version,
            },
            "nav_evidence_cutoff": self.nav_evidence_cutoff,
            "nominal_cash": _source_decimal_text(self.nominal_cash),
            "portfolio_identity": self.portfolio_identity,
            "production_cutover": self.production_cutover,
            "ranking_activation": self.ranking_activation,
            "real_evidence_execution": self.real_evidence_execution,
            "reconstruction_freeze_change": self.reconstruction_freeze_change,
            "security_weight": _source_decimal_text(self.security_weight),
            "selection_eligibility": self.selection_eligibility,
            "source_semantics": self.source_semantics,
            "wealth_points": [
                item.internal_payload_without_fingerprint() for item in self.wealth_points
            ],
            "window_proof": self.window_proof.to_dict(),
        }

    def payload_without_fingerprint(self) -> dict[str, object]:
        payload = self.internal_payload_without_fingerprint()
        payload.update(
            {
                "cash_weight": _canonical_output_text(self.cash_weight),
                "constituents": [item.to_dict() for item in self.constituents],
                "initial_security_allocation": _canonical_output_text(
                    self.initial_security_allocation
                ),
                "nominal_cash": _canonical_output_text(self.nominal_cash),
                "security_weight": _canonical_output_text(self.security_weight),
                "wealth_points": [item.to_dict() for item in self.wealth_points],
            }
        )
        return payload

    @property
    def lineage_fingerprint(self) -> str:
        return canonical_fingerprint(self.internal_payload_without_fingerprint())

    def to_dict(self) -> dict[str, object]:
        return {
            **self.payload_without_fingerprint(),
            "lineage_fingerprint": self.lineage_fingerprint,
        }

    def render_audit(self) -> str:
        return canonical_json(self.to_dict()) + "\n"


def build_synthetic_eur_portfolio_wealth(
    *,
    request: SyntheticPortfolioWealthRequest,
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> SyntheticPortfolioWealthLineage:
    """Build one deterministic synthetic EUR wealth lineage from complete histories."""
    contract = _validate_contracts(metrics_policy, construction_policy)
    _validate_request(request, contract)
    validated = tuple(
        sorted(
            (_validate_constituent(item, request) for item in request.constituents),
            key=lambda item: item.series.constituent_identity,
        )
    )
    window_dates, window_proof = _select_latest_minimal_window(validated, request, contract)

    security_weight = contract.security_weight
    cash_weight = contract.cash_weight
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        initial_security_allocation = request.initial_capital * security_weight
        nominal_cash = request.initial_capital * cash_weight
        constituent_derivations = _derive_constituents(
            validated,
            window_dates[0],
            initial_security_allocation,
            contract.endpoint_relative_tolerance,
        )
        wealth_points = _derive_wealth_points(
            validated,
            constituent_derivations,
            window_dates,
            nominal_cash,
            contract.weight_sum_tolerance,
            contract.output_quantum,
            contract.persisted_half_quantum_tolerance,
            contract.serialized_nine_weight_tolerance,
        )
        _require_relative_reconciliation(
            wealth_points[0].total_wealth,
            request.initial_capital,
            contract.endpoint_relative_tolerance,
            "initial portfolio wealth",
        )

    lineage = SyntheticPortfolioWealthLineage(
        implementation_id=PHASE_F3A_IMPLEMENTATION_ID,
        implementation_version=PHASE_F3A_IMPLEMENTATION_VERSION,
        activation_state=PHASE_F3A_ACTIVATION_STATE,
        portfolio_identity=request.portfolio_identity,
        currency_code="EUR",
        metrics_policy_id=metrics_policy.policy_id,
        metrics_policy_version=metrics_policy.version,
        metrics_policy_fingerprint=metrics_policy.fingerprint,
        metrics_policy_artifact=metrics_policy.artifact_reference,
        construction_policy_id=construction_policy.policy_id,
        construction_policy_version=construction_policy.version,
        construction_policy_fingerprint=construction_policy.fingerprint,
        construction_policy_artifact=construction_policy.artifact_reference,
        decision_as_of_utc=request.decision_as_of_utc,
        nav_evidence_cutoff=request.nav_evidence_cutoff,
        evidence_available_at_utc=max(item.series.evidence_available_at_utc for item in validated),
        source_semantics="SYNTHETIC_ACCUMULATING_SHARE_CLASS_NAV_TO_PORTFOLIO_WEALTH",
        initial_capital=request.initial_capital,
        security_weight=security_weight,
        initial_security_allocation=initial_security_allocation,
        cash_weight=cash_weight,
        nominal_cash=nominal_cash,
        window_proof=window_proof,
        constituents=constituent_derivations,
        wealth_points=wealth_points,
    )
    _validate_canonical_outputs(lineage, contract)
    return lineage


def validate_synthetic_eur_portfolio_wealth(
    *,
    lineage: SyntheticPortfolioWealthLineage,
    request: SyntheticPortfolioWealthRequest,
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    """Recompute the full derivation; a self-consistent hash alone is insufficient."""
    expected = build_synthetic_eur_portfolio_wealth(
        request=request,
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    if canonical_json(lineage.internal_payload_without_fingerprint()) != canonical_json(
        expected.internal_payload_without_fingerprint()
    ):
        raise PhaseF3AValidationError("synthetic wealth lineage differs from recomputed derivation")


def adapt_validated_synthetic_wealth_to_f2(
    *,
    lineage: SyntheticPortfolioWealthLineage,
    request: SyntheticPortfolioWealthRequest,
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> GovernedMetricSeries:
    """Validate by recomputation, then adapt only to F2 ``SYNTHETIC_FIXTURE``."""
    validate_synthetic_eur_portfolio_wealth(
        lineage=lineage,
        request=request,
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    lineage_reference = f"SYNTHETIC_FIXTURE:PHASE_F3A:LINEAGE:{lineage.lineage_fingerprint}"
    observations = tuple(
        GovernedObservation.create(
            observation_date=point.valuation_date,
            value=point.total_wealth,
            evidence_reference=(
                f"SYNTHETIC_FIXTURE:PHASE_F3A:WEALTH_POINT:{point.point_fingerprint}"
            ),
        )
        for point in lineage.wealth_points
    )
    return bind_series_provenance(
        GovernedMetricSeries(
            series_identity=(
                f"PHASE_F3A_SYNTHETIC_PORTFOLIO_WEALTH:{lineage.portfolio_identity}:"
                f"{lineage.lineage_fingerprint}"
            ),
            subject_identity=lineage.portfolio_identity,
            source_identity="PHASE_F3A_VALIDATED_SYNTHETIC_WEALTH_DERIVATION",
            source_governance="SYNTHETIC_FIXTURE_ONLY_RECOMPUTED_LINEAGE",
            currency_code="EUR",
            observation_semantics=ObservationSemantics.PORTFOLIO_WEALTH_TOTAL_RETURN,
            source_approval_state=SourceApprovalState.SYNTHETIC_FIXTURE_APPROVED,
            metric_suitability_state=MetricSuitabilityState.APPROVED_TOTAL_RETURN_SERIES,
            observation_fingerprint_scheme=ObservationFingerprintScheme.PHASE_F2_SYNTHETIC_V1,
            execution_mode=PhaseF2ExecutionMode.SYNTHETIC_FIXTURE,
            observations=observations,
            evidence_references=(
                lineage_reference,
                *(item.evidence_reference for item in observations),
            ),
            decision_as_of_utc=lineage.decision_as_of_utc,
            evidence_available_at_utc=lineage.evidence_available_at_utc,
            nav_evidence_cutoff=lineage.nav_evidence_cutoff,
            alignment_method="STRICT_EIGHT_WAY_INTERSECTION",
            window_selection_method="LATEST_MINIMAL_COMMON_365D_252_WINDOW",
            endpoint_method="OBSERVED_ENDPOINTS_EXACT_ELAPSED_DAYS",
            portfolio_dynamics="BUY_AND_HOLD_WEIGHT_DRIFT",
            cash_return_treatment="UNREMUNERATED_NOMINAL_CASH",
        )
    )


def compute_phase_f3a_synthetic_metrics(
    *,
    lineage: SyntheticPortfolioWealthLineage,
    request: SyntheticPortfolioWealthRequest,
    requested_metrics: Sequence[str],
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> GovernedMetricRun:
    """Recompute lineage and invoke the unchanged public F2 synthetic interface."""
    series = adapt_validated_synthetic_wealth_to_f2(
        lineage=lineage,
        request=request,
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    return compute_governed_metrics(
        series=series,
        requested_metrics=requested_metrics,
        policy=metrics_policy,
    )


@dataclass(frozen=True, slots=True)
class _Contract:
    decision_as_of_utc: str
    nav_evidence_cutoff: str
    security_weight: Decimal
    cash_weight: Decimal
    minimum_history_days: int
    minimum_intervals: int
    maximum_staleness_days: int
    endpoint_relative_tolerance: Decimal
    weight_sum_tolerance: Decimal
    output_quantum: Decimal
    persisted_half_quantum_tolerance: Decimal
    serialized_nine_weight_tolerance: Decimal


@dataclass(frozen=True, slots=True)
class _ValidatedConstituent:
    series: SyntheticConstituentSeries
    dates: tuple[date, ...]
    by_date: Mapping[date, SyntheticNavObservation]


def _validate_contracts(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> _Contract:
    if (
        metrics_policy.policy_id != PHASE_F1_POLICY_ID
        or metrics_policy.version != PHASE_F1_POLICY_VERSION
        or metrics_policy.fingerprint != PHASE_F1_POLICY_FINGERPRINT
        or dict(metrics_policy.decision_tokens) != PHASE_F1_DECISION_TOKENS
        or metrics_policy.artifact_reference != PHASE_F1_POLICY_ARTIFACT
    ):
        raise PhaseF3AValidationError("Phase F1 metrics policy differs from the approved contract")
    if (
        construction_policy.policy_id != CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ID
        or construction_policy.version != CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_VERSION
        or construction_policy.objective != "CAPITAL_CONSERVATION"
        or construction_policy.strategy != "CAPITAL_DEFENSIVE"
        or construction_policy.status != "APPROVED"
        or construction_policy.runtime_construction_readiness != "NOT_IMPLEMENTED"
        or construction_policy.fingerprint != _APPROVED_CONSTRUCTION_POLICY_FINGERPRINT
        or construction_policy.artifact_reference
        != CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
    ):
        raise PhaseF3AValidationError("construction policy differs from the approved contract")

    payload = metrics_policy.artifact_payload()
    decision = _mapping(payload.get("decision_context"), "decision_context")
    alignment = _mapping(payload.get("alignment"), "alignment")
    dynamics = _mapping(payload.get("portfolio_dynamics"), "portfolio_dynamics")
    cash = _mapping(payload.get("cash"), "cash")
    eligibility = _mapping(payload.get("eligibility"), "eligibility")
    precision = _mapping(payload.get("precision"), "precision")
    construction_payload = construction_policy.artifact_payload()
    allocation = _mapping(construction_payload.get("allocation"), "construction allocation")
    historical_nav = _mapping(
        construction_payload.get("historical_nav"), "construction historical_nav"
    )

    required_text = {
        "alignment.constituent_dates": (alignment.get("constituent_dates"), "STRICT_EIGHT_WAY_INTERSECTION"),
        "alignment.endpoints": (alignment.get("endpoints"), "OBSERVED_NAV_DATES_ONLY"),
        "alignment.interpolation": (alignment.get("interpolation"), "PROHIBITED"),
        "alignment.nearest_date_substitution": (
            alignment.get("nearest_date_substitution"),
            "PROHIBITED",
        ),
        "portfolio_dynamics.method": (dynamics.get("method"), "BUY_AND_HOLD_WEIGHT_DRIFT"),
        "portfolio_dynamics.periodic_rebalancing": (
            dynamics.get("periodic_rebalancing"),
            "PROHIBITED",
        ),
        "cash.return_treatment": (cash.get("return_treatment"), "UNREMUNERATED_NOMINAL_CASH"),
        "precision.input_boundary": (precision.get("input_boundary"), "EXACT_DECIMAL_TEXT"),
        "precision.intermediate_quantization": (
            precision.get("explicit_intermediate_quantization"),
            "PROHIBITED",
        ),
        "precision.rounding": (precision.get("decimal_rounding_mode"), "ROUND_HALF_EVEN"),
        "precision.source_resolution_metadata": (
            precision.get("source_resolution_metadata"),
            "REQUIRED",
        ),
    }
    for label, (actual, expected) in required_text.items():
        if actual != expected:
            raise PhaseF3AValidationError(f"{label} differs from the approved F1 contract")

    security_weight = _policy_decimal(dynamics.get("initial_weight_per_security"), "security weight")
    cash_weight = _policy_decimal(dynamics.get("initial_cash_weight"), "cash weight")
    if (
        security_weight != Decimal("0.10")
        or cash_weight != Decimal("0.20")
        or allocation.get("security_count") != _SECURITY_COUNT
        or _policy_decimal(allocation.get("weight_per_security"), "construction security weight")
        != security_weight
        or _policy_decimal(allocation.get("cash_reserve_weight"), "construction cash weight")
        != cash_weight
    ):
        raise PhaseF3AValidationError("allocation weights differ from the approved policies")
    if security_weight * Decimal(_SECURITY_COUNT) + cash_weight != Decimal(1):
        raise PhaseF3AValidationError("approved allocation weights do not sum to one")

    history_days = _policy_integer(
        eligibility.get("minimum_history_span_calendar_days"), "minimum history"
    )
    intervals = _policy_integer(
        eligibility.get("minimum_aligned_return_intervals"), "minimum intervals"
    )
    staleness = _policy_integer(
        eligibility.get("maximum_nav_staleness_calendar_days"), "maximum staleness"
    )
    if (
        history_days != 365
        or intervals != 252
        or staleness != 30
        or historical_nav.get("minimum_history_span_calendar_days") != history_days
        or historical_nav.get("minimum_aligned_return_intervals") != intervals
        or historical_nav.get("maximum_observation_staleness_calendar_days") != staleness
    ):
        raise PhaseF3AValidationError("history gates differ between approved policies")
    if eligibility.get("latest_minimal_qualifying_window") != "REQUIRED":
        raise PhaseF3AValidationError("latest minimal qualifying window is not required")

    output_quantum = _policy_decimal(
        precision.get("canonical_output_quantum"), "canonical output quantum"
    )
    half_quantum_tolerance = _policy_decimal(
        precision.get("persisted_numeric_half_quantum_tolerance"),
        "persisted numeric half-quantum tolerance",
    )
    serialized_weight_tolerance = _policy_decimal(
        precision.get("independently_serialized_nine_component_weight_tolerance"),
        "serialized nine-weight tolerance",
    )
    if (
        precision.get("decimal_context_precision_significant_digits") != _DECIMAL_PRECISION
        or precision.get("canonical_output_scale_decimal_places") != 18
        or precision.get("canonical_scale_is_economic_accuracy_claim") is not False
        or output_quantum != _OUTPUT_QUANTUM
        or half_quantum_tolerance != _PERSISTED_HALF_QUANTUM_TOLERANCE
        or serialized_weight_tolerance != _SERIALIZED_NINE_WEIGHT_TOLERANCE
    ):
        raise PhaseF3AValidationError("numeric precision differs from the approved F1 contract")

    return _Contract(
        decision_as_of_utc=_policy_text(decision.get("decision_as_of_utc"), "decision as-of"),
        nav_evidence_cutoff=_policy_text(decision.get("nav_evidence_cutoff"), "NAV cutoff"),
        security_weight=security_weight,
        cash_weight=cash_weight,
        minimum_history_days=history_days,
        minimum_intervals=intervals,
        maximum_staleness_days=staleness,
        endpoint_relative_tolerance=_policy_decimal(
            precision.get("endpoint_reconciliation_relative_tolerance"),
            "endpoint tolerance",
        ),
        weight_sum_tolerance=_policy_decimal(
            precision.get("derived_weight_sum_absolute_tolerance"),
            "weight sum tolerance",
        ),
        output_quantum=output_quantum,
        persisted_half_quantum_tolerance=half_quantum_tolerance,
        serialized_nine_weight_tolerance=serialized_weight_tolerance,
    )


def _validate_request(request: SyntheticPortfolioWealthRequest, contract: _Contract) -> None:
    if not _is_synthetic_identity(request.portfolio_identity):
        raise PhaseF3AValidationError("portfolio identity must be explicit synthetic text")
    if not isinstance(request.initial_capital, Decimal) or not request.initial_capital.is_finite():
        raise PhaseF3AValidationError("initial capital must be a finite Decimal")
    if request.initial_capital <= 0:
        raise PhaseF3AValidationError("initial capital must be positive")
    if request.decision_as_of_utc != contract.decision_as_of_utc:
        raise PhaseF3AValidationError("decision as-of differs from the Phase F1 boundary")
    if request.nav_evidence_cutoff != contract.nav_evidence_cutoff:
        raise PhaseF3AValidationError("NAV cutoff differs from the Phase F1 boundary")
    _strict_utc(request.decision_as_of_utc)
    _strict_date(request.nav_evidence_cutoff, "NAV cutoff")
    if not isinstance(request.constituents, tuple) or len(request.constituents) != _SECURITY_COUNT:
        raise PhaseF3AValidationError("exactly eight synthetic constituent series are required")
    if any(not isinstance(item, SyntheticConstituentSeries) for item in request.constituents):
        raise PhaseF3AValidationError("every constituent must use the synthetic series type")
    identities = tuple(item.constituent_identity for item in request.constituents)
    if len(set(identities)) != _SECURITY_COUNT:
        raise PhaseF3AValidationError("synthetic constituent identities must be unique")


def _validate_constituent(
    series: SyntheticConstituentSeries,
    request: SyntheticPortfolioWealthRequest,
) -> _ValidatedConstituent:
    if not isinstance(series, SyntheticConstituentSeries):
        raise PhaseF3AValidationError("constituent input has the wrong type")
    if not _is_synthetic_identity(series.constituent_identity):
        raise PhaseF3AValidationError("constituent identity must be explicit synthetic text")
    if series.currency_code != "EUR":
        raise PhaseF3AValidationError("Phase F3A accepts EUR synthetic constituents only")
    if series.source_identity != _SYNTHETIC_SOURCE_IDENTITY:
        raise PhaseF3AValidationError("synthetic source identity is not approved")
    if series.source_governance != _SYNTHETIC_SOURCE_GOVERNANCE:
        raise PhaseF3AValidationError("synthetic source governance is not approved")
    if (
        not isinstance(series.distribution_state, SyntheticDistributionState)
        or series.distribution_state
        is not SyntheticDistributionState.SIMULATED_ACCUMULATING_SHARE_CLASS
    ):
        raise PhaseF3AValidationError(
            "only simulated accumulating-share-class semantics are supported"
        )
    decision_as_of = _strict_utc(request.decision_as_of_utc)
    if _strict_utc(series.evidence_available_at_utc) > decision_as_of:
        raise PhaseF3AValidationError("synthetic evidence is unavailable at decision as-of")
    if not _is_synthetic_reference(series.series_evidence_reference):
        raise PhaseF3AValidationError("series evidence reference is not explicit synthetic provenance")
    if not _is_hash(series.provenance_fingerprint):
        raise PhaseF3AValidationError("constituent provenance fingerprint is malformed")
    try:
        expected_fingerprint = series.expected_provenance_fingerprint()
    except (DecimalException, TypeError, ValueError) as error:
        raise PhaseF3AValidationError("constituent provenance payload is malformed") from error
    if series.provenance_fingerprint != expected_fingerprint:
        raise PhaseF3AValidationError("constituent provenance fingerprint mismatch")
    if not isinstance(series.observations, tuple) or not series.observations:
        raise PhaseF3AValidationError("constituent observations must be a non-empty tuple")

    cutoff = _strict_date(request.nav_evidence_cutoff, "NAV cutoff")
    dates: list[date] = []
    by_date: dict[date, SyntheticNavObservation] = {}
    previous: date | None = None
    references = {series.series_evidence_reference}
    for observation in series.observations:
        if not isinstance(observation, SyntheticNavObservation):
            raise PhaseF3AValidationError("synthetic observation has the wrong type")
        parsed = _strict_date(observation.observation_date, "observation date")
        if parsed > cutoff:
            raise PhaseF3AValidationError("synthetic observation exceeds the governed NAV cutoff")
        if previous is not None and parsed <= previous:
            raise PhaseF3AValidationError(
                "each complete constituent series must be strictly chronological and unique"
            )
        if not isinstance(observation.nav, Decimal) or not observation.nav.is_finite():
            raise PhaseF3AValidationError("synthetic NAV must be a finite Decimal")
        if observation.nav <= 0:
            raise PhaseF3AValidationError("synthetic NAV must be positive")
        if not _is_synthetic_reference(observation.evidence_reference):
            raise PhaseF3AValidationError("observation provenance is not explicit synthetic evidence")
        if observation.evidence_reference in references:
            raise PhaseF3AValidationError("synthetic evidence references must be unique")
        if not _is_hash(observation.observation_fingerprint):
            raise PhaseF3AValidationError("observation fingerprint is malformed")
        if observation.observation_fingerprint != _observation_fingerprint(
            series.constituent_identity, observation
        ):
            raise PhaseF3AValidationError("observation fingerprint mismatch")
        references.add(observation.evidence_reference)
        dates.append(parsed)
        by_date[parsed] = observation
        previous = parsed
    return _ValidatedConstituent(series=series, dates=tuple(dates), by_date=by_date)


def _select_latest_minimal_window(
    constituents: tuple[_ValidatedConstituent, ...],
    request: SyntheticPortfolioWealthRequest,
    contract: _Contract,
) -> tuple[tuple[date, ...], SelectedWindowProof]:
    common = set(constituents[0].dates)
    for constituent in constituents[1:]:
        common.intersection_update(constituent.dates)
    common_dates = tuple(sorted(common))
    if not common_dates:
        raise PhaseF3AValidationError("the eight complete series have no common observed dates")

    cutoff = _strict_date(request.nav_evidence_cutoff, "NAV cutoff")
    latest = common_dates[-1]
    staleness = (cutoff - latest).days
    if staleness < 0 or staleness > contract.maximum_staleness_days:
        raise PhaseF3AValidationError("latest common observation fails the governed staleness gate")
    available_intervals = len(common_dates) - 1
    if available_intervals < contract.minimum_intervals:
        raise PhaseF3AValidationError(
            "complete common history has fewer than 252 return intervals"
        )
    available_span = (latest - common_dates[0]).days
    if available_span < contract.minimum_history_days:
        raise PhaseF3AValidationError("complete common history spans fewer than 365 calendar days")

    latest_possible_start = len(common_dates) - contract.minimum_intervals - 1
    selected_start: int | None = None
    for index in range(latest_possible_start, -1, -1):
        if (latest - common_dates[index]).days >= contract.minimum_history_days:
            selected_start = index
            break
    if selected_start is None:  # pragma: no cover - guarded by complete-span check
        raise PhaseF3AValidationError("no common window satisfies both governed minima")
    selected = common_dates[selected_start:]
    interval_count = len(selected) - 1
    span = (selected[-1] - selected[0]).days
    if interval_count < contract.minimum_intervals or span < contract.minimum_history_days:
        raise PhaseF3AValidationError("selected common window does not satisfy both governed minima")

    next_index = selected_start + 1
    if next_index < len(common_dates):
        next_intervals = len(common_dates) - next_index - 1
        next_span = (latest - common_dates[next_index]).days
        failures = tuple(
            label
            for label, failed in (
                ("MINIMUM_252_RETURN_INTERVALS", next_intervals < contract.minimum_intervals),
                ("MINIMUM_365_CALENDAR_DAYS", next_span < contract.minimum_history_days),
            )
            if failed
        )
        if not failures:
            raise PhaseF3AValidationError("selected common window is not minimal")
        next_date: str | None = common_dates[next_index].isoformat()
    else:  # pragma: no cover - a qualifying window always has another date
        next_intervals = None
        next_span = None
        failures = ()
        next_date = None
    return selected, SelectedWindowProof(
        complete_common_date_count=len(common_dates),
        selected_start_date=selected[0].isoformat(),
        selected_end_date=selected[-1].isoformat(),
        selected_observation_count=len(selected),
        selected_return_interval_count=interval_count,
        selected_calendar_span_days=span,
        staleness_calendar_days=staleness,
        excluded_leading_common_dates=selected_start,
        next_later_start_date=next_date,
        next_later_start_interval_count=next_intervals,
        next_later_start_span_days=next_span,
        next_later_start_failed_minima=failures,
    )


def _derive_constituents(
    constituents: tuple[_ValidatedConstituent, ...],
    initial_date: date,
    initial_allocation: Decimal,
    tolerance: Decimal,
) -> tuple[ConstituentDerivation, ...]:
    results: list[ConstituentDerivation] = []
    for item in constituents:
        initial_observation = item.by_date[initial_date]
        units = initial_allocation / initial_observation.nav
        reconstructed = units * initial_observation.nav
        relative_error = _relative_error(reconstructed, initial_allocation)
        if relative_error > tolerance:
            raise PhaseF3AValidationError("fixed-unit initial allocation does not reconcile")
        results.append(
            ConstituentDerivation(
                constituent_identity=item.series.constituent_identity,
                complete_series_fingerprint=item.series.provenance_fingerprint,
                complete_observation_count=len(item.series.observations),
                complete_first_date=item.series.observations[0].observation_date,
                complete_last_date=item.series.observations[-1].observation_date,
                distribution_state=item.series.distribution_state.value,
                initial_nav=initial_observation.nav,
                initial_allocation=initial_allocation,
                fixed_mathematical_units=units,
                initial_reconciliation_relative_error=relative_error,
            )
        )
    return tuple(results)


def _derive_wealth_points(
    constituents: tuple[_ValidatedConstituent, ...],
    derivations: tuple[ConstituentDerivation, ...],
    window_dates: tuple[date, ...],
    nominal_cash: Decimal,
    weight_tolerance: Decimal,
    output_quantum: Decimal,
    half_quantum_tolerance: Decimal,
    serialized_weight_tolerance: Decimal,
) -> tuple[PortfolioWealthPoint, ...]:
    derivation_by_identity = {item.constituent_identity: item for item in derivations}
    points: list[PortfolioWealthPoint] = []
    for valuation_date in window_dates:
        raw_components: list[tuple[_ValidatedConstituent, SyntheticNavObservation, Decimal]] = []
        for item in constituents:
            observation = item.by_date[valuation_date]
            units = derivation_by_identity[item.series.constituent_identity].fixed_mathematical_units
            value = units * observation.nav
            if not value.is_finite() or value <= 0:
                raise PhaseF3AValidationError("derived component value must be finite and positive")
            raw_components.append((item, observation, value))
        total_wealth = nominal_cash + sum((item[2] for item in raw_components), Decimal(0))
        if not total_wealth.is_finite() or total_wealth <= 0:
            raise PhaseF3AValidationError("derived portfolio wealth must be finite and positive")
        components = tuple(
            ComponentValue(
                constituent_identity=item.series.constituent_identity,
                nav=observation.nav,
                nav_observation_fingerprint=observation.observation_fingerprint,
                fixed_mathematical_units=(
                    derivation_by_identity[item.series.constituent_identity].fixed_mathematical_units
                ),
                component_value=value,
                derived_weight=value / total_wealth,
            )
            for item, observation, value in raw_components
        )
        cash_weight = nominal_cash / total_wealth
        weight_sum = cash_weight + sum(
            (component.derived_weight for component in components), Decimal(0)
        )
        weight_error = abs(weight_sum - Decimal(1))
        if weight_error > weight_tolerance:
            raise PhaseF3AValidationError("derived component and cash weights do not reconcile")
        serialized_weight_sum = _quantized_output_decimal(
            cash_weight,
            output_quantum,
            half_quantum_tolerance,
        ) + sum(
            (
                _quantized_output_decimal(
                    component.derived_weight,
                    output_quantum,
                    half_quantum_tolerance,
                )
                for component in components
            ),
            Decimal(0),
        )
        serialized_weight_error = abs(serialized_weight_sum - Decimal(1))
        if serialized_weight_error > serialized_weight_tolerance:
            raise PhaseF3AValidationError(
                "independently serialized component and cash weights do not reconcile"
            )
        points.append(
            PortfolioWealthPoint(
                valuation_date=valuation_date.isoformat(),
                components=components,
                nominal_cash=nominal_cash,
                cash_weight=cash_weight,
                total_wealth=total_wealth,
                weight_sum_absolute_error=weight_error,
                serialized_weight_sum_absolute_error=serialized_weight_error,
            )
        )
    return tuple(points)


def _validate_canonical_outputs(
    lineage: SyntheticPortfolioWealthLineage,
    contract: _Contract,
) -> None:
    derived_values: list[Decimal] = [
        lineage.initial_security_allocation,
        lineage.nominal_cash,
    ]
    for constituent in lineage.constituents:
        derived_values.extend(
            (
                constituent.initial_allocation,
                constituent.fixed_mathematical_units,
                constituent.initial_reconciliation_relative_error,
            )
        )
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        for point in lineage.wealth_points:
            derived_values.extend(
                (
                    point.nominal_cash,
                    point.cash_weight,
                    point.total_wealth,
                    point.weight_sum_absolute_error,
                    point.serialized_weight_sum_absolute_error,
                )
            )
            derived_values.extend(
                value
                for component in point.components
                for value in (
                    component.fixed_mathematical_units,
                    component.component_value,
                    component.derived_weight,
                )
            )
            serialized_sum = _quantized_output_decimal(
                point.cash_weight,
                contract.output_quantum,
                contract.persisted_half_quantum_tolerance,
            ) + sum(
                (
                    _quantized_output_decimal(
                        component.derived_weight,
                        contract.output_quantum,
                        contract.persisted_half_quantum_tolerance,
                    )
                    for component in point.components
                ),
                Decimal(0),
            )
            serialized_error = abs(serialized_sum - Decimal(1))
            if (
                point.serialized_weight_sum_absolute_error != serialized_error
                or serialized_error > contract.serialized_nine_weight_tolerance
            ):
                raise PhaseF3AValidationError(
                    "serialized nine-component weight reconciliation is invalid"
                )
    for value in derived_values:
        _quantized_output_decimal(
            value,
            contract.output_quantum,
            contract.persisted_half_quantum_tolerance,
        )


def _observation_fingerprint(
    constituent_identity: str,
    observation: SyntheticNavObservation,
) -> str:
    return canonical_fingerprint(observation.payload(constituent_identity))


def _require_relative_reconciliation(
    actual: Decimal,
    expected: Decimal,
    tolerance: Decimal,
    label: str,
) -> None:
    if _relative_error(actual, expected) > tolerance:
        raise PhaseF3AValidationError(f"{label} does not reconcile within the F1 tolerance")


def _relative_error(actual: Decimal, expected: Decimal) -> Decimal:
    if expected == 0:
        raise PhaseF3AValidationError("relative reconciliation requires a nonzero expected value")
    return abs(actual - expected) / abs(expected)


def _strict_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise PhaseF3AValidationError("timestamp must be canonical UTC text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise PhaseF3AValidationError("timestamp must be canonical UTC text") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
        raise PhaseF3AValidationError("timestamp must be canonical UTC text")
    return parsed


def _strict_date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise PhaseF3AValidationError(f"{label} must be canonical ISO date text")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise PhaseF3AValidationError(f"{label} must be canonical ISO date text") from error
    if parsed.isoformat() != value:
        raise PhaseF3AValidationError(f"{label} must be canonical ISO date text")
    return parsed


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise PhaseF3AValidationError(f"{label} is malformed")
    return value


def _policy_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PhaseF3AValidationError(f"{label} must be exact non-empty text")
    return value


def _policy_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PhaseF3AValidationError(f"{label} must be an integer")
    return value


def _policy_decimal(value: object, label: str) -> Decimal:
    text = _policy_text(value, label)
    try:
        result = Decimal(text)
    except DecimalException as error:
        raise PhaseF3AValidationError(f"{label} must be exact Decimal text") from error
    if not result.is_finite():
        raise PhaseF3AValidationError(f"{label} must be finite")
    return result


def _is_synthetic_identity(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("SYNTHETIC_")
        and value == value.strip()
        and len(value) > len("SYNTHETIC_")
        and all(character.isupper() or character.isdigit() or character in "_:-" for character in value)
    )


def _is_synthetic_reference(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_SYNTHETIC_REFERENCE_PREFIX)
        and value == value.strip()
        and len(value) > len(_SYNTHETIC_REFERENCE_PREFIX)
    )


def _is_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HASH_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _source_decimal_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise PhaseF3AValidationError("source numeric value must be a finite Decimal")
    return format(value, "f")


def _quantized_output_decimal(
    value: Decimal,
    quantum: Decimal = _OUTPUT_QUANTUM,
    half_quantum_tolerance: Decimal = _PERSISTED_HALF_QUANTUM_TOLERANCE,
) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise PhaseF3AValidationError("calculated output must be a finite Decimal")
    try:
        with localcontext() as context:
            context.prec = _DECIMAL_PRECISION
            context.rounding = ROUND_HALF_EVEN
            quantized = value.quantize(quantum)
            quantization_error = abs(value - quantized)
    except DecimalException as error:
        raise PhaseF3AValidationError("calculated output cannot be represented at Q18") from error
    if quantization_error > half_quantum_tolerance:
        raise PhaseF3AValidationError("calculated output exceeds the Q18 half-quantum tolerance")
    return abs(quantized) if quantized == 0 else quantized


def _canonical_output_text(value: Decimal) -> str:
    return format(_quantized_output_decimal(value), "f")


def _enum_text(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)
