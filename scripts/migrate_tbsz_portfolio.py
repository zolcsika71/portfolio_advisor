"""Apply the explicit, backup-verified local TBSZ schema migration only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.tbsz.models import TbszError
from portfolio_advisor.tbsz.repository import (
    CURRENT_SCHEMA_VERSION,
    TbszPortfolioRepository,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    args = parser.parse_args(argv)
    repository = TbszPortfolioRepository(args.database)
    try:
        old_version = repository.schema_version() if args.database.exists() else 0
        backup_path = repository.initialize()
        new_version = repository.schema_version()
    except (OSError, TbszError, ValueError) as error:
        print(f"TBSZ_MIGRATION_FAILED detail={error}", file=sys.stderr)
        return 2
    if new_version != CURRENT_SCHEMA_VERSION:
        print("TBSZ_MIGRATION_FAILED detail=unexpected post-migration schema version", file=sys.stderr)
        return 2
    print(
        f"TBSZ_MIGRATION_OK old_version={old_version} new_version={new_version} "
        f"backup={backup_path if backup_path is not None else 'NONE'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
