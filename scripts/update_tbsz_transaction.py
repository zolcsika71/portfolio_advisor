"""Append a user-executed TBSZ BUY or SELL record; this is never an order API."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from portfolio_advisor.tbsz.models import TbszError, TransactionAction
from portfolio_advisor.tbsz.repository import TbszPortfolioRepository


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a decimal") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--action", required=True, choices=tuple(action.value for action in TransactionAction))
    parser.add_argument("--instrument-id", required=True, type=int)
    parser.add_argument("--quantity", required=True, type=_decimal)
    parser.add_argument("--price", required=True, type=_decimal)
    parser.add_argument("--currency", required=True)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--client-reference")
    parser.add_argument("--database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    args = parser.parse_args(argv)
    try:
        transaction = TbszPortfolioRepository(args.database).record_manual_transaction(
            account_label=args.account,
            action=TransactionAction(args.action),
            instrument_id=args.instrument_id,
            quantity=args.quantity,
            price=args.price,
            currency=args.currency,
            transaction_date=args.date,
            client_reference=args.client_reference,
        )
    except TbszError as error:
        print(f"MANUAL_TRANSACTION_REJECTED detail={error}", file=sys.stderr)
        return 2
    print(f"MANUAL_TRANSACTION_RECORDED transaction_id={transaction.transaction_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
