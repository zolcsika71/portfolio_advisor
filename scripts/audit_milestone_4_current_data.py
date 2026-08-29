"""Write the deterministic, read-only Milestone 4 current-data audit report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from portfolio_advisor.audit.milestone_4 import audit_milestone_4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-directory", type=Path, default=Path("database"))
    parser.add_argument("--workbook-directory", type=Path, default=Path("data/xls/processed"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/milestone_4_current_data_audit.json"))
    args = parser.parse_args(argv)

    report = audit_milestone_4(
        database_directory=args.database_directory,
        workbook_directory=args.workbook_directory,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = report["xls_inventory"]["summary"]
    print(
        "MILESTONE_4_AUDIT "
        f"output={args.output} databases={len(report['database_inventory'])} "
        f"workbooks={summary['workbook_count']} target_sheets={summary['target_sheet_count']} "
        f"valid_isins={report['canonical_instrument_registry_seed']['valid_isin_count']} "
        f"unresolved_rows={summary['unresolved_identity_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
