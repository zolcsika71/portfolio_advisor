"""Plan the no-network acquisition of strict-complete missing NAV evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.history.nav_acquisition import (
    HistoricalNavAcquisitionError,
    build_historical_nav_acquisition_targets,
    write_historical_nav_acquisition_targets,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=Path("data/features/official_forward_labels.csv"))
    parser.add_argument("--coverage", type=Path, default=Path("data/audit/backtest_window_coverage.json"))
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--history-store", type=Path, default=Path("database/official_historical_nav.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/historical_nav_acquisition_targets.json"))
    args = parser.parse_args(argv)
    try:
        payload = build_historical_nav_acquisition_targets(
            label_store_path=args.labels,
            coverage_path=args.coverage,
            database_path=args.database,
            history_store_path=args.history_store,
        )
        write_historical_nav_acquisition_targets(args.output, payload)
    except (HistoricalNavAcquisitionError, ValueError, RuntimeError) as exc:
        print(f"Historical NAV acquisition planning failed: {exc}", file=sys.stderr)
        return 2
    print(f"Historical NAV acquisition targets: {payload['target_count']}")
    print(f"Potentially recoverable labels: {payload['potentially_recoverable_label_count']}")
    print(f"JSON output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
