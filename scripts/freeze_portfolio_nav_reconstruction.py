"""Freeze unsupported portfolio-NAV reconstruction from retained evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.history.portfolio_nav_reconstruction_freeze import (
    PortfolioNavReconstructionFrozenError,
    build_portfolio_nav_reconstruction_freeze,
    write_portfolio_nav_reconstruction_freeze,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--methodology", type=Path, default=Path("data/audit/portfolio_nav_aggregation_rebalancing_methodology.json"))
    parser.add_argument("--blocker-resolution", type=Path, default=Path("data/audit/portfolio_nav_methodology_blocker_resolution.json"))
    parser.add_argument("--duplicate-resolution", type=Path, default=Path("data/audit/portfolio_duplicate_constituent_resolution.json"))
    parser.add_argument("--label-store", type=Path, default=Path("data/audit/official_forward_label_store.json"))
    parser.add_argument("--feature-dataset", type=Path, default=Path("data/audit/point_in_time_portfolio_feature_dataset.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/portfolio_nav_reconstruction_freeze.json"))
    args = parser.parse_args(argv)
    try:
        payload = build_portfolio_nav_reconstruction_freeze(
            repository_root=Path.cwd(),
            methodology_path=args.methodology,
            blocker_resolution_path=args.blocker_resolution,
            duplicate_resolution_path=args.duplicate_resolution,
            label_store_path=args.label_store,
            feature_dataset_path=args.feature_dataset,
        )
        write_portfolio_nav_reconstruction_freeze(args.output, payload)
    except (OSError, ValueError, PortfolioNavReconstructionFrozenError) as error:
        print(f"Portfolio NAV reconstruction freeze failed: {error}", file=sys.stderr)
        return 2
    print(f"Portfolio NAV reconstruction freeze: {payload['validation_status']}")
    print(f"JSON output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
