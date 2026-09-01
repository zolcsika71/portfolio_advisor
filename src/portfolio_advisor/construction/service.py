"""Governed Milestone 11B construction orchestration without portfolio metrics."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ID,
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_VERSION,
    CapitalDefensiveConstructionPolicy,
    validate_construction_cash_input,
)

from .allocation import allocate_holding
from .candidate_generator import select_highest_ranked_feasible_set
from .constraints import (
    ConstructionValidationError,
    nav_failure_codes,
    policy_decimal,
    policy_integer,
    validate_instrument_identity,
)
from .models import (
    CapitalConservationShortlist,
    ConstructedPortfolioCandidate,
    ConstructionEvidenceReadiness,
    ConstructionReasonCode,
    ConstructionResult,
    ConstructionRuntimeStatus,
    RankedConstructionInstrument,
    ShortlistConstructionProvenance,
)
from .validation import validate_constructed_candidate

_REASON_ORDER = tuple(ConstructionReasonCode)


def construct_capital_defensive_portfolio(
    *,
    screening: CapitalConservationShortlist,
    cash_by_currency: Mapping[str, Decimal],
    policy: CapitalDefensiveConstructionPolicy,
    instruments: tuple[RankedConstructionInstrument, ...],
    readiness: ConstructionEvidenceReadiness,
) -> ConstructionResult:
    """Construct one normalized candidate or return an explicit governed state."""
    currency, _private_amount = validate_construction_cash_input(policy, cash_by_currency)
    _validate_policy_identity(policy)
    provenance = ShortlistConstructionProvenance(
        shortlist_snapshot_id=screening.provenance.snapshot_id,
        snapshot_date=screening.provenance.snapshot_date,
        source_file_sha256=screening.provenance.source_file_sha256,
        source_sheet_name=screening.provenance.source_sheet_name,
        shortlist_manifest_fingerprint=screening.provenance.shortlist_manifest_fingerprint,
        shortlist_integration_version=screening.provenance.shortlist_integration_version,
    )
    screened = tuple(
        item for item in screening.candidates if item.eligible and item.rank is not None
    )
    evidence_by_isin = {item.isin: item for item in instruments}
    if len(evidence_by_isin) != len(instruments) or set(evidence_by_isin) != {
        item.isin for item in screened
    }:
        return _empty_result(
            ConstructionRuntimeStatus.REJECTED,
            (ConstructionReasonCode.INVALID_SCREENING_OR_LINEAGE_EVIDENCE,),
            len(screened),
            0,
        )
    try:
        for ranked in screened:
            evidence = evidence_by_isin[ranked.isin]
            validate_instrument_identity(evidence, provenance)
            if (
                evidence.instrument_id != ranked.instrument_id
                or evidence.rank != ranked.rank
                or evidence.shortlist_entry_id != ranked.lineage.shortlist_entry_id
                or evidence.source_occurrence_ids != ranked.lineage.source_occurrence_ids
            ):
                raise ConstructionValidationError(
                    "screening result and construction lineage differ"
                )
    except ConstructionValidationError:
        return _empty_result(
            ConstructionRuntimeStatus.REJECTED,
            (ConstructionReasonCode.INVALID_SCREENING_OR_LINEAGE_EVIDENCE,),
            len(screened),
            0,
        )

    same_currency = tuple(item for item in instruments if item.currency == currency)
    if any(item.group is None for item in same_currency):
        return _empty_result(
            ConstructionRuntimeStatus.REJECTED,
            (ConstructionReasonCode.INVALID_CATEGORY_EVIDENCE,),
            len(screened),
            _admitted_nav_count(instruments),
        )

    failures: set[ConstructionReasonCode] = set()
    qualifying: list[RankedConstructionInstrument] = []
    for item in same_currency:
        item_failures = nav_failure_codes(item, provenance.snapshot_date, policy)
        failures.update(item_failures)
        if not item_failures:
            qualifying.append(item)
    required = policy_integer(policy, "allocation", "security_count")
    if len(same_currency) < required:
        failures.add(ConstructionReasonCode.INSUFFICIENT_SAME_CURRENCY_INSTRUMENTS)
    if len(qualifying) < required:
        failures.add(ConstructionReasonCode.INSUFFICIENT_ADMITTED_NAV_COVERAGE)
    if not (
        readiness.official_reference_rate_observations_validated
        and readiness.official_reference_rate_methodology_validated
    ):
        failures.add(ConstructionReasonCode.MISSING_OFFICIAL_REFERENCE_RATE_EVIDENCE)
    if not readiness.portfolio_risk_metrics_available:
        failures.add(ConstructionReasonCode.UNAVAILABLE_PORTFOLIO_RISK_METRICS)
    if (
        ConstructionReasonCode.MISSING_OFFICIAL_REFERENCE_RATE_EVIDENCE in failures
        or ConstructionReasonCode.UNAVAILABLE_PORTFOLIO_RISK_METRICS in failures
    ):
        return _empty_result(
            ConstructionRuntimeStatus.IMPLEMENTED_BLOCKED_BY_DATA,
            _ordered(failures),
            len(screened),
            _admitted_nav_count(instruments),
        )
    if len(qualifying) < required:
        return _empty_result(
            ConstructionRuntimeStatus.UNAVAILABLE,
            _ordered(failures),
            len(screened),
            _admitted_nav_count(instruments),
        )

    selected = select_highest_ranked_feasible_set(tuple(qualifying), policy)
    if selected is None:
        reason = (
            ConstructionReasonCode.NO_COMMON_ALIGNED_RETURN_WINDOW
            if select_highest_ranked_feasible_set(
                tuple(qualifying), policy, require_common_nav_window=False
            )
            is not None
            else ConstructionReasonCode.NO_FEASIBLE_DIVERSIFIED_SET
        )
        return _empty_result(
            ConstructionRuntimeStatus.UNAVAILABLE,
            (reason,),
            len(screened),
            _admitted_nav_count(instruments),
        )
    eligible_fingerprint = canonical_fingerprint(
        [item.fingerprint_payload() for item in sorted(qualifying, key=lambda item: (item.rank, item.isin))]
    )
    selected_fingerprint = canonical_fingerprint(sorted(item.isin for item in selected))
    candidate = ConstructedPortfolioCandidate(
        objective=policy.objective,
        strategy=policy.strategy,
        currency=currency,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_fingerprint=policy.fingerprint,
        provenance=provenance,
        eligible_universe_fingerprint=eligible_fingerprint,
        selected_universe_fingerprint=selected_fingerprint,
        holdings=tuple(allocate_holding(item, policy) for item in selected),
        cash_weight=policy_decimal(policy, "allocation", "cash_reserve_weight"),
    )
    validate_constructed_candidate(candidate, policy)
    return ConstructionResult(
        status=ConstructionRuntimeStatus.CONSTRUCTED_VALIDATED,
        reason_codes=(),
        candidate=candidate,
        screened_eligible_count=len(screened),
        admitted_nav_instrument_count=_admitted_nav_count(instruments),
    )


def _validate_policy_identity(policy: CapitalDefensiveConstructionPolicy) -> None:
    if (
        policy.policy_id != CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ID
        or policy.version != CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_VERSION
        or policy.objective != "CAPITAL_CONSERVATION"
        or policy.strategy != "CAPITAL_DEFENSIVE"
        or policy.status != "APPROVED"
    ):
        raise ConstructionValidationError("construction policy identity is not approved")


def _admitted_nav_count(instruments: tuple[RankedConstructionInstrument, ...]) -> int:
    return sum(
        item.nav.quality == "ADMITTED_AND_VALIDATED" and bool(item.nav.observation_dates)
        for item in instruments
    )


def _ordered(reasons: set[ConstructionReasonCode]) -> tuple[ConstructionReasonCode, ...]:
    return tuple(reason for reason in _REASON_ORDER if reason in reasons)


def _empty_result(
    status: ConstructionRuntimeStatus,
    reasons: tuple[ConstructionReasonCode, ...],
    screened_count: int,
    admitted_count: int,
) -> ConstructionResult:
    return ConstructionResult(
        status=status,
        reason_codes=reasons,
        candidate=None,
        screened_eligible_count=screened_count,
        admitted_nav_instrument_count=admitted_count,
    )
