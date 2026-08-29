"""Create the isolated, one-time current TBSZ standings database.

The database contains manually confirmed current ASSET holdings and CASH
balances only. It neither recommends nor executes trades.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from portfolio_advisor.tbsz.current_standings import (
    CurrentStandingsError,
    OutputAlreadyExistsError,
    create_current_standings_database,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/tbsz/source"))
    parser.add_argument(
        "--manual-confirmations",
        type=Path,
        default=Path("data/tbsz/current_standings_confirmations.json"),
    )
    parser.add_argument("--output", type=Path, default=Path("database/tbsz_current_portfolio.sqlite"))
    parser.add_argument("--force", action="store_true", help="Back up and replace an existing output database.")
    args = parser.parse_args(argv)
    try:
        result = create_current_standings_database(
            source_directory=args.source,
            confirmations_path=args.manual_confirmations,
            output_path=args.output,
            force=args.force,
        )
    except OutputAlreadyExistsError as error:
        print(f"REFUSE_TO_OVERWRITE detail={error}", file=sys.stderr)
        return 2
    except (CurrentStandingsError, OSError, sqlite3.Error) as error:
        print(f"TBSZ_CURRENT_STANDINGS_CREATION_FAILED detail={error}", file=sys.stderr)
        return 2
    print(
        "TBSZ_CURRENT_STANDINGS_CREATED "
        f"accounts={result.account_count} source_documents={result.source_document_count} "
        f"instruments={result.instrument_count} assets={result.position_count} cash={result.cash_count}"
    )
    if result.backup_path is not None:
        print(f"backup_path={result.backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
