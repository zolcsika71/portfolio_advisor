"""Deterministic read-only audit of governed capital-conservation construction."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from portfolio_advisor.audit.milestone_4 import audit_workbooks
from portfolio_advisor.construction import construct_capital_conservation_shortlist

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from scripts.validate_schema_v3_shortlist import validate_shortlist_stage


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/portfolio_advisor.sqlite"))
    parser.add_argument("--workbooks", type=Path, default=Path("data/xls/processed"))
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def _source_contract(audit: dict[str, Any]) -> tuple[dict[str, str], str]:
    sheets = [
        sheet
        for sheet in audit["files"]
        if sheet["source_type"] == "SHORTLIST_XLS" and sheet["status"] == "AUDITED"
    ]
    fingerprints = {str(sheet["file"]): str(sheet["file_sha256"]) for sheet in sheets}
    dataset = hashlib.sha256(
        json.dumps(sheets, sort_keys=True, default=str).encode()
    ).hexdigest()
    return fingerprints, dataset


def main() -> int:
    arguments = _arguments()
    root = _REPOSITORY_ROOT
    audit = audit_workbooks(arguments.workbooks)
    fingerprints, dataset = _source_contract(audit)
    stage_validation = validate_shortlist_stage(arguments.database, audit)
    result = construct_capital_conservation_shortlist(
        database_path=arguments.database,
        repository_root=root,
        expected_workbook_fingerprints=fingerprints,
        expected_manifest_fingerprint=dataset,
        as_of=arguments.as_of,
        limit=arguments.limit,
    )
    payload = {
        "audit_schema_version": 1,
        "boundary": {
            "allocation": "NOT_PERFORMED",
            "cash_deployment": "NOT_PERFORMED",
            "database_access": "READ_ONLY",
            "finalist_comparison": "NOT_IMPLEMENTED",
            "fx_conversion": "NOT_PERFORMED",
            "outcome_success_criteria": "NOT_IMPLEMENTED",
            "production_cutover": "NOT_AUTHORIZED",
        },
        "construction": result.to_dict(),
        "stage_validation": {**stage_validation, "status": "PASS"},
        "summary": {
            "candidate_count": len(result.candidates),
            "constructed_count": len(result.constructed),
            "eligible_count": sum(item.eligible for item in result.candidates),
            "latest_complete_snapshot": result.provenance.snapshot_date.isoformat(),
            "result_fingerprint": result.result_fingerprint,
            "top_ranked_instrument": result.constructed[0].isin,
        },
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
