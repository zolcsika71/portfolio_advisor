"""Run the retained-data-safe model-portfolio schema-v3 dry-run audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from portfolio_advisor.audit.milestone_4 import audit_workbooks
from portfolio_advisor.database.migrations.model_portfolio_dry_run import (
    dry_run_model_portfolio_to_v3,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--workbooks", type=Path, default=Path("data/xls/processed"))
    parser.add_argument("--destination", type=Path, required=True, help="new non-retained temporary SQLite file")
    parser.add_argument("--output", type=Path, default=Path("data/audit/schema_v3_model_portfolio_migration_dry_run.json"))
    parser.add_argument("--rules", type=Path, default=Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml"))
    arguments = parser.parse_args(argv)
    result = dry_run_model_portfolio_to_v3(
        legacy_path=arguments.source,
        workbook_directory=arguments.workbooks,
        destination_path=arguments.destination,
        rules_path=arguments.rules,
    )
    workbook_audit = audit_workbooks(arguments.workbooks)
    artifact = {
        "report_schema_version": 1,
        "mode": "TEMPORARY_SCHEMA_V3_DRY_RUN_NO_CUTOVER",
        "source_fingerprints": result.source_fingerprints,
        "temporary_destination": {"path": str(arguments.destination), "sha256": result.destination_fingerprint},
        "migrated_counts": result.counts,
        "unresolved_identity_and_metadata_conflicts": {
            "unresolved_identity_rows": workbook_audit["summary"]["unresolved_identity_rows"],
            "identity_conflicts": workbook_audit["identity_conflicts"],
            "metadata_conflicts": workbook_audit["metadata_conflicts"],
        },
        "duplicate_occurrence_results": result.duplicate_occurrences,
        "equivalence_by_date": result.equivalence_by_date,
        "maximum_observed_numeric_delta": result.maximum_numeric_delta,
        "blockers": list(result.blockers),
        "validation_status": "PASS" if all(item["exact"] for item in result.equivalence_by_date.values()) else "FAILED_EXACT_EQUIVALENCE",
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
