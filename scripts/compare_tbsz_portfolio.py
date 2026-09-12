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
from portfolio_advisor.tbsz.comparison import (
    DescriptivePortfolioComparison,
    DescriptiveStrategy,
    PortfolioComparison,
    compare_tbsz_to_recommended_portfolio,
    compare_tbsz_to_selected_portfolio_descriptively,
)
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
    parser.add_argument(
        "--mode",
        choices=("legacy", "descriptive"),
        default="legacy",
        help="Legacy action classification or action-free single-account description",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--account")
    scope.add_argument("--all-tbsz", action="store_true")
    parser.add_argument("--target-portfolio")
    parser.add_argument(
        "--tolerance",
        type=_decimal,
        help="Legacy mode only; 0.01 means one percentage point",
    )
    parser.add_argument(
        "--strategy",
        choices=tuple(strategy.value for strategy in DescriptiveStrategy),
        help="Required in descriptive mode",
    )
    parser.add_argument(
        "--investment-horizon-days",
        type=int,
        help="Required in descriptive mode; positive and no longer than 365 days",
    )
    parser.add_argument(
        "--output",
        choices=("json",),
        default="json",
        help="Structured read-only report format",
    )
    parser.add_argument(
        "--tbsz-database", type=Path, default=Path("database/tbsz_portfolio.sqlite")
    )
    parser.add_argument(
        "--model-database", type=Path, default=Path("database/model_portfolio.sqlite")
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(
            "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
        ),
    )
    parser.add_argument(
        "--identity-confirmations",
        type=Path,
        default=Path("data/tbsz/ltia_identity_confirmations.json"),
        help="Descriptive mode only; approved read-only LTIA identity overlay",
    )
    parser.add_argument(
        "--identity-registry-audit",
        type=Path,
        default=Path("data/audit/milestone_4_current_data_audit.json"),
        help="Descriptive mode only; canonical identity/currency evidence",
    )
    args = parser.parse_args(argv)
    if args.mode == "descriptive":
        if args.all_tbsz:
            parser.error("descriptive mode requires exactly one --account")
        if args.target_portfolio is None:
            parser.error("descriptive mode requires --target-portfolio")
        if args.strategy is None:
            parser.error("descriptive mode requires --strategy")
        if args.investment_horizon_days is None:
            parser.error("descriptive mode requires --investment-horizon-days")
        if args.tolerance is not None:
            parser.error("descriptive mode does not use or accept --tolerance")
    elif args.tolerance is None:
        parser.error("legacy mode requires --tolerance")
    result: DescriptivePortfolioComparison | PortfolioComparison
    try:
        if args.mode == "descriptive":
            result = compare_tbsz_to_selected_portfolio_descriptively(
                tbsz_repository=TbszPortfolioRepository(args.tbsz_database),
                model_repository=ModelPortfolioRepository(args.model_database),
                rules_path=args.rules,
                account_label=args.account,
                target_portfolio_name=args.target_portfolio,
                strategy=args.strategy,
                investment_horizon_days=args.investment_horizon_days,
                identity_confirmations_path=args.identity_confirmations,
                identity_registry_audit_path=args.identity_registry_audit,
            )
        else:
            assert args.tolerance is not None
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
