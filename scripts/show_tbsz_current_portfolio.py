"""Show observed TBSZ source state plus the non-netted manual transaction ledger."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from portfolio_advisor.tbsz.repository import TbszPortfolioRepository
from portfolio_advisor.tbsz.service import (
    current_account_state,
    current_portfolio_records_from_state,
)


def _json_default(value: object) -> object:
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot serialize {type(value).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    args = parser.parse_args(argv)
    try:
        result = current_account_state(TbszPortfolioRepository(args.database), args.account)
    except RuntimeError as error:
        print(f"TBSZ_CURRENT_PORTFOLIO_READ_FAILED detail={error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "account": result.account.label,
                "position_snapshot_id": result.position_snapshot.snapshot_id if result.position_snapshot else None,
                "cash_snapshot_id": result.cash_snapshot.snapshot_id if result.cash_snapshot else None,
                "records": [asdict(item) for item in current_portfolio_records_from_state(result)],
                "manual_transactions": [asdict(item) for item in result.manual_transactions],
            },
            default=_json_default,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
