"""Build one disposable copy-on-write Milestone 11B schema candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.database.migrations import (
    ConstructedPortfolioMigrationError,
    build_constructed_portfolio_schema_candidate,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("database/portfolio_advisor.sqlite"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = build_constructed_portfolio_schema_candidate(
            source=arguments.source,
            candidate=arguments.candidate,
        )
    except (ConstructedPortfolioMigrationError, OSError, ValueError) as error:
        print(f"Milestone 11B candidate migration failed: {error}", file=sys.stderr)
        return 2
    output = json.dumps(result.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        sys.stdout.write(output)
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
