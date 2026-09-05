"""Phase F2 governed, Decimal-only portfolio metric computation foundation.

This module consumes an already-governed portfolio-wealth series.  It never
constructs that series from constituents, allocates cash, repairs observations,
or participates in ranking or selection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import (
    ROUND_HALF_EVEN,
    Decimal,
    DecimalException,
    InvalidOperation,
    localcontext,
)
from enum import StrEnum
from typing import Final

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.metrics.policy_contract import (
    PHASE_F1_DECISION_TOKENS,
    PHASE_F1_POLICY_FINGERPRINT,
    PHASE_F1_POLICY_ID,
    PHASE_F1_POLICY_VERSION,
    PhaseF1PortfolioMetricsPolicy,
)

PHASE_F2_IMPLEMENTATION_ID: Final = (
    "PHASE_F2_GOVERNED_CAPITAL_DEFENSIVE_PORTFOLIO_METRIC_COMPUTATION_FOUNDATION"
)
PHASE_F2_IMPLEMENTATION_VERSION: Final = "1.0.0"
PHASE_F2_ACTIVATION_STATE: Final = "METRIC_FOUNDATION_IMPLEMENTED"

_DECIMAL_PRECISION: Final = 50
_OUTPUT_QUANTUM: Final = Decimal("0.000000000000000001")
_ENDPOINT_RELATIVE_TOLERANCE: Final = Decimal("1E-40")
_DAYS_PER_YEAR: Final = Decimal(365)
_HASH_LENGTH: Final = 64
_CANONICAL_METRIC_ORDER: Final = (
    "TOTAL_RETURN",
    "ANNUALIZED_RETURN",
    "ANNUALIZED_VOLATILITY",
    "MAXIMUM_DRAWDOWN",
    "SHARPE_RATIO",
    "SORTINO_RATIO",
    "HISTORICAL_VAR",
    "HISTORICAL_CVAR",
    "DOWNSIDE_DEVIATION",
)
_AVAILABLE_METRICS: Final = frozenset(_CANONICAL_METRIC_ORDER[:4])
_POLICY_BLOCKED_METRICS: Final = frozenset(
    {"SHARPE_RATIO", "SORTINO_RATIO", "DOWNSIDE_DEVIATION"}
)
_UNAUTHORIZED_METRICS: Final = frozenset({"HISTORICAL_VAR", "HISTORICAL_CVAR"})
_DECISION_EXECUTION_MAP: Final = {
    "F1-D01": "BOUNDARY_ONLY_SUPPLEMENTARY_NAV_ADMISSION_NOT_PERFORMED",
    "F1-D02": "EXECUTED_EUR_FIRST_AND_HUF_BLOCKED",
    "F1-D03": "EXECUTED_EXACT_DECISION_AS_OF_AND_NAV_CUTOFF",
    "F1-D04": "BOUNDARY_ONLY_STRICT_INTERSECTION_DECLARATION_VALIDATED",
    "F1-D05": "EXECUTED_OBSERVED_ENDPOINT_INTERVALS_ONLY",
    "F1-D06": "BOUNDARY_ONLY_BUY_AND_HOLD_DECLARATION_VALIDATED",
    "F1-D07": "BOUNDARY_ONLY_UNREMUNERATED_CASH_DECLARATION_VALIDATED",
    "F1-D08": "BOUNDARY_ONLY_ESTR_CALCULATION_NOT_REQUIRED_BY_AVAILABLE_METRICS",
    "F1-D09": "EXECUTED_HUF_FAIL_CLOSED",
    "F1-D10": "EXECUTED_GEOMETRIC_RETURN_AND_ENDPOINT_RECONCILIATION",
    "F1-D11": "EXECUTED_ACT365F_MODEL_BASED_IRREGULAR_VOLATILITY",
    "F1-D12": "POLICY_BLOCKED_SHARPE_FORMULA_INCOMPLETE",
    "F1-D13": "POLICY_BLOCKED_SORTINO_FORMULA_INCOMPLETE",
    "F1-D14": "EXECUTED_NO_PARTIAL_RISK_ADJUSTED_RESULT",
    "F1-D15": "EXECUTED_365_DAY_AND_252_INTERVAL_MINIMA",
    "F1-D16": "EXECUTED_TOTAL_RETURN_SUITABILITY_REQUIRED",
    "F1-D17": "EXECUTED_DECIMAL50_Q18_AND_RECONCILIATION",
    "F1-D18": "BOUNDARY_ONLY_NO_REAL_PORTFOLIO_RANKING",
    "F1-D19": "BOUNDARY_ONLY_SAME_CURRENCY_AND_NO_CROSS_CURRENCY_RANKING",
    "F1-D20": "EXECUTED_RESULT_LINEAGE_WITHOUT_DATABASE_PERSISTENCE",
    "F1-D21": "BOUNDARY_ONLY_LATEST_MINIMAL_WINDOW_REQUIRES_TRUSTED_LINEAGE",
    "F1-D22": "BOUNDARY_ONLY_SCORING_NOT_IMPLEMENTED",
}


class PhaseF2ExecutionMode(StrEnum):
    """Whether a run is a formula fixture or consumes admitted evidence."""

    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    ADMITTED_EVIDENCE = "ADMITTED_EVIDENCE"


class PhaseF2ComputationStatus(StrEnum):
    """Fail-closed metric result states."""

    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INPUT_REJECTED = "INPUT_REJECTED"
    SEMANTICS_NOT_APPROVED = "SEMANTICS_NOT_APPROVED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    UNSUPPORTED_METRIC = "UNSUPPORTED_METRIC"


class ObservationSemantics(StrEnum):
    """Meaning of the supplied numeric observations."""

    PORTFOLIO_WEALTH_TOTAL_RETURN = "PORTFOLIO_WEALTH_TOTAL_RETURN"
    INSTRUMENT_NAV_PRICE_ONLY = "INSTRUMENT_NAV_PRICE_ONLY"


class SourceApprovalState(StrEnum):
    """Admission state at the F2 boundary."""

    ADMITTED_VALIDATED = "ADMITTED_VALIDATED"
    SYNTHETIC_FIXTURE_APPROVED = "SYNTHETIC_FIXTURE_APPROVED"


class MetricSuitabilityState(StrEnum):
    """Whether observations may support total-return portfolio metrics."""

    APPROVED_TOTAL_RETURN_SERIES = "APPROVED_TOTAL_RETURN_SERIES"
    UNKNOWN_DISTRIBUTION_STATUS = "UNKNOWN_DISTRIBUTION_STATUS"
    NOT_PORTFOLIO_WEALTH = "NOT_PORTFOLIO_WEALTH"


class ObservationFingerprintScheme(StrEnum):
    """Origin-specific observation fingerprint contract."""

    PHASE_F2_SYNTHETIC_V1 = "PHASE_F2_SYNTHETIC_V1"
    PHASE_E_NAV_OBSERVATION_VERSION_V1 = "PHASE_E_NAV_OBSERVATION_VERSION_V1"


@dataclass(frozen=True, slots=True)
class GovernedObservation:
    """One exact observed endpoint and its immutable evidence identity."""

    observation_date: str
    value: Decimal
    evidence_reference: str
    observation_fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        observation_date: str,
        value: Decimal,
        evidence_reference: str,
    ) -> GovernedObservation:
        """Create a deterministic observation identity without validating admission."""
        observation = cls(
            observation_date=observation_date,
            value=value,
            evidence_reference=evidence_reference,
            observation_fingerprint="",
        )
        return replace(
            observation,
            observation_fingerprint=_synthetic_observation_fingerprint(observation),
        )

    def provenance_payload(self) -> dict[str, object]:
        return {
            "evidence_reference": self.evidence_reference,
            "observation_date": self.observation_date,
            "observation_fingerprint": self.observation_fingerprint,
            "value": _decimal_text(self.value),
        }


@dataclass(frozen=True, slots=True)
class GovernedMetricSeries:
    """A provenance-bound series supplied to the canonical F2 engine."""

    series_identity: str
    subject_identity: str
    source_identity: str
    source_governance: str
    currency_code: str
    observation_semantics: ObservationSemantics
    source_approval_state: SourceApprovalState
    metric_suitability_state: MetricSuitabilityState
    observation_fingerprint_scheme: ObservationFingerprintScheme
    execution_mode: PhaseF2ExecutionMode
    observations: tuple[GovernedObservation, ...]
    evidence_references: tuple[str, ...]
    decision_as_of_utc: str
    evidence_available_at_utc: str
    nav_evidence_cutoff: str
    alignment_method: str
    window_selection_method: str
    endpoint_method: str
    portfolio_dynamics: str
    cash_return_treatment: str
    provenance_fingerprint: str = ""

    def provenance_payload(self) -> dict[str, object]:
        """Return the complete immutable input identity, excluding its own hash."""
        return {
            "alignment_method": self.alignment_method,
            "cash_return_treatment": self.cash_return_treatment,
            "currency_code": self.currency_code,
            "endpoint_method": self.endpoint_method,
            "evidence_references": list(self.evidence_references),
            "decision_as_of_utc": self.decision_as_of_utc,
            "evidence_available_at_utc": self.evidence_available_at_utc,
            "execution_mode": _enum_text(self.execution_mode),
            "metric_suitability_state": _enum_text(self.metric_suitability_state),
            "observation_fingerprint_scheme": _enum_text(
                self.observation_fingerprint_scheme
            ),
            "observation_semantics": _enum_text(self.observation_semantics),
            "observations": [item.provenance_payload() for item in self.observations],
            "nav_evidence_cutoff": self.nav_evidence_cutoff,
            "portfolio_dynamics": self.portfolio_dynamics,
            "series_identity": self.series_identity,
            "source_approval_state": _enum_text(self.source_approval_state),
            "source_governance": self.source_governance,
            "source_identity": self.source_identity,
            "subject_identity": self.subject_identity,
            "window_selection_method": self.window_selection_method,
        }

    def expected_provenance_fingerprint(self) -> str:
        return canonical_fingerprint(self.provenance_payload())


def bind_series_provenance(series: GovernedMetricSeries) -> GovernedMetricSeries:
    """Return an immutable copy bearing the hash of its exact input payload."""
    return replace(series, provenance_fingerprint=series.expected_provenance_fingerprint())


@dataclass(frozen=True, slots=True)
class GovernedReturnInterval:
    """One return over adjacent, observed, strictly increasing endpoints."""

    start_date: str
    end_date: str
    elapsed_calendar_days: int
    simple_return: Decimal
    log_return: Decimal
    start_observation_fingerprint: str
    end_observation_fingerprint: str


@dataclass(frozen=True, slots=True)
class GovernedMetricResult:
    """One available or explicitly unavailable governed metric."""

    metric_id: str
    status: PhaseF2ComputationStatus
    value: Decimal | None
    units: str
    method_id: str
    time_basis: str
    reason: str | None
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    input_series_identity: str
    input_provenance_fingerprint: str
    source_semantics: str
    observation_count: int
    interval_count: int
    first_effective_observation: str | None
    last_effective_observation: str | None

    def __post_init__(self) -> None:
        if self.status is PhaseF2ComputationStatus.AVAILABLE:
            if self.value is None or not self.value.is_finite():
                raise ValueError("available metric result must contain a finite Decimal")
            if self.reason is not None:
                raise ValueError("available metric result cannot contain a failure reason")
        elif self.value is not None:
            raise ValueError("unavailable metric result cannot contain a numeric value")

    def payload_without_fingerprint(self) -> dict[str, object]:
        return {
            "first_effective_observation": self.first_effective_observation,
            "input_provenance_fingerprint": self.input_provenance_fingerprint,
            "input_series_identity": self.input_series_identity,
            "interval_count": self.interval_count,
            "last_effective_observation": self.last_effective_observation,
            "method_id": self.method_id,
            "metric_id": self.metric_id,
            "observation_count": self.observation_count,
            "policy_fingerprint": self.policy_fingerprint,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "reason": self.reason,
            "source_semantics": self.source_semantics,
            "status": self.status.value,
            "time_basis": self.time_basis,
            "units": self.units,
            "value": _quantized_output(self.value) if self.value is not None else None,
        }

    @property
    def result_fingerprint(self) -> str:
        return canonical_fingerprint(self.payload_without_fingerprint())

    def to_dict(self) -> dict[str, object]:
        return {
            **self.payload_without_fingerprint(),
            "result_fingerprint": self.result_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class GovernedMetricRun:
    """Deterministic multi-metric execution record; never a ranking result."""

    implementation_id: str
    implementation_version: str
    activation_state: str
    execution_mode: str
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    input_series_identity: str
    input_provenance_fingerprint: str
    evidence_references: tuple[str, ...]
    results: tuple[GovernedMetricResult, ...]
    ranking_activation: str = "NOT_ACTIVATED"
    portfolio_selection_activation: str = "NOT_ACTIVATED"
    production_cutover: str = "NOT_AUTHORIZED"

    def payload_without_fingerprint(self) -> dict[str, object]:
        return {
            "activation_state": self.activation_state,
            "evidence_references": list(self.evidence_references),
            "execution_mode": self.execution_mode,
            "implementation_id": self.implementation_id,
            "implementation_version": self.implementation_version,
            "input_provenance_fingerprint": self.input_provenance_fingerprint,
            "input_series_identity": self.input_series_identity,
            "policy_fingerprint": self.policy_fingerprint,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "portfolio_selection_activation": self.portfolio_selection_activation,
            "production_cutover": self.production_cutover,
            "ranking_activation": self.ranking_activation,
            "results": [item.to_dict() for item in self.results],
        }

    @property
    def run_fingerprint(self) -> str:
        return canonical_fingerprint(self.payload_without_fingerprint())

    def to_dict(self) -> dict[str, object]:
        return {**self.payload_without_fingerprint(), "run_fingerprint": self.run_fingerprint}

    def render_audit(self) -> str:
        return canonical_json(self.to_dict()) + "\n"


@dataclass(frozen=True, slots=True)
class _ValidatedSeries:
    dates: tuple[date, ...]
    intervals: tuple[GovernedReturnInterval, ...]


def compute_governed_metrics(
    *,
    series: GovernedMetricSeries,
    requested_metrics: Sequence[str],
    policy: PhaseF1PortfolioMetricsPolicy,
) -> GovernedMetricRun:
    """Execute the one F1-bound F2 metric path without constructing portfolio wealth."""
    ordered_metrics = _ordered_metric_ids(requested_metrics)
    policy_reason = _policy_rejection_reason(policy)
    validation_status: PhaseF2ComputationStatus | None = None
    validation_reason: str | None = None
    validated: _ValidatedSeries | None = None

    if policy_reason is not None:
        validation_status = PhaseF2ComputationStatus.POLICY_BLOCKED
        validation_reason = policy_reason
    else:
        validation_status, validation_reason, validated = _validate_series(series)

    results: list[GovernedMetricResult] = []
    for metric_id in ordered_metrics:
        if metric_id not in _CANONICAL_METRIC_ORDER:
            results.append(
                _unavailable_result(
                    metric_id=metric_id,
                    status=PhaseF2ComputationStatus.UNSUPPORTED_METRIC,
                    reason="metric identifier is not governed by the Phase F1 contract",
                    series=series,
                    policy=policy,
                    validated=validated,
                )
            )
            continue
        if validation_status is not None:
            results.append(
                _unavailable_result(
                    metric_id=metric_id,
                    status=validation_status,
                    reason=validation_reason or "input validation failed",
                    series=series,
                    policy=policy,
                    validated=validated,
                )
            )
            continue
        assert validated is not None
        blocked = _metric_policy_block(metric_id, series)
        if blocked is not None:
            status, reason = blocked
            results.append(
                _unavailable_result(
                    metric_id=metric_id,
                    status=status,
                    reason=reason,
                    series=series,
                    policy=policy,
                    validated=validated,
                )
            )
            continue
        method_minimum = 2 if metric_id == "ANNUALIZED_VOLATILITY" else 1
        if len(validated.intervals) < method_minimum:
            results.append(
                _unavailable_result(
                    metric_id=metric_id,
                    status=PhaseF2ComputationStatus.INSUFFICIENT_DATA,
                    reason=f"{metric_id} requires at least {method_minimum} return interval(s)",
                    series=series,
                    policy=policy,
                    validated=validated,
                )
            )
            continue
        results.append(_calculate_metric(metric_id, series, policy, validated))

    return GovernedMetricRun(
        implementation_id=PHASE_F2_IMPLEMENTATION_ID,
        implementation_version=PHASE_F2_IMPLEMENTATION_VERSION,
        activation_state=PHASE_F2_ACTIVATION_STATE,
        execution_mode=_enum_text(series.execution_mode),
        policy_id=_safe_policy_text(policy, "policy_id"),
        policy_version=_safe_policy_text(policy, "version"),
        policy_fingerprint=_safe_policy_fingerprint(policy),
        input_series_identity=series.series_identity,
        input_provenance_fingerprint=series.provenance_fingerprint,
        evidence_references=series.evidence_references,
        results=tuple(results),
    )


def build_observed_return_intervals(
    series: GovernedMetricSeries,
    *,
    policy: PhaseF1PortfolioMetricsPolicy,
) -> tuple[GovernedReturnInterval, ...]:
    """Expose adjacent observed intervals only through the exact F1 policy boundary."""
    policy_reason = _policy_rejection_reason(policy)
    if policy_reason is not None:
        raise ValueError(policy_reason)
    status, reason, validated = _validate_series(series)
    if status is not None or validated is None:
        raise ValueError(reason or "series is not valid for interval construction")
    return validated.intervals


def _validate_series(
    series: GovernedMetricSeries,
) -> tuple[PhaseF2ComputationStatus | None, str | None, _ValidatedSeries | None]:
    text_fields = (
        series.series_identity,
        series.subject_identity,
        series.source_identity,
        series.source_governance,
        series.currency_code,
    )
    if any(not isinstance(item, str) or not item or item != item.strip() for item in text_fields):
        return PhaseF2ComputationStatus.INPUT_REJECTED, "series identity fields are malformed", None
    if len(series.currency_code) != 3 or series.currency_code != series.currency_code.upper():
        return PhaseF2ComputationStatus.INPUT_REJECTED, "currency code must be uppercase ISO-like text", None
    if not _is_hash(series.provenance_fingerprint):
        return PhaseF2ComputationStatus.INPUT_REJECTED, "series provenance fingerprint is malformed", None
    try:
        expected = series.expected_provenance_fingerprint()
    except (DecimalException, TypeError, ValueError):
        return PhaseF2ComputationStatus.INPUT_REJECTED, "series provenance payload is malformed", None
    if series.provenance_fingerprint != expected:
        return PhaseF2ComputationStatus.INPUT_REJECTED, "series provenance fingerprint mismatch", None
    if not series.evidence_references or len(set(series.evidence_references)) != len(
        series.evidence_references
    ) or any(
        not isinstance(item, str) or not item or item != item.strip()
        for item in series.evidence_references
    ):
        return PhaseF2ComputationStatus.INPUT_REJECTED, "evidence references are malformed", None
    if not isinstance(series.execution_mode, PhaseF2ExecutionMode):
        return PhaseF2ComputationStatus.INPUT_REJECTED, "execution mode is not recognized", None
    if series.execution_mode is PhaseF2ExecutionMode.SYNTHETIC_FIXTURE:
        if series.source_approval_state is not SourceApprovalState.SYNTHETIC_FIXTURE_APPROVED:
            return PhaseF2ComputationStatus.INPUT_REJECTED, "fixture source is not approved", None
        if (
            series.observation_fingerprint_scheme
            is not ObservationFingerprintScheme.PHASE_F2_SYNTHETIC_V1
        ):
            return PhaseF2ComputationStatus.INPUT_REJECTED, "fixture fingerprint scheme differs", None
    elif series.source_approval_state is not SourceApprovalState.ADMITTED_VALIDATED:
        return PhaseF2ComputationStatus.INPUT_REJECTED, "evidence source is not admitted", None
    elif (
        series.observation_fingerprint_scheme
        is not ObservationFingerprintScheme.PHASE_E_NAV_OBSERVATION_VERSION_V1
    ):
        return PhaseF2ComputationStatus.INPUT_REJECTED, "admitted fingerprint scheme differs", None

    try:
        decision_as_of = _strict_utc(series.decision_as_of_utc)
        evidence_available = _strict_utc(series.evidence_available_at_utc)
        evidence_cutoff = date.fromisoformat(series.nav_evidence_cutoff)
    except (TypeError, ValueError):
        return PhaseF2ComputationStatus.INPUT_REJECTED, "decision/evidence time boundary is malformed", None
    if evidence_cutoff.isoformat() != series.nav_evidence_cutoff:
        return PhaseF2ComputationStatus.INPUT_REJECTED, "NAV evidence cutoff is not canonical", None
    if series.execution_mode is PhaseF2ExecutionMode.ADMITTED_EVIDENCE:
        if series.decision_as_of_utc != "2026-09-04T12:24:23.000000Z":
            return PhaseF2ComputationStatus.POLICY_BLOCKED, "decision as-of differs from Phase F1", None
        if series.nav_evidence_cutoff != "2026-08-31":
            return PhaseF2ComputationStatus.POLICY_BLOCKED, "NAV cutoff differs from Phase F1", None
        if evidence_available > decision_as_of:
            return PhaseF2ComputationStatus.INPUT_REJECTED, "evidence was unavailable at decision as-of", None

    parsed_dates: list[date] = []
    previous: date | None = None
    seen: set[date] = set()
    for observation in series.observations:
        try:
            parsed = date.fromisoformat(observation.observation_date)
        except (TypeError, ValueError):
            return PhaseF2ComputationStatus.INPUT_REJECTED, "observation date is malformed", None
        if parsed.isoformat() != observation.observation_date:
            return PhaseF2ComputationStatus.INPUT_REJECTED, "observation date is not canonical", None
        if parsed in seen:
            return PhaseF2ComputationStatus.INPUT_REJECTED, "duplicate observation date is prohibited", None
        if previous is not None and parsed <= previous:
            return PhaseF2ComputationStatus.INPUT_REJECTED, "observations are not chronological", None
        if not isinstance(observation.value, Decimal) or not observation.value.is_finite():
            return PhaseF2ComputationStatus.INPUT_REJECTED, "observation value is not a finite Decimal", None
        if observation.value <= 0:
            return PhaseF2ComputationStatus.INPUT_REJECTED, "wealth/NAV observations must be positive", None
        if (
            not observation.evidence_reference
            or observation.evidence_reference != observation.evidence_reference.strip()
            or not _is_hash(observation.observation_fingerprint)
        ):
            return PhaseF2ComputationStatus.INPUT_REJECTED, "observation provenance is malformed", None
        if observation.evidence_reference not in series.evidence_references:
            return PhaseF2ComputationStatus.INPUT_REJECTED, "observation evidence is not inventoried", None
        if (
            series.execution_mode is PhaseF2ExecutionMode.SYNTHETIC_FIXTURE
            and observation.observation_fingerprint
            != _synthetic_observation_fingerprint(observation)
        ):
            return PhaseF2ComputationStatus.INPUT_REJECTED, "observation fingerprint mismatch", None
        seen.add(parsed)
        parsed_dates.append(parsed)
        previous = parsed

    try:
        intervals = _construct_intervals(series.observations, tuple(parsed_dates))
    except (DecimalException, InvalidOperation, ValueError):
        return PhaseF2ComputationStatus.INPUT_REJECTED, "return interval calculation failed", None
    validated = _ValidatedSeries(tuple(parsed_dates), intervals)

    if (
        series.execution_mode is PhaseF2ExecutionMode.ADMITTED_EVIDENCE
        and parsed_dates
        and parsed_dates[-1] > evidence_cutoff
    ):
        return (
            PhaseF2ComputationStatus.INPUT_REJECTED,
            "observation exceeds the governed NAV evidence cutoff",
            validated,
        )

    if series.observation_semantics is not ObservationSemantics.PORTFOLIO_WEALTH_TOTAL_RETURN:
        return (
            PhaseF2ComputationStatus.SEMANTICS_NOT_APPROVED,
            "F2 metrics require an already-governed portfolio total-return wealth series",
            validated,
        )
    if series.metric_suitability_state is not MetricSuitabilityState.APPROVED_TOTAL_RETURN_SERIES:
        return (
            PhaseF2ComputationStatus.SEMANTICS_NOT_APPROVED,
            "series is not approved for total-return metrics",
            validated,
        )
    if series.execution_mode is PhaseF2ExecutionMode.ADMITTED_EVIDENCE and series.currency_code == "HUF":
        return (
            PhaseF2ComputationStatus.POLICY_BLOCKED,
            "HUF remains blocked pending authoritative HUFONIA convention evidence",
            validated,
        )
    expected_contract = {
        "alignment_method": "STRICT_EIGHT_WAY_INTERSECTION",
        "endpoint_method": "OBSERVED_ENDPOINTS_EXACT_ELAPSED_DAYS",
        "portfolio_dynamics": "BUY_AND_HOLD_WEIGHT_DRIFT",
        "cash_return_treatment": "UNREMUNERATED_NOMINAL_CASH",
        "window_selection_method": "LATEST_MINIMAL_COMMON_365D_252_WINDOW",
    }
    actual_contract = {
        "alignment_method": series.alignment_method,
        "endpoint_method": series.endpoint_method,
        "portfolio_dynamics": series.portfolio_dynamics,
        "cash_return_treatment": series.cash_return_treatment,
        "window_selection_method": series.window_selection_method,
    }
    if actual_contract != expected_contract:
        return (
            PhaseF2ComputationStatus.SEMANTICS_NOT_APPROVED,
            "series construction metadata differs from the Phase F1 contract",
            validated,
        )
    if series.execution_mode is PhaseF2ExecutionMode.ADMITTED_EVIDENCE:
        if not parsed_dates:
            return PhaseF2ComputationStatus.INSUFFICIENT_DATA, "admitted series is empty", validated
        span = (parsed_dates[-1] - parsed_dates[0]).days
        staleness = (evidence_cutoff - parsed_dates[-1]).days
        if staleness > 30:
            return (
                PhaseF2ComputationStatus.INSUFFICIENT_DATA,
                "latest admitted observation is more than 30 calendar days before cutoff",
                validated,
            )
        if len(intervals) < 252 or span < 365:
            return (
                PhaseF2ComputationStatus.INSUFFICIENT_DATA,
                "admitted evidence requires 252 intervals and 365 days in the same window",
                validated,
            )
        return (
            PhaseF2ComputationStatus.POLICY_BLOCKED,
            "no trusted admitted portfolio-wealth lineage artifact exists in Phase F2",
            validated,
        )
    return None, None, validated


def _construct_intervals(
    observations: tuple[GovernedObservation, ...],
    dates: tuple[date, ...],
) -> tuple[GovernedReturnInterval, ...]:
    intervals: list[GovernedReturnInterval] = []
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        for index in range(1, len(observations)):
            previous = observations[index - 1]
            current = observations[index]
            elapsed = (dates[index] - dates[index - 1]).days
            if elapsed <= 0:
                raise ValueError("return interval must have positive elapsed time")
            factor = current.value / previous.value
            if not factor.is_finite() or factor <= 0:
                raise ValueError("return factor must be finite and positive")
            intervals.append(
                GovernedReturnInterval(
                    start_date=previous.observation_date,
                    end_date=current.observation_date,
                    elapsed_calendar_days=elapsed,
                    simple_return=factor - Decimal(1),
                    log_return=factor.ln(),
                    start_observation_fingerprint=previous.observation_fingerprint,
                    end_observation_fingerprint=current.observation_fingerprint,
                )
            )
    return tuple(intervals)


def _calculate_metric(
    metric_id: str,
    series: GovernedMetricSeries,
    policy: PhaseF1PortfolioMetricsPolicy,
    validated: _ValidatedSeries,
) -> GovernedMetricResult:
    metadata = _metric_metadata(metric_id)
    try:
        if metric_id == "TOTAL_RETURN":
            value = _total_return(series, validated.intervals)
        elif metric_id == "ANNUALIZED_RETURN":
            value = _annualized_return(series, validated.dates)
        elif metric_id == "ANNUALIZED_VOLATILITY":
            value = _d11_volatility(validated.intervals)
        elif metric_id == "MAXIMUM_DRAWDOWN":
            value = _maximum_drawdown(series.observations)
        else:  # pragma: no cover - guarded by policy inventory
            raise ValueError("metric is not available")
        if not value.is_finite():
            raise ValueError("calculated metric is not finite")
        _quantized_output(value)
    except (DecimalException, InvalidOperation, ValueError):
        return _unavailable_result(
            metric_id=metric_id,
            status=PhaseF2ComputationStatus.INPUT_REJECTED,
            reason="governed Decimal calculation failed",
            series=series,
            policy=policy,
            validated=validated,
        )
    return GovernedMetricResult(
        metric_id=metric_id,
        status=PhaseF2ComputationStatus.AVAILABLE,
        value=value,
        units=metadata[0],
        method_id=metadata[1],
        time_basis=metadata[2],
        reason=None,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_fingerprint=policy.fingerprint,
        input_series_identity=series.series_identity,
        input_provenance_fingerprint=series.provenance_fingerprint,
        source_semantics=_enum_text(series.observation_semantics),
        observation_count=len(series.observations),
        interval_count=len(validated.intervals),
        first_effective_observation=validated.dates[0].isoformat(),
        last_effective_observation=validated.dates[-1].isoformat(),
    )


def _total_return(
    series: GovernedMetricSeries,
    intervals: tuple[GovernedReturnInterval, ...],
) -> Decimal:
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        chain_factor = Decimal(1)
        for interval in intervals:
            chain_factor *= Decimal(1) + interval.simple_return
        endpoint_factor = series.observations[-1].value / series.observations[0].value
        relative_error = abs(chain_factor - endpoint_factor) / abs(endpoint_factor)
        if relative_error > _ENDPOINT_RELATIVE_TOLERANCE:
            raise ValueError("geometric chain does not reconcile to endpoint wealth")
        return chain_factor - Decimal(1)


def _annualized_return(series: GovernedMetricSeries, dates: tuple[date, ...]) -> Decimal:
    elapsed_days = (dates[-1] - dates[0]).days
    if elapsed_days <= 0:
        raise ValueError("annualized return requires positive elapsed time")
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        factor = series.observations[-1].value / series.observations[0].value
        exponent = _DAYS_PER_YEAR / Decimal(elapsed_days)
        return (factor.ln() * exponent).exp() - Decimal(1)


def _d11_volatility(intervals: tuple[GovernedReturnInterval, ...]) -> Decimal:
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        time_fractions = tuple(
            Decimal(item.elapsed_calendar_days) / _DAYS_PER_YEAR for item in intervals
        )
        total_time = sum(time_fractions, Decimal(0))
        drift = sum((item.log_return for item in intervals), Decimal(0)) / total_time
        residual_sum = sum(
            (
                (item.log_return - drift * time_fraction) ** 2
                / time_fraction
            )
            for item, time_fraction in zip(intervals, time_fractions, strict=True)
        )
        variance = residual_sum / Decimal(len(intervals) - 1)
        if variance < 0:
            raise ValueError("D11 variance cannot be negative")
        return variance.sqrt()


def _maximum_drawdown(observations: tuple[GovernedObservation, ...]) -> Decimal:
    peak = observations[0].value
    drawdown = Decimal(0)
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        for observation in observations:
            peak = max(peak, observation.value)
            current = observation.value / peak - Decimal(1)
            drawdown = min(drawdown, current)
    return drawdown


def _metric_policy_block(
    metric_id: str,
    series: GovernedMetricSeries,
) -> tuple[PhaseF2ComputationStatus, str] | None:
    if metric_id in _POLICY_BLOCKED_METRICS:
        return (
            PhaseF2ComputationStatus.POLICY_BLOCKED,
            "Phase F1 does not define a complete irregular-interval denominator and annualization formula",
        )
    if metric_id in _UNAUTHORIZED_METRICS:
        return (
            PhaseF2ComputationStatus.UNSUPPORTED_METRIC,
            "Phase F1 authorizes no confidence, quantile, or tail-conditioning method",
        )
    if metric_id not in _AVAILABLE_METRICS:
        return PhaseF2ComputationStatus.UNSUPPORTED_METRIC, "metric is not implemented by Phase F2"
    if series.execution_mode is PhaseF2ExecutionMode.ADMITTED_EVIDENCE and series.currency_code != "EUR":
        return PhaseF2ComputationStatus.POLICY_BLOCKED, "Phase F2 admitted-evidence delivery is EUR-first"
    return None


def _unavailable_result(
    *,
    metric_id: str,
    status: PhaseF2ComputationStatus,
    reason: str,
    series: GovernedMetricSeries,
    policy: PhaseF1PortfolioMetricsPolicy,
    validated: _ValidatedSeries | None,
) -> GovernedMetricResult:
    metadata = _metric_metadata(metric_id)
    dates = validated.dates if validated is not None else ()
    interval_count = len(validated.intervals) if validated is not None else 0
    return GovernedMetricResult(
        metric_id=metric_id,
        status=status,
        value=None,
        units=metadata[0],
        method_id=metadata[1],
        time_basis=metadata[2],
        reason=reason,
        policy_id=_safe_policy_text(policy, "policy_id"),
        policy_version=_safe_policy_text(policy, "version"),
        policy_fingerprint=_safe_policy_fingerprint(policy),
        input_series_identity=series.series_identity,
        input_provenance_fingerprint=series.provenance_fingerprint,
        source_semantics=_enum_text(series.observation_semantics),
        observation_count=len(series.observations),
        interval_count=interval_count,
        first_effective_observation=dates[0].isoformat() if dates else None,
        last_effective_observation=dates[-1].isoformat() if dates else None,
    )


def _metric_metadata(metric_id: str) -> tuple[str, str, str]:
    values = {
        "TOTAL_RETURN": (
            "DECIMAL_RETURN",
            "GEOMETRIC_CHAIN_ENDPOINT_RECONCILE",
            "OBSERVED_ENDPOINTS",
        ),
        "ANNUALIZED_RETURN": (
            "DECIMAL_ANNUAL_RETURN",
            "GEOMETRIC_ELAPSED_CALENDAR_TIME",
            "ACT_365_FIXED",
        ),
        "ANNUALIZED_VOLATILITY": (
            "DECIMAL_ANNUAL_VOLATILITY",
            "ACT365F_LOG_ELAPSED_TIME_SAMPLE_VOLATILITY",
            "ACT_365_FIXED",
        ),
        "MAXIMUM_DRAWDOWN": (
            "NON_POSITIVE_DECIMAL_LOSS",
            "OBSERVED_WEALTH_RUNNING_PEAK",
            "OBSERVED_ENDPOINTS",
        ),
        "SHARPE_RATIO": ("DIMENSIONLESS", "POLICY_BLOCKED", "EXACT_INTERVAL_REQUIRED"),
        "SORTINO_RATIO": ("DIMENSIONLESS", "POLICY_BLOCKED", "EXACT_INTERVAL_REQUIRED"),
        "DOWNSIDE_DEVIATION": (
            "DECIMAL_ANNUAL_DOWNSIDE",
            "POLICY_BLOCKED",
            "UNSPECIFIED_BY_F1",
        ),
        "HISTORICAL_VAR": ("DECIMAL_LOSS", "NOT_AUTHORIZED_BY_F1", "UNSPECIFIED_BY_F1"),
        "HISTORICAL_CVAR": (
            "DECIMAL_LOSS",
            "NOT_AUTHORIZED_BY_F1",
            "UNSPECIFIED_BY_F1",
        ),
    }
    return values.get(metric_id, ("NONE", "UNSUPPORTED", "NONE"))


def _policy_rejection_reason(policy: PhaseF1PortfolioMetricsPolicy) -> str | None:
    try:
        if policy.policy_id != PHASE_F1_POLICY_ID:
            return "Phase F1 policy identity mismatch"
        if policy.version != PHASE_F1_POLICY_VERSION:
            return "Phase F1 policy version mismatch"
        if policy.fingerprint != PHASE_F1_POLICY_FINGERPRINT:
            return "Phase F1 canonical fingerprint mismatch"
        if dict(policy.decision_tokens) != PHASE_F1_DECISION_TOKENS:
            return "Phase F1 decision-token mapping mismatch"
    except (AssertionError, KeyError, TypeError, ValueError):
        return "Phase F1 policy is missing required execution content"
    return None


def _ordered_metric_ids(requested_metrics: Sequence[str]) -> tuple[str, ...]:
    values = tuple(requested_metrics)
    if not values:
        return ()
    if any(not isinstance(item, str) or not item or item != item.strip() for item in values):
        return ("<INVALID_METRIC_ID>",)
    if len(set(values)) != len(values):
        return ("<DUPLICATE_METRIC_ID>",)
    order = {name: index for index, name in enumerate(_CANONICAL_METRIC_ORDER)}
    return tuple(sorted(values, key=lambda item: (order.get(item, len(order)), item)))


def _safe_policy_text(policy: PhaseF1PortfolioMetricsPolicy, attribute: str) -> str:
    try:
        value = getattr(policy, attribute)
    except (AssertionError, KeyError, TypeError, ValueError):
        return "UNAVAILABLE"
    return value if isinstance(value, str) else "UNAVAILABLE"


def _safe_policy_fingerprint(policy: PhaseF1PortfolioMetricsPolicy) -> str:
    try:
        value = policy.fingerprint
    except (AssertionError, KeyError, TypeError, ValueError):
        return "UNAVAILABLE"
    return value if isinstance(value, str) else "UNAVAILABLE"


def _quantized_output(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        quantized = value.quantize(_OUTPUT_QUANTUM)
    if quantized == 0:
        quantized = abs(quantized)
    return format(quantized, "f")


def _decimal_text(value: object) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("numeric observations must be finite Decimal values")
    return str(value)


def _synthetic_observation_fingerprint(observation: GovernedObservation) -> str:
    return canonical_fingerprint(
        {
            "evidence_reference": observation.evidence_reference,
            "observation_date": observation.observation_date,
            "value": _decimal_text(observation.value),
        }
    )


def _enum_text(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


def _is_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HASH_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp is not text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp is not UTC")
    permitted = {
        parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        parsed.astimezone(UTC).isoformat(timespec="microseconds"),
    }
    if value not in permitted:
        raise ValueError("timestamp is not fixed-width UTC")
    return parsed


def phase_f2_contract_inventory() -> Mapping[str, object]:
    """Return deterministic implementation capabilities without activating later phases."""
    return {
        "activation_state": PHASE_F2_ACTIVATION_STATE,
        "decision_execution": dict(sorted(_DECISION_EXECUTION_MAP.items())),
        "implementation_id": PHASE_F2_IMPLEMENTATION_ID,
        "implementation_version": PHASE_F2_IMPLEMENTATION_VERSION,
        "policy_blocked_metrics": sorted(_POLICY_BLOCKED_METRICS),
        "production_cutover": "NOT_AUTHORIZED",
        "ranking_activation": "NOT_ACTIVATED",
        "supported_metrics": sorted(_AVAILABLE_METRICS),
        "unauthorized_metrics": sorted(_UNAUTHORIZED_METRICS),
    }
