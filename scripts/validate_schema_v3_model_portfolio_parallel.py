"""Read-only validation of a schema-v3 parallel model-portfolio target."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from portfolio_advisor.database.migrations.model_portfolio_parallel import (
    result_as_audit,
    validate_parallel_database,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--workbooks", type=Path, default=Path("data/xls/processed"))
    parser.add_argument("--target", type=Path, default=Path("database/portfolio_advisor.sqlite"))
    parser.add_argument("--rules", type=Path, default=Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml"))
    parser.add_argument("--audited-dry-run", type=Path, default=Path("data/audit/schema_v3_model_portfolio_migration_dry_run.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/milestone_7_model_portfolio_relational_migration.json"))
    args = parser.parse_args(argv)
    result = validate_parallel_database(legacy_path=args.source, workbook_directory=args.workbooks, target_path=args.target, rules_path=args.rules, audited_dry_run_path=args.audited_dry_run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result_as_audit(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
