"""Read-only reconciliation of two retained, dated George position snapshots."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.tbsz.reconciliation import reconcile_position_snapshots
from portfolio_advisor.tbsz.repository import TbszPortfolioRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--previous-snapshot-id", required=True, type=int)
    parser.add_argument("--later-snapshot-id", required=True, type=int)
    parser.add_argument("--database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    args = parser.parse_args(argv)
    try:
        result = reconcile_position_snapshots(
            TbszPortfolioRepository(args.database),
            account_label=args.account,
            previous_snapshot_id=args.previous_snapshot_id,
            later_snapshot_id=args.later_snapshot_id,
        )
    except (ValueError, RuntimeError) as error:
        print(f"TBSZ_RECONCILIATION_FAILED detail={error}", file=sys.stderr)
        return 2
    print(f"{result.status} changed_instruments={len(result.changed_instrument_ids)} detail={result.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
