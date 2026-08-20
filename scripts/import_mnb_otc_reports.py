"""Import already-downloaded MNB/KELER OTC weekly-report PDFs without network I/O."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.history.mnb_otc import (
    MnbOtcError,
    MnbOtcRepository,
)
from portfolio_advisor.history.mnb_otc_inventory import inspect_local_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory of manually downloaded MNB/KELER PDFs.")
    parser.add_argument(
        "--database", type=Path, default=Path("database/model_portfolio.sqlite"), help="SQLite destination."
    )
    args = parser.parse_args()
    if not args.directory.is_dir():
        print(f"MNB OTC input directory does not exist: {args.directory}", file=sys.stderr)
        return 1
    try:
        repository = MnbOtcRepository(args.database)
        imported = skipped = absent = 0
        seen_hashes: set[str] = set()
        root = Path.cwd()
        for path in sorted(args.directory.glob("*.pdf")):
            record = inspect_local_artifact(path.resolve(), root=root)
            if record is None:
                continue
            if record.duplicate_of is not None or record.sha256 in seen_hashes:
                skipped += 1
                continue
            seen_hashes.add(record.sha256)
            if not record.contains_exact_isin:
                absent += 1
                continue
            if record.observation is None:
                raise MnbOtcError(f"MNB OTC exact-ISIN report cannot be imported: {record.parser_status}")
            if repository.import_observation(record.observation):
                imported += 1
            else:
                skipped += 1
    except (MnbOtcError, RuntimeError) as exc:
        print(f"MNB OTC import failed closed: {exc}", file=sys.stderr)
        return 1
    print(f"MNB OTC reports imported: {imported}")
    print(f"MNB OTC idempotent re-imports skipped: {skipped}")
    print(f"MNB OTC acquired reports with exact ISIN absent: {absent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
