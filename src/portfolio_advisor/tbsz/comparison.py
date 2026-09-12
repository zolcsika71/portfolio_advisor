"""Read-only target-allocation comparison for observed TBSZ evidence.

This module intentionally derives all output in memory. It neither acquires
market/FX data nor alters the source snapshot or manual transaction ledgers.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Final

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.database.repository import (
    HoldingObservation,
    ModelPortfolioRepository,
    RepositoryError,
)
from portfolio_advisor.objectives.models import PortfolioObjective
from portfolio_advisor.ranking.config import load_ranking_rules

from .ltia_reconciliation import (
    ValidatedIdentityConfirmation,
    ValidatedIdentityConfirmationStore,
    load_validated_identity_confirmations,
    normalize_name,
)
from .models import ComparisonAction, IdentityStatus, Instrument, PositionSnapshot
from .repository import TbszPortfolioRepository
from .service import CurrentAccountState, current_account_state


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """One advisory-only current-vs-target allocation gap."""

    account: str
    account_provenance: tuple[str, ...]
    instrument_id: int | None
    instrument_ids: tuple[int, ...]
    asset_name: str
    isin: str | None
    currency: str | None
    asset_currency: str | None
    current_value: Decimal | None
    current_weight: Decimal | None
    target_weight: Decimal | None
    weight_difference: Decimal | None
    target_value: Decimal | None
    estimated_trade_value: Decimal | None
    roi: Decimal | None
    action: ComparisonAction
    identity_status: str
    comparison_status: str
    data_quality_status: str


@dataclass(frozen=True, slots=True)
class CashBalance:
    """Cash remains evidence separate from investable position comparisons."""

    account: str
    currency: str
    balance: Decimal
    source_snapshot_id: int
    source_date: date | None
    data_quality_status: str


@dataclass(frozen=True, slots=True)
class PortfolioComparison:
    account_labels: tuple[str, ...]
    account_scope: str
    comparison_timestamp: datetime
    model_observation_date: date
    target_portfolio_name: str
    policy_version: str
    policy_fingerprint: str
    tolerance: Decimal
    comparison_currency: str | None
    total_comparison_value: Decimal | None
    rows: tuple[ComparisonRow, ...]
    cash_by_currency: tuple[CashBalance, ...]
    unmapped_current_holdings: tuple[str, ...]
    unmapped_target_holdings: tuple[str, ...]
    identity_blockers: tuple[str, ...]
    fx_blockers: tuple[str, ...]
    manual_transaction_blockers: tuple[str, ...]


class DescriptiveStrategy(StrEnum):
    """Exact user-facing strategy labels for the bounded descriptive mode."""

    CAPITAL_PRESERVATION = "CAPITAL_PRESERVATION"
    DIVIDEND_MAXIMISATION = "DIVIDEND_MAXIMISATION"

    @classmethod
    def parse(cls, value: str) -> DescriptiveStrategy:
        """Parse only the two exact descriptive-mode labels."""
        try:
            return cls(value)
        except ValueError as error:
            raise ValueError(f"unsupported descriptive strategy: {value!r}") from error


DESCRIPTIVE_STRATEGY_OBJECTIVES: Final = {
    DescriptiveStrategy.CAPITAL_PRESERVATION: PortfolioObjective.CAPITAL_CONSERVATION,
    DescriptiveStrategy.DIVIDEND_MAXIMISATION: PortfolioObjective.DIVIDEND_PORTFOLIO,
}


@dataclass(frozen=True, slots=True)
class DescriptiveTargetAllocation:
    """One source-reported target row, retained without renormalization."""

    asset_name: str
    isin: str | None
    asset_currency: str | None
    asset_class: str | None
    source_weight: Decimal | None
    allocation_type: str
    support_status: str


@dataclass(frozen=True, slots=True)
class DescriptiveIdentityProvenance:
    """Source of an effective descriptive identity, without changing raw evidence."""

    rule: str
    source: str
    confirmed_by: str | None
    confirmed_at: str | None
    source_support: str | None
    confirmation_store_fingerprint: str | None
    identity_registry_audit_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class DescriptiveComparisonRow:
    """An action-free allocation gap over comparable security values."""

    account: str
    account_provenance: tuple[str, ...]
    instrument_id: int | None
    instrument_ids: tuple[int, ...]
    asset_name: str
    isin: str | None
    raw_isin: str | None
    currency: str | None
    asset_currency: str | None
    current_value: Decimal | None
    current_weight: Decimal | None
    source_target_weight: Decimal | None
    weight_gap: Decimal | None
    target_value: Decimal | None
    signed_monetary_gap: Decimal | None
    roi: Decimal | None
    identity_status: str
    raw_identity_status: str
    identity_provenance: DescriptiveIdentityProvenance | None
    comparison_status: str
    data_quality_status: str


@dataclass(frozen=True, slots=True)
class DescriptivePortfolioComparison:
    """Read-only, single-account description; never a recommendation or proposal."""

    mode: str
    account_label: str
    report_timestamp: datetime
    strategy: DescriptiveStrategy
    internal_objective: PortfolioObjective
    investment_horizon_days: int
    investment_horizon_status: str
    metric_lookback_status: str
    nav_cutoff_status: str
    reported_annual_indicators_status: str
    model_observation_date: date
    model_observation_age_days: int
    target_portfolio_name: str
    reference_selection_status: str
    strategy_policy_availability: str
    strategy_policy_id: str | None
    strategy_policy_version: str | None
    strategy_policy_fingerprint: str | None
    descriptive_comparison_availability: str
    strategy_ranking_availability: str
    shortlist_selection_availability: str
    recommendation_availability: str
    trade_proposal_availability: str
    denominator_basis: str
    target_weight_treatment: str
    comparison_currency: str | None
    total_comparable_security_value: Decimal | None
    position_source_snapshot_id: int | None
    position_source_date: date | None
    position_source_age_days: int | None
    cash_source_snapshot_id: int | None
    cash_source_date: date | None
    cash_source_age_days: int | None
    cash_treatment: str
    identity_confirmation_store_fingerprint: str | None
    identity_registry_audit_fingerprint: str | None
    identity_confirmation_validation_blockers: tuple[str, ...]
    identity_resolution_blockers: tuple[str, ...]
    rows: tuple[DescriptiveComparisonRow, ...]
    target_allocations: tuple[DescriptiveTargetAllocation, ...]
    cash_by_currency: tuple[CashBalance, ...]
    identity_blockers: tuple[str, ...]
    valuation_blockers: tuple[str, ...]
    date_blockers: tuple[str, ...]
    reconciliation_blockers: tuple[str, ...]
    target_completeness_blockers: tuple[str, ...]
    cash_blockers: tuple[str, ...]
    strategy_blockers: tuple[str, ...]
    unresolved_constraints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TargetAllocation:
    isin: str
    asset_name: str
    asset_currency: str | None
    weight: Decimal


@dataclass(frozen=True, slots=True)
class _ValuedPosition:
    state: CurrentAccountState
    position: PositionSnapshot
    value: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class _DescriptiveTargetSet:
    allocations: tuple[DescriptiveTargetAllocation, ...]
    security_targets: dict[str, _TargetAllocation]
    completeness_blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DescriptivePolicyFields:
    availability: str
    policy_id: str | None
    version: str | None
    fingerprint: str | None
    ranking_availability: str
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ResolvedDescriptivePosition:
    raw_position: PositionSnapshot
    effective_position: PositionSnapshot | None
    provenance: DescriptiveIdentityProvenance | None
    blocker: str | None


_UNRESOLVED_DESCRIPTIVE_CONSTRAINTS: Final = (
    "RISK_AND_LOSS_LIMITS_UNRESOLVED",
    "RESERVE_REQUIREMENT_UNRESOLVED",
    "CONCENTRATION_LIMITS_UNRESOLVED",
    "DIVIDEND_TREATMENT_UNRESOLVED",
    "FEES_AND_TAXES_UNRESOLVED",
    "SETTLEMENT_EVIDENCE_UNRESOLVED",
)


def compare_tbsz_to_selected_portfolio_descriptively(
    *,
    tbsz_repository: TbszPortfolioRepository,
    model_repository: ModelPortfolioRepository,
    rules_path: Path,
    account_label: str,
    target_portfolio_name: str,
    strategy: DescriptiveStrategy | str,
    investment_horizon_days: int,
    identity_confirmations_path: Path | None = None,
    identity_registry_audit_path: Path | None = None,
) -> DescriptivePortfolioComparison:
    """Describe one account against one explicit model reference without actions.

    The investment horizon is recorded as user context only. It is independent
    of model/NAV dates, metric lookbacks, and evidence sufficiency, and it does
    not activate or imply policy support.
    """
    if not account_label or account_label != account_label.strip():
        raise ValueError("account_label must be an exact non-empty value")
    if (
        not target_portfolio_name
        or target_portfolio_name != target_portfolio_name.strip()
    ):
        raise ValueError("target_portfolio_name must be an exact non-empty value")
    if (
        isinstance(investment_horizon_days, bool)
        or not isinstance(investment_horizon_days, int)
        or investment_horizon_days < 1
        or investment_horizon_days > 365
    ):
        raise ValueError("investment_horizon_days must be an integer from 1 to 365")
    parsed_strategy = (
        strategy
        if isinstance(strategy, DescriptiveStrategy)
        else DescriptiveStrategy.parse(strategy)
    )
    internal_objective = DESCRIPTIVE_STRATEGY_OBJECTIVES[parsed_strategy]
    report_timestamp = datetime.now(UTC)
    state = current_account_state(tbsz_repository, account_label)
    observation_date = model_repository.latest_observation_date()
    holdings = tuple(
        item
        for item in model_repository.load_holdings(observation_date)
        if item.portfolio_name == target_portfolio_name
    )
    if not holdings:
        raise RepositoryError(
            "explicitly selected model portfolio has no holdings at the resolved model snapshot"
        )
    target_set = _descriptive_target_set(holdings)
    policy = _descriptive_policy_fields(parsed_strategy, rules_path)
    confirmation_store = load_validated_identity_confirmations(
        identity_confirmations_path,
        identity_registry_audit_path,
    )
    return _build_descriptive_comparison(
        state=state,
        report_timestamp=report_timestamp,
        observation_date=observation_date,
        target_portfolio_name=target_portfolio_name,
        strategy=parsed_strategy,
        internal_objective=internal_objective,
        investment_horizon_days=investment_horizon_days,
        target_set=target_set,
        policy=policy,
        confirmation_store=confirmation_store,
    )


def compare_tbsz_to_recommended_portfolio(
    *,
    tbsz_repository: TbszPortfolioRepository,
    model_repository: ModelPortfolioRepository,
    rules_path: Path,
    account_label: str | None = None,
    all_tbsz: bool = False,
    target_portfolio_name: str | None = None,
    tolerance: Decimal,
) -> PortfolioComparison:
    """Return an advisory-only comparison without a provider, FX, or DB write."""
    if (account_label is None) == (not all_tbsz):
        raise ValueError("provide exactly one account_label or all_tbsz=True")
    if not tolerance.is_finite() or tolerance < 0 or tolerance > 1:
        raise ValueError("tolerance must be a finite fraction from 0 to 1")
    labels = (
        (account_label,)
        if account_label is not None
        else tuple(account.label for account in tbsz_repository.accounts())
    )
    if not labels:
        raise ValueError("no TBSZ accounts are available")
    states = tuple(current_account_state(tbsz_repository, label) for label in labels)
    (
        observation_date,
        selected_target,
        targets,
        unmapped_targets,
        policy_version,
        policy_fingerprint,
    ) = _target_allocations(model_repository, rules_path, target_portfolio_name)
    return _build_comparison(
        states=states,
        all_tbsz=all_tbsz,
        observation_date=observation_date,
        selected_target=selected_target,
        targets=targets,
        unmapped_targets=unmapped_targets,
        policy_version=policy_version,
        policy_fingerprint=policy_fingerprint,
        tolerance=tolerance,
    )


def _target_allocations(
    repository: ModelPortfolioRepository,
    rules_path: Path,
    requested_name: str | None,
) -> tuple[date, str, dict[str, _TargetAllocation], tuple[str, ...], str, str]:
    """Read exact reported target weights; never optimize or manufacture them."""
    rules = load_ranking_rules(rules_path)
    observation_date = repository.latest_observation_date()
    if requested_name is None:
        result = CapitalPreservationAdvisor(repository, rules_path).evaluate(
            observation_date=observation_date,
            alternative_count=100,
        )
        if result.selected_portfolio is None:
            raise RepositoryError("the active policy produced no selected portfolio")
        selected_name = result.selected_portfolio.metrics.portfolio_name
    else:
        selected_name = requested_name
    holdings = [
        item
        for item in repository.load_holdings(observation_date)
        if item.portfolio_name == selected_name
    ]
    if not holdings:
        raise RepositoryError("selected recommended portfolio has no holdings at the current model snapshot")

    targets: dict[str, _TargetAllocation] = {}
    unmapped: list[str] = []
    for holding in holdings:
        asset_name = holding.product or "UNKNOWN_TARGET_INSTRUMENT"
        if holding.isin is None or not holding.isin.strip() or holding.allocation is None:
            unmapped.append(asset_name)
            continue
        isin = holding.isin.upper()
        weight = Decimal(str(holding.allocation)) / Decimal(100)
        if isin in targets:
            previous = targets[isin]
            if previous.asset_currency != holding.currency:
                raise RepositoryError("selected portfolio has conflicting currencies for one target ISIN")
            targets[isin] = _TargetAllocation(
                isin=isin,
                asset_name=previous.asset_name,
                asset_currency=previous.asset_currency,
                weight=previous.weight + weight,
            )
        else:
            targets[isin] = _TargetAllocation(isin, asset_name, holding.currency, weight)
    fingerprint = hashlib.sha256(rules_path.read_bytes()).hexdigest()
    return (
        observation_date,
        selected_name,
        targets,
        tuple(sorted(set(unmapped))),
        rules.version,
        fingerprint,
    )


def _descriptive_policy_fields(
    strategy: DescriptiveStrategy, rules_path: Path
) -> _DescriptivePolicyFields:
    if strategy is DescriptiveStrategy.DIVIDEND_MAXIMISATION:
        return _DescriptivePolicyFields(
            availability="NO_VALIDATED_ACTIVE_POLICY",
            policy_id=None,
            version=None,
            fingerprint=None,
            ranking_availability="UNAVAILABLE_NO_VALIDATED_ACTIVE_POLICY",
            blockers=("DIVIDEND_POLICY_NOT_VALIDATED_OR_ACTIVE",),
        )
    rules = load_ranking_rules(rules_path)
    return _DescriptivePolicyFields(
        availability="VALIDATED_ACTIVE_POLICY",
        policy_id=rules.policy_name,
        version=rules.version,
        fingerprint=hashlib.sha256(rules_path.read_bytes()).hexdigest(),
        ranking_availability="AVAILABLE_REVIEWED_NOT_RUN_MANUAL_REFERENCE",
        blockers=(),
    )


def _descriptive_target_set(
    holdings: tuple[HoldingObservation, ...],
) -> _DescriptiveTargetSet:
    allocations: list[DescriptiveTargetAllocation] = []
    targets: dict[str, _TargetAllocation] = {}
    blockers: list[str] = []
    total_weight = Decimal()
    total_is_complete = True
    for holding in holdings:
        asset_name = holding.product or "UNKNOWN_TARGET_INSTRUMENT"
        weight = _source_weight(holding.allocation)
        if weight is None:
            blockers.append(f"TARGET_ALLOCATION_INVALID:{asset_name}")
            total_is_complete = False
        else:
            total_weight += weight
        allocation_type = "CASH" if _is_source_cash_allocation(holding) else "SECURITY"
        isin = holding.isin.upper() if holding.isin and holding.isin.strip() else None
        if allocation_type == "CASH":
            support_status = "SOURCE_CASH_ALLOCATION_PRESERVED_SEPARATELY"
        elif isin is None:
            support_status = "UNSUPPORTED_MISSING_EXACT_ISIN"
            blockers.append(f"TARGET_EXACT_ISIN_UNAVAILABLE:{asset_name}")
        else:
            support_status = "SUPPORTED_EXACT_ISIN"
            if weight is not None:
                previous = targets.get(isin)
                if previous is not None and previous.asset_currency != holding.currency:
                    blockers.append(f"TARGET_CURRENCY_CONFLICT:{isin}")
                elif previous is not None:
                    targets[isin] = _TargetAllocation(
                        isin=isin,
                        asset_name=previous.asset_name,
                        asset_currency=previous.asset_currency,
                        weight=previous.weight + weight,
                    )
                else:
                    targets[isin] = _TargetAllocation(
                        isin=isin,
                        asset_name=asset_name,
                        asset_currency=holding.currency,
                        weight=weight,
                    )
        allocations.append(
            DescriptiveTargetAllocation(
                asset_name=asset_name,
                isin=isin,
                asset_currency=holding.currency,
                asset_class=holding.asset_class,
                source_weight=weight,
                allocation_type=allocation_type,
                support_status=support_status,
            )
        )
    if total_is_complete and total_weight != Decimal(1):
        blockers.append(f"TARGET_WEIGHTS_DO_NOT_SUM_TO_ONE:{total_weight}")
    return _DescriptiveTargetSet(
        allocations=tuple(
            sorted(
                allocations,
                key=lambda item: (
                    item.allocation_type,
                    item.asset_name.casefold(),
                    item.isin or "",
                ),
            )
        ),
        security_targets=targets,
        completeness_blockers=tuple(sorted(set(blockers))),
    )


def _source_weight(allocation: float | None) -> Decimal | None:
    if allocation is None:
        return None
    weight = Decimal(str(allocation)) / Decimal(100)
    if not weight.is_finite() or weight < 0 or weight > 1:
        return None
    return weight


def _is_source_cash_allocation(holding: HoldingObservation) -> bool:
    return any(
        value is not None and value.strip().casefold() in {"cash", "készpénz"}
        for value in (holding.asset_class, holding.product)
    )


def _resolve_descriptive_position(
    position: PositionSnapshot,
    confirmation_store: ValidatedIdentityConfirmationStore,
) -> _ResolvedDescriptivePosition:
    raw_isin = position.instrument.isin
    confirmation = confirmation_store.confirmations.get(
        normalize_name(position.provider_name)
    )
    if raw_isin is not None and confirmation is not None and raw_isin != confirmation.isin:
        return _ResolvedDescriptivePosition(
            position,
            None,
            None,
            "IDENTITY_CONFIRMATION_CONFLICTS_WITH_SOURCE_ISIN",
        )
    if raw_isin is not None and position.instrument.identity_status in {
        IdentityStatus.EXACT_ISIN,
        IdentityStatus.MANUAL_CONFIRMED,
    }:
        source = (
            "SOURCE_DATABASE_EXPLICIT_ISIN"
            if position.instrument.identity_status is IdentityStatus.EXACT_ISIN
            else "SQLITE_MANUAL_IDENTITY_LEDGER"
        )
        rule = (
            "EXPLICIT_ISIN"
            if position.instrument.identity_status is IdentityStatus.EXACT_ISIN
            else "MANUAL_CONFIRMED"
        )
        return _ResolvedDescriptivePosition(
            position,
            position,
            DescriptiveIdentityProvenance(rule, source, None, None, None, None, None),
            None,
        )
    if raw_isin is not None:
        return _ResolvedDescriptivePosition(
            position,
            None,
            None,
            "SOURCE_ISIN_WITH_UNCONFIRMED_IDENTITY_STATUS",
        )
    if confirmation is None:
        return _ResolvedDescriptivePosition(
            position,
            None,
            None,
            "NO_APPLICABLE_APPROVED_IDENTITY_CONFIRMATION",
        )
    if (
        position.market_currency is not None
        and position.market_currency != confirmation.approved_currency
    ):
        return _ResolvedDescriptivePosition(
            position,
            None,
            None,
            "IDENTITY_CONFIRMATION_CURRENCY_MISMATCH",
        )
    effective = replace(
        position,
        instrument=Instrument(
            instrument_id=position.instrument.instrument_id,
            canonical_name=position.instrument.canonical_name,
            isin=confirmation.isin,
            identity_status=IdentityStatus.MANUAL_CONFIRMED,
        ),
    )
    return _ResolvedDescriptivePosition(
        position,
        effective,
        _confirmation_provenance(confirmation),
        None,
    )


def _confirmation_provenance(
    confirmation: ValidatedIdentityConfirmation,
) -> DescriptiveIdentityProvenance:
    return DescriptiveIdentityProvenance(
        rule=confirmation.rule,
        source="APPROVED_LTIA_IDENTITY_CONFIRMATION_STORE",
        confirmed_by=confirmation.confirmed_by,
        confirmed_at=confirmation.confirmed_at,
        source_support=confirmation.source_support,
        confirmation_store_fingerprint=confirmation.store_fingerprint,
        identity_registry_audit_fingerprint=(
            confirmation.registry_audit_fingerprint
        ),
    )


def _build_descriptive_comparison(
    *,
    state: CurrentAccountState,
    report_timestamp: datetime,
    observation_date: date,
    target_portfolio_name: str,
    strategy: DescriptiveStrategy,
    internal_objective: PortfolioObjective,
    investment_horizon_days: int,
    target_set: _DescriptiveTargetSet,
    policy: _DescriptivePolicyFields,
    confirmation_store: ValidatedIdentityConfirmationStore,
) -> DescriptivePortfolioComparison:
    position_date = (
        state.position_snapshot.source_date if state.position_snapshot else None
    )
    cash_date = state.cash_snapshot.source_date if state.cash_snapshot else None
    date_blockers: list[str] = []
    hard_date_blockers: list[str] = []
    if state.position_snapshot is None:
        hard_date_blockers.append("POSITION_SOURCE_UNAVAILABLE")
    elif position_date is None:
        hard_date_blockers.append("POSITION_SOURCE_DATE_UNAVAILABLE")
    if state.cash_snapshot is None:
        date_blockers.append("CASH_SOURCE_UNAVAILABLE")
    elif cash_date is None:
        date_blockers.append("CASH_SOURCE_DATE_UNAVAILABLE")
    for label, observed in (
        ("MODEL_SNAPSHOT", observation_date),
        ("POSITION_SOURCE", position_date),
        ("CASH_SOURCE", cash_date),
    ):
        if observed is not None and observed > report_timestamp.date():
            blocker = f"{label}_DATE_AFTER_REPORT_TIMESTAMP"
            date_blockers.append(blocker)
            if label != "CASH_SOURCE":
                hard_date_blockers.append(blocker)
    date_blockers.extend(hard_date_blockers)

    cash = tuple(
        CashBalance(
            account=state.account.label,
            currency=item.currency,
            balance=item.balance,
            source_snapshot_id=item.snapshot_id,
            source_date=cash_date,
            data_quality_status=item.data_quality_status,
        )
        for item in state.cash
    )
    cash_blockers: list[str] = []
    if state.cash_snapshot is None:
        cash_blockers.append("RECORDED_CASH_UNKNOWN_NO_SOURCE")
    else:
        cash_blockers.append("RECORDED_CASH_NOT_ESTABLISHED_SPENDABLE")
    if any(item.allocation_type == "CASH" for item in target_set.allocations):
        cash_blockers.append("SOURCE_TARGET_CASH_GAP_NOT_COMPUTED")

    valid_positions: list[tuple[CurrentAccountState, PositionSnapshot]] = []
    resolutions: dict[int, _ResolvedDescriptivePosition] = {}
    rows: list[DescriptiveComparisonRow] = []
    identity_blockers: list[str] = []
    identity_resolution_blockers: list[str] = []
    for position in state.positions:
        resolution = _resolve_descriptive_position(position, confirmation_store)
        resolutions[position.instrument.instrument_id] = resolution
        if resolution.effective_position is not None:
            valid_positions.append((state, resolution.effective_position))
            continue
        label = f"{state.account.label}:{position.provider_name}"
        identity_blockers.append(label)
        assert resolution.blocker is not None
        identity_resolution_blockers.append(resolution.blocker)
        value, currency = _preferred_value(position)
        rows.append(
            _descriptive_row(
                state=state,
                position=position,
                isin=None,
                currency=currency,
                current_value=value,
                source_target_weight=None,
                identity_status="IDENTITY_UNRESOLVED",
                identity_provenance=None,
                comparison_status=resolution.blocker,
            )
        )

    reconciliation_blockers = (
        (f"POST_TRADE_PDF_RECONCILIATION_REQUIRED:{state.account.label}",)
        if _has_unreconciled_manual_transactions(state)
        else ()
    )
    valuation_currency, valued_positions, valuation_blockers = (
        _descriptive_common_valuation(valid_positions)
    )
    total_value: Decimal | None = None
    if valuation_currency is not None:
        candidate_total = sum((item.value for item in valued_positions), Decimal())
        if candidate_total <= 0:
            valuation_blockers = ("NO_POSITIVE_COMPARABLE_SECURITY_VALUE",)
            valuation_currency = None
        else:
            total_value = candidate_total
    hard_blocked = bool(
        identity_blockers
        or reconciliation_blockers
        or target_set.completeness_blockers
        or hard_date_blockers
        or valuation_blockers
        or total_value is None
    )
    if hard_blocked:
        rows.extend(
            _blocked_descriptive_rows(
                state=state,
                positions=valid_positions,
                targets=target_set.security_targets,
                existing_target_isins={
                    row.isin for row in rows if row.isin is not None
                },
                resolutions=resolutions,
            )
        )
    else:
        assert valuation_currency is not None
        assert total_value is not None
        grouped = _group_positions(
            valued_positions,
            all_tbsz=False,
            scope=state.account.label,
        )
        for isin, entries in grouped.items():
            target = target_set.security_targets.get(isin)
            resolution = resolutions[
                entries[0].position.instrument.instrument_id
            ]
            current_value = sum((item.value for item in entries), Decimal())
            current_weight = current_value / total_value
            target_weight = target.weight if target else Decimal()
            target_value = total_value * target_weight
            rows.append(
                DescriptiveComparisonRow(
                    account=state.account.label,
                    account_provenance=(state.account.label,),
                    instrument_id=(
                        entries[0].position.instrument.instrument_id
                        if len(entries) == 1
                        else None
                    ),
                    instrument_ids=tuple(
                        sorted(
                            {item.position.instrument.instrument_id for item in entries}
                        )
                    ),
                    asset_name=entries[0].position.provider_name,
                    isin=isin,
                    raw_isin=resolution.raw_position.instrument.isin,
                    currency=valuation_currency,
                    asset_currency=(
                        entries[0].position.market_currency
                        or entries[0].position.reporting_currency
                    ),
                    current_value=current_value,
                    current_weight=current_weight,
                    source_target_weight=target_weight,
                    weight_gap=target_weight - current_weight,
                    target_value=target_value,
                    signed_monetary_gap=target_value - current_value,
                    roi=entries[0].position.observed_roi if len(entries) == 1 else None,
                    identity_status=entries[
                        0
                    ].position.instrument.identity_status.value,
                    raw_identity_status=(
                        resolution.raw_position.instrument.identity_status.value
                    ),
                    identity_provenance=resolution.provenance,
                    comparison_status="DESCRIPTIVE_COMPARABLE",
                    data_quality_status=_combined_quality(entries),
                )
            )
        for isin, target in target_set.security_targets.items():
            if isin in grouped:
                continue
            target_value = total_value * target.weight
            rows.append(
                DescriptiveComparisonRow(
                    account=state.account.label,
                    account_provenance=(state.account.label,),
                    instrument_id=None,
                    instrument_ids=(),
                    asset_name=target.asset_name,
                    isin=isin,
                    raw_isin=None,
                    currency=valuation_currency,
                    asset_currency=target.asset_currency,
                    current_value=Decimal(),
                    current_weight=Decimal(),
                    source_target_weight=target.weight,
                    weight_gap=target.weight,
                    target_value=target_value,
                    signed_monetary_gap=target_value,
                    roi=None,
                    identity_status="TARGET_EXACT_ISIN",
                    raw_identity_status="NO_CURRENT_POSITION",
                    identity_provenance=DescriptiveIdentityProvenance(
                        "TARGET_EXACT_ISIN",
                        "MODEL_PORTFOLIO_REFERENCE",
                        None,
                        None,
                        None,
                        None,
                        None,
                    ),
                    comparison_status="DESCRIPTIVE_TARGET_ONLY_EXACT_IDENTITY",
                    data_quality_status="TARGET_MODEL_PORTFOLIO",
                )
            )

    return DescriptivePortfolioComparison(
        mode="DESCRIPTIVE_SINGLE_ACCOUNT",
        account_label=state.account.label,
        report_timestamp=report_timestamp,
        strategy=strategy,
        internal_objective=internal_objective,
        investment_horizon_days=investment_horizon_days,
        investment_horizon_status="RECORDED_ONLY_POLICY_SUPPORT_NOT_ESTABLISHED",
        metric_lookback_status="NOT_USED_DESCRIPTIVE_MODE",
        nav_cutoff_status="NOT_USED_DESCRIPTIVE_MODE",
        reported_annual_indicators_status="NOT_USED_NOT_RESCALED",
        model_observation_date=observation_date,
        model_observation_age_days=_age_days(observation_date, report_timestamp),
        target_portfolio_name=target_portfolio_name,
        reference_selection_status="MANUALLY_SELECTED_NOT_A_VALIDATED_STRATEGY_RECOMMENDATION",
        strategy_policy_availability=policy.availability,
        strategy_policy_id=policy.policy_id,
        strategy_policy_version=policy.version,
        strategy_policy_fingerprint=policy.fingerprint,
        descriptive_comparison_availability="BLOCKED" if hard_blocked else "AVAILABLE",
        strategy_ranking_availability=policy.ranking_availability,
        shortlist_selection_availability="UNAVAILABLE_NOT_IMPLEMENTED",
        recommendation_availability="UNAVAILABLE_MANUAL_REFERENCE_ONLY",
        trade_proposal_availability="UNAVAILABLE_DESCRIPTIVE_MODE",
        denominator_basis="COMPARABLE_SECURITY_VALUES_EXCLUDING_RECORDED_CASH",
        target_weight_treatment="SOURCE_REPORTED_NOT_RENORMALIZED",
        comparison_currency=valuation_currency,
        total_comparable_security_value=total_value,
        position_source_snapshot_id=(
            state.position_snapshot.snapshot_id if state.position_snapshot else None
        ),
        position_source_date=position_date,
        position_source_age_days=_optional_age_days(position_date, report_timestamp),
        cash_source_snapshot_id=state.cash_snapshot.snapshot_id
        if state.cash_snapshot
        else None,
        cash_source_date=cash_date,
        cash_source_age_days=_optional_age_days(cash_date, report_timestamp),
        cash_treatment="RECORDED_SEPARATELY_NOT_ESTABLISHED_SPENDABLE",
        identity_confirmation_store_fingerprint=(
            confirmation_store.store_fingerprint
        ),
        identity_registry_audit_fingerprint=(
            confirmation_store.registry_audit_fingerprint
        ),
        identity_confirmation_validation_blockers=(
            confirmation_store.validation_blockers
        ),
        identity_resolution_blockers=tuple(sorted(set(identity_resolution_blockers))),
        rows=tuple(
            sorted(rows, key=lambda item: (item.asset_name.casefold(), item.isin or ""))
        ),
        target_allocations=target_set.allocations,
        cash_by_currency=tuple(sorted(cash, key=lambda item: item.currency)),
        identity_blockers=tuple(sorted(set(identity_blockers))),
        valuation_blockers=tuple(sorted(set(valuation_blockers))),
        date_blockers=tuple(sorted(set(date_blockers))),
        reconciliation_blockers=reconciliation_blockers,
        target_completeness_blockers=target_set.completeness_blockers,
        cash_blockers=tuple(sorted(set(cash_blockers))),
        strategy_blockers=policy.blockers,
        unresolved_constraints=_UNRESOLVED_DESCRIPTIVE_CONSTRAINTS,
    )


def _descriptive_common_valuation(
    positions: list[tuple[CurrentAccountState, PositionSnapshot]],
) -> tuple[str | None, tuple[_ValuedPosition, ...], tuple[str, ...]]:
    missing = tuple(
        f"VALUE_OR_CURRENCY_UNAVAILABLE:{state.account.label}:{position.provider_name}"
        for state, position in positions
        if not _valuation_options(position)
    )
    if missing:
        return None, (), missing
    currency, valued, mixed = _common_valuation(positions)
    if currency is None:
        return (
            None,
            (),
            tuple(f"MIXED_CURRENCIES_NO_FX:{item}" for item in mixed)
            or ("NO_COMPARABLE_SECURITY_POSITIONS",),
        )
    return currency, valued, ()


def _blocked_descriptive_rows(
    *,
    state: CurrentAccountState,
    positions: list[tuple[CurrentAccountState, PositionSnapshot]],
    targets: dict[str, _TargetAllocation],
    existing_target_isins: set[str],
    resolutions: dict[int, _ResolvedDescriptivePosition],
) -> list[DescriptiveComparisonRow]:
    rows: list[DescriptiveComparisonRow] = []
    for _, position in positions:
        assert position.instrument.isin is not None
        resolution = resolutions[position.instrument.instrument_id]
        value, currency = _preferred_value(position)
        rows.append(
            _descriptive_row(
                state=state,
                position=position,
                isin=position.instrument.isin,
                currency=currency,
                current_value=value,
                source_target_weight=None,
                identity_status=position.instrument.identity_status.value,
                identity_provenance=resolution.provenance,
                comparison_status="DESCRIPTIVE_GAP_BLOCKED",
            )
        )
    current_isins = {
        position.instrument.isin
        for _, position in positions
        if position.instrument.isin is not None
    }
    for isin, target in targets.items():
        if isin in current_isins or isin in existing_target_isins:
            continue
        rows.append(
            DescriptiveComparisonRow(
                account=state.account.label,
                account_provenance=(state.account.label,),
                instrument_id=None,
                instrument_ids=(),
                asset_name=target.asset_name,
                isin=isin,
                raw_isin=None,
                currency=None,
                asset_currency=target.asset_currency,
                current_value=None,
                current_weight=None,
                source_target_weight=None,
                weight_gap=None,
                target_value=None,
                signed_monetary_gap=None,
                roi=None,
                identity_status="TARGET_EXACT_ISIN",
                raw_identity_status="NO_CURRENT_POSITION",
                identity_provenance=DescriptiveIdentityProvenance(
                    "TARGET_EXACT_ISIN",
                    "MODEL_PORTFOLIO_REFERENCE",
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
                comparison_status="DESCRIPTIVE_GAP_BLOCKED",
                data_quality_status="TARGET_MODEL_PORTFOLIO",
            )
        )
    return rows


def _descriptive_row(
    *,
    state: CurrentAccountState,
    position: PositionSnapshot,
    isin: str | None,
    currency: str | None,
    current_value: Decimal | None,
    source_target_weight: Decimal | None,
    identity_status: str,
    identity_provenance: DescriptiveIdentityProvenance | None,
    comparison_status: str,
) -> DescriptiveComparisonRow:
    return DescriptiveComparisonRow(
        account=state.account.label,
        account_provenance=(state.account.label,),
        instrument_id=position.instrument.instrument_id,
        instrument_ids=(position.instrument.instrument_id,),
        asset_name=position.provider_name,
        isin=isin,
        raw_isin=position.instrument.isin,
        currency=currency,
        asset_currency=position.market_currency or position.reporting_currency,
        current_value=current_value,
        current_weight=None,
        source_target_weight=source_target_weight,
        weight_gap=None,
        target_value=None,
        signed_monetary_gap=None,
        roi=position.observed_roi,
        identity_status=identity_status,
        raw_identity_status=position.instrument.identity_status.value,
        identity_provenance=identity_provenance,
        comparison_status=comparison_status,
        data_quality_status=position.data_quality_status,
    )


def _age_days(observed: date, report_timestamp: datetime) -> int:
    return (report_timestamp.date() - observed).days


def _optional_age_days(observed: date | None, report_timestamp: datetime) -> int | None:
    return _age_days(observed, report_timestamp) if observed is not None else None


def _build_comparison(
    *,
    states: tuple[CurrentAccountState, ...],
    all_tbsz: bool,
    observation_date: date,
    selected_target: str,
    targets: dict[str, _TargetAllocation],
    unmapped_targets: tuple[str, ...],
    policy_version: str,
    policy_fingerprint: str,
    tolerance: Decimal,
) -> PortfolioComparison:
    scope = "ALL_TBSZ" if all_tbsz else states[0].account.label
    cash = tuple(
        CashBalance(
            account=state.account.label,
            currency=item.currency,
            balance=item.balance,
            source_snapshot_id=item.snapshot_id,
            source_date=state.cash_snapshot.source_date if state.cash_snapshot else None,
            data_quality_status=item.data_quality_status,
        )
        for state in states
        for item in state.cash
    )
    transaction_blockers = tuple(
        sorted(state.account.label for state in states if _has_unreconciled_manual_transactions(state))
    )
    target_names = _targets_by_normalized_name(targets)
    valid_positions: list[tuple[CurrentAccountState, PositionSnapshot]] = []
    rows: list[ComparisonRow] = []
    unmapped_current: list[str] = []
    identity_blockers: list[str] = []
    blocked_target_isins: set[str] = set()

    for state in states:
        for position in state.positions:
            label = f"{state.account.label}:{position.provider_name}"
            if _is_confirmed_identity(position):
                valid_positions.append((state, position))
                continue
            unmapped_current.append(label)
            identity_blockers.append(label)
            candidate = _exact_name_target(position.provider_name, target_names)
            if candidate is not None:
                blocked_target_isins.add(candidate.isin)
            value, currency = _preferred_value(position)
            rows.append(
                _row(
                    account=state.account.label,
                    account_provenance=(state.account.label,),
                    instrument_id=position.instrument.instrument_id,
                    instrument_ids=(position.instrument.instrument_id,),
                    asset_name=position.provider_name,
                    isin=None,
                    currency=currency,
                    asset_currency=position.market_currency or position.reporting_currency,
                    current_value=value,
                    target_weight=candidate.weight if candidate else None,
                    roi=position.observed_roi,
                    action=ComparisonAction.IDENTITY_MAPPING_REQUIRED,
                    identity_status=position.instrument.identity_status.value,
                    comparison_status="IDENTITY_MAPPING_REQUIRED",
                    data_quality_status=position.data_quality_status,
                )
            )

    if transaction_blockers:
        rows.extend(
            _blocked_position_rows(
                valid_positions,
                scope=scope,
                action=ComparisonAction.INSUFFICIENT_DATA,
                comparison_status="POST_TRADE_PDF_RECONCILIATION_REQUIRED",
                targets=targets,
            )
        )
        return _comparison(
            states, scope, observation_date, selected_target, policy_version, policy_fingerprint,
            tolerance, None, None, rows, cash, unmapped_current, unmapped_targets,
            identity_blockers, (), transaction_blockers,
        )
    if unmapped_targets:
        rows.extend(
            _blocked_position_rows(
                valid_positions,
                scope=scope,
                action=ComparisonAction.INSUFFICIENT_DATA,
                comparison_status="TARGET_INSTRUMENT_DATA_INCOMPLETE",
                targets=targets,
            )
        )
        return _comparison(
            states, scope, observation_date, selected_target, policy_version, policy_fingerprint,
            tolerance, None, None, rows, cash, unmapped_current, unmapped_targets,
            identity_blockers, (), (),
        )
    if identity_blockers:
        rows.extend(
            _blocked_position_rows(
                valid_positions,
                scope=scope,
                action=ComparisonAction.IDENTITY_MAPPING_REQUIRED,
                comparison_status="IDENTITY_BLOCKED_BY_CURRENT_HOLDINGS",
                targets=targets,
            )
        )
        for target in targets.values():
            if target.isin in blocked_target_isins:
                continue
            rows.append(
                _row(
                    account=scope,
                    account_provenance=tuple(state.account.label for state in states),
                    instrument_id=None,
                    instrument_ids=(),
                    asset_name=target.asset_name,
                    isin=target.isin,
                    currency=None,
                    asset_currency=target.asset_currency,
                    current_value=None,
                    target_weight=target.weight,
                    roi=None,
                    action=ComparisonAction.IDENTITY_MAPPING_REQUIRED,
                    identity_status="TARGET_EXACT_ISIN",
                    comparison_status="IDENTITY_BLOCKED_BY_CURRENT_HOLDINGS",
                    data_quality_status="TARGET_MODEL_PORTFOLIO",
                )
            )
        return _comparison(
            states, scope, observation_date, selected_target, policy_version, policy_fingerprint,
            tolerance, None, None, rows, cash, unmapped_current, unmapped_targets,
            identity_blockers, (), (),
        )

    if not valid_positions:
        for target in targets.values():
            rows.append(
                _row(
                    account=scope,
                    account_provenance=tuple(state.account.label for state in states),
                    instrument_id=None,
                    instrument_ids=(),
                    asset_name=target.asset_name,
                    isin=target.isin,
                    currency=None,
                    asset_currency=target.asset_currency,
                    current_value=None,
                    target_weight=target.weight,
                    roi=None,
                    action=ComparisonAction.INSUFFICIENT_DATA,
                    identity_status="TARGET_EXACT_ISIN",
                    comparison_status="NO_COMPARABLE_POSITION_VALUE_CASH_NOT_AUTOMATICALLY_INVESTED",
                    data_quality_status="TARGET_MODEL_PORTFOLIO",
                )
            )
        return _comparison(
            states, scope, observation_date, selected_target, policy_version, policy_fingerprint,
            tolerance, None, None, rows, cash, (), (), (), (), (),
        )

    valuation_currency, valued_positions, fx_blockers = _common_valuation(valid_positions)
    if valuation_currency is None:
        rows.extend(
            _blocked_position_rows(
                valid_positions,
                scope=scope,
                action=ComparisonAction.FX_REQUIRED_FOR_EXACT_TRADE_AMOUNT,
                comparison_status="FX_REQUIRED_FOR_EXACT_TRADE_AMOUNT",
                targets=targets,
            )
        )
        return _comparison(
            states, scope, observation_date, selected_target, policy_version, policy_fingerprint,
            tolerance, None, None, rows, cash, (), (), (), fx_blockers, (),
        )
    total_value = sum((item.value for item in valued_positions), Decimal())
    if total_value <= 0:
        rows.extend(
            _blocked_position_rows(
                valid_positions,
                scope=scope,
                action=ComparisonAction.INSUFFICIENT_DATA,
                comparison_status="NO_POSITIVE_COMPARABLE_PORTFOLIO_VALUE",
                targets=targets,
            )
        )
        return _comparison(
            states, scope, observation_date, selected_target, policy_version, policy_fingerprint,
            tolerance, valuation_currency, None, rows, cash, (), (), (), (), (),
        )

    grouped = _group_positions(valued_positions, all_tbsz=all_tbsz, scope=scope)
    for isin, entries in grouped.items():
        position_target = targets.get(isin)
        current_value = sum((item.value for item in entries), Decimal())
        current_weight = current_value / total_value
        target_weight = position_target.weight if position_target else Decimal()
        target_value = total_value * target_weight
        trade_value = target_value - current_value
        rows.append(
            _row(
                account=scope if all_tbsz else entries[0].state.account.label,
                account_provenance=tuple(sorted({item.state.account.label for item in entries})),
                instrument_id=entries[0].position.instrument.instrument_id if len(entries) == 1 else None,
                instrument_ids=tuple(sorted({item.position.instrument.instrument_id for item in entries})),
                asset_name=entries[0].position.provider_name,
                isin=isin,
                currency=valuation_currency,
                asset_currency=entries[0].position.market_currency or entries[0].position.reporting_currency,
                current_value=current_value,
                current_weight=current_weight,
                target_weight=target_weight,
                weight_difference=target_weight - current_weight,
                target_value=target_value,
                estimated_trade_value=trade_value,
                roi=entries[0].position.observed_roi if len(entries) == 1 else None,
                action=_action_for_difference(target_weight - current_weight, tolerance),
                identity_status=entries[0].position.instrument.identity_status.value,
                comparison_status="COMPARABLE",
                data_quality_status=_combined_quality(entries),
            )
        )
    for isin, target in targets.items():
        if isin in grouped:
            continue
        target_value = total_value * target.weight
        rows.append(
            _row(
                account=scope,
                account_provenance=tuple(state.account.label for state in states),
                instrument_id=None,
                instrument_ids=(),
                asset_name=target.asset_name,
                isin=target.isin,
                currency=valuation_currency,
                asset_currency=target.asset_currency,
                current_value=Decimal(),
                current_weight=Decimal(),
                target_weight=target.weight,
                weight_difference=target.weight,
                target_value=target_value,
                estimated_trade_value=target_value,
                roi=None,
                action=ComparisonAction.BUY,
                identity_status="TARGET_EXACT_ISIN",
                comparison_status="TARGET_ONLY_EXACT_IDENTITY",
                data_quality_status="TARGET_MODEL_PORTFOLIO",
            )
        )
    return _comparison(
        states, scope, observation_date, selected_target, policy_version, policy_fingerprint,
        tolerance, valuation_currency, total_value, rows, cash, (), (), (), (), (),
    )


def _row(
    *,
    account: str,
    account_provenance: tuple[str, ...],
    instrument_id: int | None,
    instrument_ids: tuple[int, ...],
    asset_name: str,
    isin: str | None,
    currency: str | None,
    asset_currency: str | None,
    current_value: Decimal | None,
    target_weight: Decimal | None,
    roi: Decimal | None,
    action: ComparisonAction,
    identity_status: str,
    comparison_status: str,
    data_quality_status: str,
    current_weight: Decimal | None = None,
    weight_difference: Decimal | None = None,
    target_value: Decimal | None = None,
    estimated_trade_value: Decimal | None = None,
) -> ComparisonRow:
    return ComparisonRow(
        account, account_provenance, instrument_id, instrument_ids, asset_name, isin,
        currency, asset_currency, current_value, current_weight, target_weight,
        weight_difference, target_value, estimated_trade_value, roi, action,
        identity_status, comparison_status, data_quality_status,
    )


def _comparison(
    states: tuple[CurrentAccountState, ...],
    scope: str,
    observation_date: date,
    selected_target: str,
    policy_version: str,
    policy_fingerprint: str,
    tolerance: Decimal,
    comparison_currency: str | None,
    total_comparison_value: Decimal | None,
    rows: list[ComparisonRow],
    cash: tuple[CashBalance, ...],
    unmapped_current: list[str] | tuple[str, ...],
    unmapped_targets: tuple[str, ...],
    identity_blockers: list[str] | tuple[str, ...],
    fx_blockers: list[str] | tuple[str, ...],
    transaction_blockers: tuple[str, ...],
) -> PortfolioComparison:
    return PortfolioComparison(
        account_labels=tuple(state.account.label for state in states),
        account_scope=scope,
        comparison_timestamp=datetime.now(UTC),
        model_observation_date=observation_date,
        target_portfolio_name=selected_target,
        policy_version=policy_version,
        policy_fingerprint=policy_fingerprint,
        tolerance=tolerance,
        comparison_currency=comparison_currency,
        total_comparison_value=total_comparison_value,
        rows=tuple(sorted(rows, key=lambda item: (item.account, item.asset_name, item.isin or ""))),
        cash_by_currency=tuple(sorted(cash, key=lambda item: (item.account, item.currency))),
        unmapped_current_holdings=tuple(sorted(set(unmapped_current))),
        unmapped_target_holdings=tuple(sorted(set(unmapped_targets))),
        identity_blockers=tuple(sorted(set(identity_blockers))),
        fx_blockers=tuple(sorted(set(fx_blockers))),
        manual_transaction_blockers=transaction_blockers,
    )


def _is_confirmed_identity(position: PositionSnapshot) -> bool:
    return position.instrument.isin is not None and position.instrument.identity_status in {
        IdentityStatus.EXACT_ISIN,
        IdentityStatus.MANUAL_CONFIRMED,
    }


def _targets_by_normalized_name(targets: dict[str, _TargetAllocation]) -> dict[str, tuple[_TargetAllocation, ...]]:
    values: dict[str, list[_TargetAllocation]] = defaultdict(list)
    for target in targets.values():
        values[_normalize(target.asset_name)].append(target)
    return {key: tuple(value) for key, value in values.items()}


def _exact_name_target(
    provider_name: str, candidates: dict[str, tuple[_TargetAllocation, ...]]
) -> _TargetAllocation | None:
    matches = candidates.get(_normalize(provider_name), ())
    return matches[0] if len(matches) == 1 else None


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _preferred_value(position: PositionSnapshot) -> tuple[Decimal | None, str | None]:
    options = _valuation_options(position)
    if len(options) == 1:
        currency, value = next(iter(options.items()))
        return value, currency
    if "HUF" in options:
        return options["HUF"], "HUF"
    return None, None


def _valuation_options(position: PositionSnapshot) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    if position.market_value is not None and position.market_currency is not None:
        values[position.market_currency] = position.market_value
    if position.reporting_value is not None and position.reporting_currency is not None:
        values[position.reporting_currency] = position.reporting_value
    return values


def _common_valuation(
    positions: list[tuple[CurrentAccountState, PositionSnapshot]],
) -> tuple[str | None, tuple[_ValuedPosition, ...], tuple[str, ...]]:
    if not positions:
        return None, (), ()
    choices = [_valuation_options(position) for _, position in positions]
    common = set(choices[0])
    for options in choices[1:]:
        common.intersection_update(options)
    if not common:
        blockers = tuple(f"{state.account.label}:{position.provider_name}" for state, position in positions)
        return None, (), blockers
    currency = "HUF" if "HUF" in common else min(common)
    return (
        currency,
        tuple(
            _ValuedPosition(state, position, _valuation_options(position)[currency], currency)
            for state, position in positions
        ),
        (),
    )


def _group_positions(
    positions: tuple[_ValuedPosition, ...], *, all_tbsz: bool, scope: str
) -> dict[str, list[_ValuedPosition]]:
    grouped: dict[str, list[_ValuedPosition]] = defaultdict(list)
    for item in positions:
        assert item.position.instrument.isin is not None
        key = item.position.instrument.isin if all_tbsz else f"{scope}\0{item.position.instrument.isin}"
        grouped[key].append(item)
    return {entries[0].position.instrument.isin or "": entries for entries in grouped.values()}


def _blocked_position_rows(
    positions: list[tuple[CurrentAccountState, PositionSnapshot]],
    *,
    scope: str,
    action: ComparisonAction,
    comparison_status: str,
    targets: dict[str, _TargetAllocation],
) -> list[ComparisonRow]:
    result: list[ComparisonRow] = []
    for state, position in positions:
        assert position.instrument.isin is not None
        target = targets.get(position.instrument.isin)
        value, currency = _preferred_value(position)
        result.append(
            _row(
                account=scope if scope == "ALL_TBSZ" else state.account.label,
                account_provenance=(state.account.label,),
                instrument_id=position.instrument.instrument_id,
                instrument_ids=(position.instrument.instrument_id,),
                asset_name=position.provider_name,
                isin=position.instrument.isin,
                currency=currency,
                asset_currency=position.market_currency or position.reporting_currency,
                current_value=value,
                target_weight=target.weight if target else Decimal(),
                roi=position.observed_roi,
                action=action,
                identity_status=position.instrument.identity_status.value,
                comparison_status=comparison_status,
                data_quality_status=position.data_quality_status,
            )
        )
    return result


def _combined_quality(entries: list[_ValuedPosition]) -> str:
    qualities = {item.position.data_quality_status for item in entries}
    return next(iter(qualities)) if len(qualities) == 1 else "MULTIPLE_SOURCE_QUALITY_STATUSES"


def _has_unreconciled_manual_transactions(state: CurrentAccountState) -> bool:
    if not state.manual_transactions:
        return False
    if state.position_snapshot is None or state.position_snapshot.source_date is None:
        return True
    return any(transaction.transaction_date > state.position_snapshot.source_date for transaction in state.manual_transactions)


def _action_for_difference(difference: Decimal, tolerance: Decimal) -> ComparisonAction:
    if abs(difference) <= tolerance:
        return ComparisonAction.HOLD
    return ComparisonAction.BUY if difference > 0 else ComparisonAction.SELL
