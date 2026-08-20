"""Read-only comparison between observed TBSZ holdings and model allocations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.database.repository import (
    ModelPortfolioRepository,
    RepositoryError,
)

from .models import ComparisonAction, IdentityStatus, PositionSnapshot
from .repository import TbszPortfolioRepository
from .service import CurrentAccountState, current_account_state


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    instrument: str
    account: str | None
    current_value: Decimal | None
    current_weight: Decimal | None
    target_weight: Decimal | None
    weight_difference: Decimal | None
    target_value: Decimal | None
    estimated_trade_value: Decimal | None
    action: ComparisonAction
    identity_status: str
    data_quality_status: str


@dataclass(frozen=True, slots=True)
class PortfolioComparison:
    account_labels: tuple[str, ...]
    model_observation_date: date
    target_portfolio_name: str
    tolerance: Decimal
    rows: tuple[ComparisonRow, ...]
    cash_by_currency: tuple[tuple[str, str, Decimal], ...]
    unmapped_holdings: tuple[str, ...]
    unmapped_target_instruments: tuple[str, ...]
    missing_isins: tuple[str, ...]
    missing_quantities: tuple[str, ...]
    missing_purchase_prices: tuple[str, ...]
    identity_blockers: tuple[str, ...]
    fx_blockers: tuple[str, ...]
    manual_transaction_blockers: tuple[str, ...]


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
    """Build an advisory-only report without a provider, FX, or database write.

    `tolerance` is a fraction, so `Decimal("0.01")` means one percentage
    point.  No implicit rebalance threshold exists in this subsystem.
    """
    if (account_label is None) == (not all_tbsz):
        raise ValueError("provide exactly one account_label or all_tbsz=True")
    if not tolerance.is_finite() or tolerance < 0 or tolerance > 1:
        raise ValueError("tolerance must be a finite fraction from 0 to 1")
    labels = (account_label,) if account_label else tuple(account.label for account in tbsz_repository.accounts())
    if not labels:
        raise ValueError("no TBSZ accounts are available")
    states = tuple(current_account_state(tbsz_repository, label) for label in labels)
    observation_date, selected_target, target_weights, unmapped_targets = _target_allocations(
        model_repository, rules_path, target_portfolio_name
    )
    return _build_comparison(states, observation_date, selected_target, target_weights, unmapped_targets, tolerance)


def _target_allocations(
    repository: ModelPortfolioRepository,
    rules_path: Path,
    requested_name: str | None,
) -> tuple[date, str, dict[str, Decimal], tuple[str, ...]]:
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
    holdings = [item for item in repository.load_holdings(observation_date) if item.portfolio_name == selected_name]
    if not holdings:
        raise RepositoryError("selected recommended portfolio has no holdings at the current model snapshot")
    target_weights: dict[str, Decimal] = defaultdict(Decimal)
    unmapped: list[str] = []
    for holding in holdings:
        if holding.isin is None or not holding.isin.strip() or holding.allocation is None:
            unmapped.append(holding.product or "UNKNOWN_TARGET_INSTRUMENT")
            continue
        target_weights[holding.isin.upper()] += Decimal(str(holding.allocation)) / Decimal(100)
    return observation_date, selected_name, dict(target_weights), tuple(sorted(set(unmapped)))


def _build_comparison(
    states: tuple[CurrentAccountState, ...],
    observation_date: date,
    selected_target: str,
    target_weights: dict[str, Decimal],
    unmapped_targets: tuple[str, ...],
    tolerance: Decimal,
) -> PortfolioComparison:
    cash = tuple(
        sorted(
            (state.account.label, item.currency, item.balance)
            for state in states
            for item in state.cash
        )
    )
    transaction_blockers = tuple(
        sorted(
            state.account.label
            for state in states
            if _has_unreconciled_manual_transactions(state)
        )
    )
    known: dict[str, list[tuple[CurrentAccountState, PositionSnapshot, Decimal | None]]] = defaultdict(list)
    rows: list[ComparisonRow] = []
    unmapped_holdings: list[str] = []
    missing_isins: list[str] = []
    missing_quantities: list[str] = []
    missing_purchase_prices: list[str] = []
    identity_blockers: list[str] = []
    fx_blockers: list[str] = []
    for state in states:
        for position in state.positions:
            label = f"{state.account.label}:{position.provider_name}"
            if position.quantity is None:
                missing_quantities.append(label)
            # Initial observed positions intentionally do not invent an acquisition price.
            if position.unit_price is None:
                missing_purchase_prices.append(label)
            if position.instrument.isin is None or position.instrument.identity_status not in {
                IdentityStatus.EXACT_ISIN,
                IdentityStatus.MANUAL_CONFIRMED,
            }:
                unmapped_holdings.append(label)
                missing_isins.append(label)
                identity_blockers.append(label)
                rows.append(
                    ComparisonRow(
                        position.provider_name,
                        state.account.label,
                        _huf_value(position),
                        None,
                        None,
                        None,
                        None,
                        None,
                        ComparisonAction.IDENTITY_MAPPING_REQUIRED,
                        position.instrument.identity_status.value,
                        position.data_quality_status,
                    )
                )
                continue
            value = _huf_value(position)
            if value is None:
                fx_blockers.append(label)
            known[position.instrument.isin].append((state, position, value))

    if transaction_blockers:
        _add_manual_transaction_blocked_rows(rows, known, target_weights)
        return PortfolioComparison(
            tuple(state.account.label for state in states),
            observation_date,
            selected_target,
            tolerance,
            tuple(sorted(rows, key=lambda item: ((item.account or ""), item.instrument, item.action.value))),
            cash,
            tuple(sorted(unmapped_holdings)),
            unmapped_targets,
            tuple(sorted(missing_isins)),
            tuple(sorted(missing_quantities)),
            tuple(sorted(missing_purchase_prices)),
            tuple(sorted(identity_blockers)),
            tuple(sorted(fx_blockers)),
            transaction_blockers,
        )
    if identity_blockers:
        _add_identity_blocked_rows(rows, known, target_weights)
        return PortfolioComparison(
            tuple(state.account.label for state in states),
            observation_date,
            selected_target,
            tolerance,
            tuple(sorted(rows, key=lambda item: ((item.account or ""), item.instrument, item.action.value))),
            cash,
            tuple(sorted(unmapped_holdings)),
            unmapped_targets,
            tuple(sorted(missing_isins)),
            tuple(sorted(missing_quantities)),
            tuple(sorted(missing_purchase_prices)),
            tuple(sorted(identity_blockers)),
            tuple(sorted(fx_blockers)),
            transaction_blockers,
        )
    if unmapped_targets:
        _add_insufficient_target_rows(rows, known)
        return PortfolioComparison(
            tuple(state.account.label for state in states),
            observation_date,
            selected_target,
            tolerance,
            tuple(sorted(rows, key=lambda item: ((item.account or ""), item.instrument, item.action.value))),
            cash,
            tuple(sorted(unmapped_holdings)),
            unmapped_targets,
            tuple(sorted(missing_isins)),
            tuple(sorted(missing_quantities)),
            tuple(sorted(missing_purchase_prices)),
            tuple(sorted(identity_blockers)),
            tuple(sorted(fx_blockers)),
            transaction_blockers,
        )
    all_values = [value for entries in known.values() for _, _, value in entries]
    total_value = sum((value for value in all_values if value is not None), Decimal()) if all(value is not None for value in all_values) else None
    if total_value is None:
        for isin, entries in known.items():
            for state, position, value in entries:
                rows.append(
                    ComparisonRow(
                        position.provider_name,
                        state.account.label,
                        value,
                        None,
                        target_weights.get(isin),
                        None,
                        None,
                        None,
                        ComparisonAction.FX_REQUIRED_FOR_EXACT_TRADE_AMOUNT,
                        position.instrument.identity_status.value,
                        position.data_quality_status,
                    )
                )
    else:
        _add_known_rows(rows, known, target_weights, total_value, tolerance)
    known_isins = set(known)
    if total_value is not None:
        for isin in sorted(set(target_weights) - known_isins):
            target_value = target_weights[isin] * total_value
            rows.append(
                ComparisonRow(
                    isin,
                    None,
                    Decimal(),
                    Decimal(),
                    target_weights[isin],
                    target_weights[isin],
                    target_value,
                    target_value,
                    ComparisonAction.BUY,
                    "TARGET_EXACT_ISIN",
                    "TARGET_ONLY",
                )
            )
    return PortfolioComparison(
        tuple(state.account.label for state in states),
        observation_date,
        selected_target,
        tolerance,
        tuple(sorted(rows, key=lambda item: ((item.account or ""), item.instrument, item.action.value))),
        cash,
        tuple(sorted(unmapped_holdings)),
        unmapped_targets,
        tuple(sorted(missing_isins)),
        tuple(sorted(missing_quantities)),
        tuple(sorted(missing_purchase_prices)),
        tuple(sorted(identity_blockers)),
        tuple(sorted(fx_blockers)),
        transaction_blockers,
    )


def _has_unreconciled_manual_transactions(state: CurrentAccountState) -> bool:
    """Do not present a post-trade source view until a later dated PDF reconciles it."""
    if not state.manual_transactions:
        return False
    if state.position_snapshot is None or state.position_snapshot.source_date is None:
        return True
    return any(transaction.transaction_date > state.position_snapshot.source_date for transaction in state.manual_transactions)


def _add_manual_transaction_blocked_rows(
    rows: list[ComparisonRow],
    known: dict[str, list[tuple[CurrentAccountState, PositionSnapshot, Decimal | None]]],
    target_weights: dict[str, Decimal],
) -> None:
    """Manual executions are evidence, not an inferred replacement source snapshot."""
    for isin, entries in known.items():
        for state, position, value in entries:
            rows.append(
                ComparisonRow(
                    position.provider_name,
                    state.account.label,
                    value,
                    None,
                    target_weights.get(isin),
                    None,
                    None,
                    None,
                    ComparisonAction.INSUFFICIENT_DATA,
                    position.instrument.identity_status.value,
                    "POST_TRADE_PDF_RECONCILIATION_REQUIRED",
                )
            )


def _add_identity_blocked_rows(
    rows: list[ComparisonRow],
    known: dict[str, list[tuple[CurrentAccountState, PositionSnapshot, Decimal | None]]],
    target_weights: dict[str, Decimal],
) -> None:
    """Avoid a false BUY/SELL plan while any current holding lacks identity evidence."""
    for isin, entries in known.items():
        for state, position, value in entries:
            rows.append(
                ComparisonRow(
                    position.provider_name,
                    state.account.label,
                    value,
                    None,
                    target_weights.get(isin),
                    None,
                    None,
                    None,
                    ComparisonAction.IDENTITY_MAPPING_REQUIRED,
                    position.instrument.identity_status.value,
                    "IDENTITY_BLOCKED_BY_CURRENT_HOLDINGS",
                )
            )
    for isin in sorted(set(target_weights) - set(known)):
        rows.append(
            ComparisonRow(
                isin,
                None,
                None,
                None,
                target_weights[isin],
                None,
                None,
                None,
                ComparisonAction.IDENTITY_MAPPING_REQUIRED,
                "TARGET_EXACT_ISIN",
                "IDENTITY_BLOCKED_BY_CURRENT_HOLDINGS",
            )
        )


def _add_insufficient_target_rows(
    rows: list[ComparisonRow],
    known: dict[str, list[tuple[CurrentAccountState, PositionSnapshot, Decimal | None]]],
) -> None:
    """A target without an ISIN/allocation cannot support a complete trade plan."""
    for entries in known.values():
        for state, position, value in entries:
            rows.append(
                ComparisonRow(
                    position.provider_name,
                    state.account.label,
                    value,
                    None,
                    None,
                    None,
                    None,
                    None,
                    ComparisonAction.INSUFFICIENT_DATA,
                    position.instrument.identity_status.value,
                    "TARGET_INSTRUMENT_DATA_INCOMPLETE",
                )
            )


def _add_known_rows(
    rows: list[ComparisonRow],
    known: dict[str, list[tuple[CurrentAccountState, PositionSnapshot, Decimal | None]]],
    target_weights: dict[str, Decimal],
    total_value: Decimal,
    tolerance: Decimal,
) -> None:
    for isin, entries in known.items():
        target_weight = target_weights.get(isin, Decimal())
        total_for_isin = sum((value for _, _, value in entries if value is not None), Decimal())
        # Explicit account provenance is retained: an all-TBSZ target is apportioned
        # across existing positions by their currently observed share.
        for state, position, value in entries:
            assert value is not None
            current_weight = value / total_value if total_value else Decimal()
            target_value = target_weight * total_value * (value / total_for_isin) if total_for_isin else Decimal()
            target_weight_for_account = target_value / total_value if total_value else Decimal()
            difference = target_weight_for_account - current_weight
            trade = target_value - value
            action = _action_for_difference(difference, trade, tolerance)
            rows.append(
                ComparisonRow(
                    position.provider_name,
                    state.account.label,
                    value,
                    current_weight,
                    target_weight_for_account,
                    difference,
                    target_value,
                    abs(trade),
                    action,
                    position.instrument.identity_status.value,
                    position.data_quality_status,
                )
            )


def _huf_value(position: PositionSnapshot) -> Decimal | None:
    if position.market_value is not None and position.market_currency == "HUF":
        return position.market_value
    if position.reporting_value is not None and position.reporting_currency == "HUF":
        return position.reporting_value
    return None


def _action_for_difference(difference: Decimal, trade: Decimal, tolerance: Decimal) -> ComparisonAction:
    if abs(difference) <= tolerance:
        return ComparisonAction.HOLD
    return ComparisonAction.BUY if trade > 0 else ComparisonAction.SELL
