"""Resolve portfolio-NAV methodology blockers from retained local evidence only."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from portfolio_advisor.history.portfolio_nav_blocker_resolution import (
    PortfolioNavBlockerResolutionError,
    build_portfolio_nav_blocker_resolution,
    write_resolution_artifact,
)
from portfolio_advisor.history.portfolio_nav_methodology import (
    build_portfolio_nav_methodology_audit,
    write_portfolio_nav_methodology_audit,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--nav-store", type=Path, default=Path("database/official_historical_nav.sqlite"))
    parser.add_argument("--processed-workbooks", type=Path, default=Path("data/xls/processed"))
    parser.add_argument(
        "--worksheet-importer",
        type=Path,
        default=Path("src/portfolio_advisor/DB_creation/excel_processing.py"),
    )
    parser.add_argument(
        "--database-importer",
        type=Path,
        default=Path("src/portfolio_advisor/DB_creation/database_create.py"),
    )
    parser.add_argument(
        "--model-repository",
        type=Path,
        default=Path("src/portfolio_advisor/database/repository.py"),
    )
    parser.add_argument("--methodology-doc", type=Path, default=Path("docs/methodology.md"))
    parser.add_argument("--strict-validation", type=Path, default=Path("data/audit/strict_backtest_pipeline_validation.json"))
    parser.add_argument("--labels", type=Path, default=Path("data/features/official_forward_labels.csv"))
    parser.add_argument("--resolution-output", type=Path, default=Path("data/audit/portfolio_nav_methodology_blocker_resolution.json"))
    parser.add_argument("--duplicate-output", type=Path, default=Path("data/audit/portfolio_duplicate_constituent_resolution.json"))
    parser.add_argument("--methodology-output", type=Path, default=Path("data/audit/portfolio_nav_aggregation_rebalancing_methodology.json"))
    args = parser.parse_args(argv)
    try:
        resolution, duplicates = build_portfolio_nav_blocker_resolution(
            database_path=args.database,
            nav_store_path=args.nav_store,
            processed_workbook_dir=args.processed_workbooks,
            worksheet_importer_path=args.worksheet_importer,
            database_importer_path=args.database_importer,
            model_repository_path=args.model_repository,
            methodology_document_path=args.methodology_doc,
        )
        write_resolution_artifact(args.resolution_output, resolution)
        write_resolution_artifact(args.duplicate_output, duplicates)
        methodology = build_portfolio_nav_methodology_audit(
            database_path=args.database,
            nav_store_path=args.nav_store,
            strict_validation_path=args.strict_validation,
            label_store_path=args.labels,
        )
        methodology["blocker_resolution"] = {
            "status": resolution["validation_status"],
            "reference": "data/audit/portfolio_nav_methodology_blocker_resolution.json",
            "duplicate_reference": "data/audit/portfolio_duplicate_constituent_resolution.json",
        }
        methodology["evidence_fingerprint"] = _fingerprint(methodology)
        write_portfolio_nav_methodology_audit(args.methodology_output, methodology)
    except (PortfolioNavBlockerResolutionError, OSError, ValueError, RuntimeError) as exc:
        print(f"Portfolio NAV methodology blocker resolution failed: {exc}", file=sys.stderr)
        return 2
    print(f"Blocker resolution: {resolution['validation_status']}")
    print(f"Duplicate resolution: {duplicates['validation_status']}")
    print(f"Methodology: {methodology['validation_status']} / {methodology['activation_state']}")
    print(f"JSON output: {args.resolution_output}")
    return 0


def _fingerprint(payload: dict[str, object]) -> str:
    value = dict(payload)
    value.pop("evidence_fingerprint", None)
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
