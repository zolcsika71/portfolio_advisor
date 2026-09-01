"""Read-only production-evidence attempt for the implemented 11B foundation."""

from __future__ import annotations

import hashlib
import json
import warnings
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from portfolio_advisor.audit.milestone_4 import audit_workbooks
from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    load_capital_defensive_construction_policy,
)

from .capital_conservation import construct_capital_conservation_shortlist
from .evidence import load_construction_instrument_evidence
from .models import ConstructionEvidenceReadiness, ConstructionResult
from .service import construct_capital_defensive_portfolio


def attempt_current_production_construction(
    *,
    database_path: Path,
    repository_root: Path,
    workbook_directory: Path,
    cash_by_currency: Mapping[str, Decimal],
) -> ConstructionResult:
    """Attempt construction read-only and expose current governed data blockers."""
    audit = audit_workbooks(workbook_directory)
    sheets = [
        sheet
        for sheet in audit["files"]
        if sheet["source_type"] == "SHORTLIST_XLS" and sheet["status"] == "AUDITED"
    ]
    fingerprints = {str(sheet["file"]): str(sheet["file_sha256"]) for sheet in sheets}
    dataset_fingerprint = hashlib.sha256(
        json.dumps(sheets, sort_keys=True, default=str).encode()
    ).hexdigest()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        screening = construct_capital_conservation_shortlist(
            database_path=database_path,
            repository_root=repository_root,
            expected_workbook_fingerprints=fingerprints,
            expected_manifest_fingerprint=dataset_fingerprint,
        )
    evidence = load_construction_instrument_evidence(database_path, screening)
    policy = load_capital_defensive_construction_policy(
        repository_root / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
    )
    return construct_capital_defensive_portfolio(
        screening=screening,
        cash_by_currency=cash_by_currency,
        policy=policy,
        instruments=evidence,
        readiness=ConstructionEvidenceReadiness(
            official_reference_rate_observations_validated=False,
            official_reference_rate_methodology_validated=False,
            portfolio_risk_metrics_available=False,
        ),
    )
