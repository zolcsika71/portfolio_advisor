"""Build a read-only, advisory-only TBSZ-vs-model portfolio comparison."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path

from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.tbsz.comparison import compare_tbsz_to_recommended_portfolio
from portfolio_advisor.tbsz.repository import TbszPortfolioRepository


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a decimal") from error


def _json_default(value: object) -> object:
    if isinstance(value, (Decimal, date)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot serialize {type(value).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--account")
    scope.add_argument("--all-tbsz", action="store_true")
    parser.add_argument("--target-portfolio")
    parser.add_argument("--tolerance", required=True, type=_decimal, help="Fraction; 0.01 means one percentage point")
    parser.add_argument("--tbsz-database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    parser.add_argument("--model-database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--rules", type=Path, default=Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml"))
    args = parser.parse_args(argv)
    try:
        result = compare_tbsz_to_recommended_portfolio(
            tbsz_repository=TbszPortfolioRepository(args.tbsz_database),
            model_repository=ModelPortfolioRepository(args.model_database),
            rules_path=args.rules,
            account_label=args.account,
            all_tbsz=args.all_tbsz,
            target_portfolio_name=args.target_portfolio,
            tolerance=args.tolerance,
        )
    except (RuntimeError, ValueError) as error:
        print(f"TBSZ_COMPARISON_FAILED detail={error}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), default=_json_default, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
