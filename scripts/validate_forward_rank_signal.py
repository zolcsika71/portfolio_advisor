"""Validate official forward rank-signal evidence without policy optimisation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from portfolio_advisor.advisor.forward_rank_signal_validation import (
    ForwardRankSignalValidationError,
    build_forward_rank_signal_validation,
)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/features/point_in_time_portfolio_features.csv"))
    parser.add_argument("--manifest", type=Path, default=Path("data/audit/point_in_time_portfolio_feature_dataset.json"))
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--rules", type=Path, default=Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml"))
    parser.add_argument("--contract", type=Path, default=Path("data/audit/capital_preservation_ranking_policy_contract.json"))
    parser.add_argument("--strict", type=Path, default=Path("data/audit/strict_backtest_pipeline_validation.json"))
    parser.add_argument("--methodology", type=Path, default=Path("data/audit/capital_preservation_metrics_ranking_validation.json"))
    parser.add_argument("--current", type=Path, default=Path("data/audit/active_ranking_policy_current_universe_validation.json"))
    parser.add_argument("--temporal", type=Path, default=Path("data/audit/active_ranking_policy_temporal_stability.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/forward_rank_signal_validation.json"))
    args = parser.parse_args(argv)
    try:
        first = build_forward_rank_signal_validation(
            dataset_path=args.dataset, dataset_manifest_path=args.manifest, database_path=args.database,
            rules_path=args.rules, contract_path=args.contract, strict_pipeline_path=args.strict,
            methodology_path=args.methodology, current_universe_path=args.current, temporal_path=args.temporal,
        )
        second = build_forward_rank_signal_validation(
            dataset_path=args.dataset, dataset_manifest_path=args.manifest, database_path=args.database,
            rules_path=args.rules, contract_path=args.contract, strict_pipeline_path=args.strict,
            methodology_path=args.methodology, current_universe_path=args.current, temporal_path=args.temporal,
        )
        if first != second:
            raise ForwardRankSignalValidationError("repeated complete forward-signal validation was not deterministic")
        _write_json_atomic(args.output, first)
    except (ForwardRankSignalValidationError, ValueError, RuntimeError) as error:
        print(f"Forward rank-signal validation failed: {error}", file=sys.stderr)
        return 2
    print(f"Forward rank signal: {first['validation_status']}")
    print(f"JSON output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
