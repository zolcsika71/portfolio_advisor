"""Build the offline, strict official forward-label store."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.features.official_forward_labels import (
    OfficialForwardLabelStoreError,
    build_official_forward_label_store,
    write_official_forward_label_manifest,
    write_official_forward_labels_csv,
)


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=Path("data/features/point_in_time_portfolio_features.csv"))
    parser.add_argument("--feature-manifest", type=Path, default=Path("data/audit/point_in_time_portfolio_feature_dataset.json"))
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--rules", type=Path, default=Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml"))
    parser.add_argument("--contract", type=Path, default=Path("data/audit/capital_preservation_ranking_policy_contract.json"))
    parser.add_argument("--strict", type=Path, default=Path("data/audit/strict_backtest_pipeline_validation.json"))
    parser.add_argument("--methodology", type=Path, default=Path("data/audit/capital_preservation_metrics_ranking_validation.json"))
    parser.add_argument("--current", type=Path, default=Path("data/audit/active_ranking_policy_current_universe_validation.json"))
    parser.add_argument("--temporal", type=Path, default=Path("data/audit/active_ranking_policy_temporal_stability.json"))
    parser.add_argument("--output", type=Path, default=Path("data/features/official_forward_labels.csv"))
    parser.add_argument("--manifest", type=Path, default=Path("data/audit/official_forward_label_store.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _arguments().parse_args(argv)
    kwargs = {
        "feature_dataset_path": args.features,
        "feature_manifest_path": args.feature_manifest,
        "database_path": args.database,
        "rules_path": args.rules,
        "contract_path": args.contract,
        "strict_pipeline_path": args.strict,
        "methodology_path": args.methodology,
        "current_universe_path": args.current,
        "temporal_path": args.temporal,
    }
    try:
        labels, manifest = build_official_forward_label_store(**kwargs)
        repeated_labels, repeated_manifest = build_official_forward_label_store(**kwargs)
        if labels != repeated_labels or manifest != repeated_manifest:
            raise OfficialForwardLabelStoreError("repeated forward-label build was not deterministic")
        write_official_forward_labels_csv(args.output, labels)
        write_official_forward_label_manifest(args.manifest, manifest)
    except (OfficialForwardLabelStoreError, ValueError, RuntimeError) as exc:
        print(f"Official forward-label store build failed: {exc}", file=sys.stderr)
        return 2
    print(f"Official forward-label store: {manifest['validation_status']}")
    print(f"Candidates: {manifest['candidate_label_count']}; official: {manifest['available_label_count']}")
    print(f"CSV output: {args.output}")
    print(f"Manifest output: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
