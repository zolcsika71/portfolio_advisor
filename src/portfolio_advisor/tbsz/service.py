"""Read-only current-state projections over retained TBSZ evidence."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    CashSnapshot,
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
