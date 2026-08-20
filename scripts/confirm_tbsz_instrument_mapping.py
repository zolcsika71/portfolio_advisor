"""Record a reviewed manual instrument identity mapping for TBSZ comparison."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.tbsz.models import TbszError
from portfolio_advisor.tbsz.repository import TbszPortfolioRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument-id", required=True, type=int)
    parser.add_argument("--isin", required=True)
    parser.add_argument("--alias", required=True, help="Exact manually reviewed source/provider name")
    parser.add_argument("--database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    args = parser.parse_args(argv)
    try:
        instrument = TbszPortfolioRepository(args.database).confirm_instrument_mapping(
            args.instrument_id, args.isin, args.alias
        )
    except TbszError as error:
        print(f"MANUAL_MAPPING_REJECTED detail={error}", file=sys.stderr)
        return 2
    print(f"MANUAL_MAPPING_CONFIRMED instrument_id={instrument.instrument_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
