"""Read-only target-allocation comparison for observed TBSZ evidence.

This module intentionally derives all output in memory. It neither acquires
market/FX data nor alters the source snapshot or manual transaction ledgers.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.database.repository import (
    ModelPortfolioRepository,
    RepositoryError,
)
from portfolio_advisor.ranking.config import load_ranking_rules

from .models import ComparisonAction, IdentityStatus, PositionSnapshot
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
