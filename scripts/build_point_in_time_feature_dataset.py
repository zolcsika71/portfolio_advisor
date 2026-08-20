"""Build the offline, deterministic point-in-time portfolio feature dataset."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.features.dataset import (
    DatasetBuildError,
    build_point_in_time_feature_dataset,
    write_dataset_csv,
    write_manifest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--rules", type=Path, default=Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml"))
    parser.add_argument("--graph", type=Path, default=Path("data/knowledge/graphify-out/graph.json"))
    parser.add_argument("--contract", type=Path, default=Path("data/audit/capital_preservation_ranking_policy_contract.json"))
    parser.add_argument("--dataset", type=Path, default=Path("data/features/point_in_time_portfolio_features.csv"))
    parser.add_argument("--manifest", type=Path, default=Path("data/audit/point_in_time_portfolio_feature_dataset.json"))
    args = parser.parse_args(argv)
    try:
        rows, manifest = build_point_in_time_feature_dataset(
            database_path=args.database, rules_path=args.rules, graph_path=args.graph, contract_path=args.contract,
        )
        write_dataset_csv(args.dataset, rows)
        write_manifest(args.manifest, manifest)
    except (DatasetBuildError, ValueError, RuntimeError) as error:
        print(f"Point-in-time feature dataset build failed: {error}", file=sys.stderr)
        return 2
    print(f"Point-in-time feature dataset: {manifest['dataset_status']}")
    print(f"Rows: {manifest['row_count']}; fingerprint: {manifest['dataset_fingerprint']}")
    print(f"Dataset output: {args.dataset}")
    print(f"Manifest output: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
