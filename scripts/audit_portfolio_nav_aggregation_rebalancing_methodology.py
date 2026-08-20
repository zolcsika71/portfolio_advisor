"""Audit local evidence for a fail-closed portfolio-NAV methodology.

This command performs no network I/O and does not construct portfolio NAV or
forward labels. It only materializes the approval evidence and blockers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.history.portfolio_nav_methodology import (
    PortfolioNavMethodologyError,
    build_portfolio_nav_methodology_audit,
    write_portfolio_nav_methodology_audit,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--nav-store", type=Path, default=Path("database/official_historical_nav.sqlite"))
    parser.add_argument("--strict-validation", type=Path, default=Path("data/audit/strict_backtest_pipeline_validation.json"))
    parser.add_argument("--labels", type=Path, default=Path("data/features/official_forward_labels.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/portfolio_nav_aggregation_rebalancing_methodology.json"))
    args = parser.parse_args(argv)
    try:
        payload = build_portfolio_nav_methodology_audit(
            database_path=args.database,
            nav_store_path=args.nav_store,
            strict_validation_path=args.strict_validation,
            label_store_path=args.labels,
        )
        write_portfolio_nav_methodology_audit(args.output, payload)
    except (PortfolioNavMethodologyError, OSError, ValueError, RuntimeError) as exc:
        print(f"Portfolio NAV methodology audit failed: {exc}", file=sys.stderr)
        return 2
    print(f"Portfolio NAV methodology: {payload['validation_status']}")
    print(f"Activation state: {payload['activation_state']}")
    print(f"Evidence fingerprint: {payload['evidence_fingerprint']}")
    print(f"JSON output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
