"""Reassess valid forward-validation paths without deriving portfolio returns."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.advisor.forward_validation_strategy import (
    build_forward_validation_strategy_reassessment,
    write_forward_validation_strategy_reassessment,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, default=Path("data/audit/portfolio_nav_reconstruction_freeze.json"))
    parser.add_argument("--methodology", type=Path, default=Path("data/audit/portfolio_nav_aggregation_rebalancing_methodology.json"))
    parser.add_argument("--label-store", type=Path, default=Path("data/audit/official_forward_label_store.json"))
    parser.add_argument("--strict-validation", type=Path, default=Path("data/audit/strict_backtest_pipeline_validation.json"))
    parser.add_argument("--feature-dataset", type=Path, default=Path("data/audit/point_in_time_portfolio_feature_dataset.json"))
    parser.add_argument("--temporal-policy", type=Path, default=Path("data/audit/active_ranking_policy_temporal_stability.json"))
    parser.add_argument("--current-policy", type=Path, default=Path("data/audit/active_ranking_policy_current_universe_validation.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/forward_validation_strategy_reassessment.json"))
    args = parser.parse_args(argv)
    try:
        payload = build_forward_validation_strategy_reassessment(
            repository_root=Path.cwd(),
            freeze_path=args.freeze,
            methodology_path=args.methodology,
            label_store_path=args.label_store,
            strict_validation_path=args.strict_validation,
            feature_dataset_path=args.feature_dataset,
            temporal_policy_path=args.temporal_policy,
            current_policy_path=args.current_policy,
        )
        write_forward_validation_strategy_reassessment(args.output, payload)
    except (OSError, TypeError, ValueError) as error:
        print(f"Forward-validation strategy reassessment failed: {error}", file=sys.stderr)
        return 2
    print(f"Forward-validation strategy: {payload['validation_status']}")
    print(f"Recommended next path: {payload['recommended_next_path']}")
    print(f"JSON output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
