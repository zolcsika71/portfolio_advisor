"""Read-only current-state projections over retained TBSZ evidence."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import (
    CashSnapshot,
    CurrentPortfolioRecord,
    CurrentPortfolioRecordType,
    ManualTransaction,
    PositionSnapshot,
    SourceSnapshot,
    TbszAccount,
)
from .repository import TbszPortfolioRepository


@dataclass(frozen=True, slots=True)
class CurrentAccountState:
    """Latest observed source views for one account; no inferred transaction state."""

    account: TbszAccount
    position_snapshot: SourceSnapshot | None
    cash_snapshot: SourceSnapshot | None
    positions: tuple[PositionSnapshot, ...]
    cash: tuple[CashSnapshot, ...]
    manual_transactions: tuple[ManualTransaction, ...]


def current_account_state(repository: TbszPortfolioRepository, account_label: str) -> CurrentAccountState:
    account = repository.account(account_label)
    position_snapshot = repository.current_position_snapshot(account.label)
    cash_snapshot = repository.current_cash_snapshot(account.label)
    return CurrentAccountState(
        account=account,
        position_snapshot=position_snapshot,
        cash_snapshot=cash_snapshot,
        positions=repository.positions_for_snapshot(position_snapshot.snapshot_id) if position_snapshot else (),
        cash=repository.cash_for_snapshot(cash_snapshot.snapshot_id) if cash_snapshot else (),
        manual_transactions=repository.transactions(account.label),
    )


def current_portfolio_records(
    repository: TbszPortfolioRepository, account_label: str
) -> tuple[CurrentPortfolioRecord, ...]:
    """Unify the latest position and cash evidence without joining or netting it."""
    return current_portfolio_records_from_state(current_account_state(repository, account_label))


def current_portfolio_records_from_state(
    state: CurrentAccountState,
) -> tuple[CurrentPortfolioRecord, ...]:
    """Project assets and cash together while retaining their source snapshot IDs."""
    records = [
        _asset_record(state.account.label, position)
        for position in state.positions
    ]
    records.extend(
        CurrentPortfolioRecord(
            account=state.account.label,
            record_type=CurrentPortfolioRecordType.CASH,
            asset_name="CASH",
            isin=None,
            currency=cash.currency,
            amount=cash.balance,
            roi=None,
            source_snapshot_id=cash.snapshot_id,
            data_quality_status=cash.data_quality_status,
            value_status="SOURCE_SUPPORTED",
        )
        for cash in state.cash
    )
    return tuple(
        sorted(
            records,
            key=lambda item: (
                item.record_type.value,
                item.asset_name.casefold(),
                item.currency or "",
                item.isin or "",
                item.source_snapshot_id,
            ),
        )
    )


def _asset_record(account: str, position: PositionSnapshot) -> CurrentPortfolioRecord:
    """Prefer a supported native market-value pair, then reporting-value pair."""
    currency: str | None
    amount: Decimal | None
    if position.market_value is not None and position.market_currency is not None:
        currency = position.market_currency
        amount = position.market_value
        value_status = "SOURCE_SUPPORTED_MARKET_VALUE"
    elif position.reporting_value is not None and position.reporting_currency is not None:
        currency = position.reporting_currency
        amount = position.reporting_value
        value_status = "SOURCE_SUPPORTED_REPORTING_VALUE"
    else:
        currency = position.market_currency or position.reporting_currency
        amount = None
        value_status = "SOURCE_VALUE_OR_CURRENCY_UNAVAILABLE"
    return CurrentPortfolioRecord(
        account=account,
        record_type=CurrentPortfolioRecordType.ASSET,
        asset_name=position.provider_name,
        isin=position.instrument.isin,
        currency=currency,
        amount=amount,
        roi=position.observed_roi,
        source_snapshot_id=position.snapshot_id,
        data_quality_status=position.data_quality_status,
        value_status=value_status,
    )
