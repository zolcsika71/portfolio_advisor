"""CLI entry point for deterministic portfolio analysis and legacy import."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .advisor.service import CapitalPreservationAdvisor
from .database.repository import ModelPortfolioRepository, RepositoryError

DEFAULT_DATABASE = Path("database/model_portfolio.sqlite")
DEFAULT_RULES = Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml")


def _json_default(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic capital-preservation portfolio ranking")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--allow-proposed-rules", action="store_true")
    parser.add_argument("--top-alternatives", type=int, default=3)
    parser.add_argument(
        "--import",
        dest="run_import",
        action="store_true",
        help="Run the existing Excel import workflow instead of analysis.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run analysis as JSON. Proposed rules require an explicit opt-in."""
    args = _parser().parse_args(argv)
    if args.run_import:
        from .DB_creation.database_create import process_directory

        process_directory(database_path=args.database)
        return 0
    advisor = CapitalPreservationAdvisor(ModelPortfolioRepository(args.database), args.rules)
    try:
        result = advisor.evaluate(
            allow_proposed_rules=args.allow_proposed_rules,
            alternative_count=args.top_alternatives,
        )
    except (RepositoryError, ValueError) as error:
        print(json.dumps({"error": str(error)}, indent=2, sort_keys=True))
        return 2
    print(json.dumps(asdict(result), default=_json_default, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
