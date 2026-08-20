"""Initialize the local TBSZ evidence database from confirmed George PDFs.

This script never submits or prepares brokerage orders.  Image-only screens
require a local, explicitly confirmed manifest; use --write-template to create
the safe filename-only starting point.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.tbsz.models import SourceConflictError, TbszError
from portfolio_advisor.tbsz.repository import TbszPortfolioRepository
from portfolio_advisor.tbsz.source_import import (
    SOURCE_FIELD_REQUIRES_MANUAL_CONFIRMATION,
    import_george_pdf_directory,
    write_manual_confirmation_template,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/tbsz/source"))
    parser.add_argument("--manual-confirmations", type=Path, default=Path("data/tbsz/manual_confirmations.json"))
    parser.add_argument("--database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    parser.add_argument("--write-template", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.write_template:
            filenames = write_manual_confirmation_template(args.source, args.manual_confirmations)
            print(f"MANUAL_CONFIRMATION_TEMPLATE_WRITTEN documents={len(filenames)}")
            return 0
        repository = TbszPortfolioRepository(args.database)
        repository.initialize()
        result = import_george_pdf_directory(repository, args.source, args.manual_confirmations)
    except (OSError, SourceConflictError, TbszError, ValueError) as error:
        print(f"TBSZ_SOURCE_IMPORT_FAILED stage=source_validation detail={error}", file=sys.stderr)
        return 2
    print(
        f"{result.status} discovered={len(result.discovered_filenames)} "
        f"imported={len(result.imported_filenames)} already_imported={len(result.already_imported_filenames)}"
    )
    if result.status == SOURCE_FIELD_REQUIRES_MANUAL_CONFIRMATION:
        print(f"confirmation_required={len(result.confirmation_required_filenames)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
