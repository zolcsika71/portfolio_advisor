"""Process a launchd WatchPaths event using the existing XLS importer."""

from __future__ import annotations

import argparse
from pathlib import Path

from portfolio_advisor.operations.xls_import_watch import run_watched_xls_import


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Check candidate stability without importing.")
    parser.add_argument("--stability-interval-seconds", type=float, default=3.0)
    parser.add_argument("--stability-retry-limit", type=int, default=2)
    args = parser.parse_args(argv)
    result = run_watched_xls_import(
        repository_root=Path.cwd(),
        dry_run=args.dry_run,
        stability_interval_seconds=args.stability_interval_seconds,
        stability_retry_limit=args.stability_retry_limit,
    )
    suffix = f" canonical_date={result.canonical_date}" if result.canonical_date else ""
    suffix += f" error_stage={result.error_stage}" if result.error_stage else ""
    print(f"{result.status}{suffix}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
