"""Audit deprecated exploratory model-versus-instrument comparison infrastructure."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from portfolio_advisor.audit.milestone_4 import audit_workbooks
from portfolio_advisor.workflows import build_capital_conservation_reference_workflow

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from scripts.audit_capital_conservation_shortlist import source_contract
from scripts.validate_schema_v3_shortlist import validate_shortlist_stage


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/portfolio_advisor.sqlite"))
    parser.add_argument("--workbooks", type=Path, default=Path("data/xls/processed"))
    parser.add_argument("--as-of", type=date.fromisoformat)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    workbook_audit = audit_workbooks(arguments.workbooks)
    fingerprints, dataset = source_contract(workbook_audit)
    shortlist_validation = validate_shortlist_stage(arguments.database, workbook_audit)
    result = build_capital_conservation_reference_workflow(
        database_path=arguments.database,
        repository_root=_REPOSITORY_ROOT,
        expected_workbook_fingerprints=fingerprints,
        expected_shortlist_manifest_fingerprint=dataset,
        as_of=arguments.as_of,
    )
    payload = {
        "audit_schema_version": 1,
        "boundaries": {
            "allocation": "NOT_PERFORMED",
            "cash_deployment": "NOT_PERFORMED",
            "database_access": "READ_ONLY",
            "fx_conversion": "NOT_PERFORMED",
            "outcome_success_criteria": "NOT_IMPLEMENTED",
            "persistence": "NOT_PERFORMED",
            "production_cutover": "NOT_AUTHORIZED",
            "roadmap_finalist_comparison": "NOT_IMPLEMENTED",
        },
        "classification": "EXPLORATORY_MODEL_VERSUS_INSTRUMENT_COMPARISON",
        "shortlist_stage_validation": {**shortlist_validation, "status": "PASS"},
        "summary": {
            "common_as_of_date": result.common_as_of_date.isoformat(),
            "comparison_policy_fingerprint": result.recommendation.comparison_policy_fingerprint,
            "model_finalist": result.model_finalist.display_name,
            "recommendation": result.recommendation.status.value,
            "recommendation_fingerprint": result.recommendation.fingerprint,
            "shortlist_finalist": result.shortlist_finalist.stable_id,
            "user_choice_state": result.user_choice_state.value,
            "valid_choice_options": [choice.value for choice in result.valid_choice_options],
            "workflow_fingerprint": result.workflow_fingerprint,
        },
        "workflow": result.to_dict(),
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
