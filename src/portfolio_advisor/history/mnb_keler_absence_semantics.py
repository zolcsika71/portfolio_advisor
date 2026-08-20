"""Freeze terminal MNB/KELER absent-row research without promoting absence.

This audit-only module deliberately makes a completed ``NOT_FOUND`` research
result actionable as a guardrail: a missing exact-ISIN row stays a missing
observation until genuinely new, locally retained and applicable primary
evidence is explicitly reviewed.  It never creates a transaction, price, or
return observation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from .sparse_trading_semantics import NO_EXACT_ISIN_OBSERVATION

TARGET_ISIN = "HU0000554795"
FROZEN_STATUS = "AUTHORITATIVE_EVIDENCE_NOT_FOUND"
ABSENCE_SEMANTICS_UNKNOWN = "ABSENCE_SEMANTICS_UNKNOWN"
REQUIRED_MISSING_LINKS = frozenset(
    {
        "B_report_completeness",
        "C_row_inclusion",
        "D_zero_transaction_omission",
        "transaction_count_semantics",
    }
)


class AbsenceSemanticsFreezeError(RuntimeError):
    """The terminal research result cannot safely be frozen or reconsidered."""


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AbsenceSemanticsFreezeError(f"{label} must be an object")
    return value


def build_absence_semantics_freeze(
    *,
    scope_semantics: Mapping[str, object],
    research_ledger: Mapping[str, object],
    sparse_semantics: Mapping[str, object],
    scope_artifact: str,
    evidence_ledger: str,
) -> dict[str, object]:
    """Build the deterministic terminal decision from existing offline audits."""
    if scope_semantics.get("semantic_status") != "REPORT_SCOPE_SEMANTICS_NOT_FOUND":
        raise AbsenceSemanticsFreezeError("Only terminal NOT_FOUND scope research can freeze UNKNOWN")
    if research_ledger.get("research_status") != "REPORT_SCOPE_SEMANTICS_NOT_FOUND":
        raise AbsenceSemanticsFreezeError("Scope evidence ledger is not terminal NOT_FOUND")
    if research_ledger.get("stopping_rule_completed") is not True:
        raise AbsenceSemanticsFreezeError("Scope research stopping rule was not completed")
    evidence = _require_mapping(scope_semantics.get("evidence_chain"), "scope evidence chain")
    if evidence.get("absence_semantics_validated") is not False:
        raise AbsenceSemanticsFreezeError("Validated absence semantics cannot be frozen as unknown")
    missing = {
        name
        for name in REQUIRED_MISSING_LINKS
        if evidence.get(
            {
                "B_report_completeness": "report_completeness_validated",
                "C_row_inclusion": "row_inclusion_rule_validated",
                "D_zero_transaction_omission": "zero_transaction_omission_validated",
                "transaction_count_semantics": "transaction_count_semantics_validated",
            }[name]
        )
        is False
    }
    if missing != REQUIRED_MISSING_LINKS:
        raise AbsenceSemanticsFreezeError("Terminal scope research has unexpected mandatory-link state")
    counts = _require_mapping(sparse_semantics.get("semantic_status_counts"), "sparse semantic counts")
    if (
        counts.get("OBSERVED_OTC_ACTIVITY") != 3
        or counts.get("NO_EXACT_ISIN_OBSERVATION") != 23
        or counts.get("NO_REPORTED_KELER_OTC_ACTIVITY", 0) != 0
    ):
        raise AbsenceSemanticsFreezeError("Sparse report classifications no longer match frozen baseline")
    return {
        "schema_version": 1,
        "target_report_family": "MNB-hosted KELER weekly OTC securities turnover report",
        "target_instrument": {
            "isin": TARGET_ISIN,
            "series": "K2025/23",
            "instrument_name": "K250604 Egyéves Magyar Állampapír",
            "currency": "HUF",
        },
        "previous_research_status": "REPORT_SCOPE_SEMANTICS_NOT_FOUND",
        "research_terminal_outcome": "REPORT_SCOPE_SEMANTICS_NOT_FOUND",
        "absence_semantics_status": FROZEN_STATUS,
        "frozen_interpretation": ABSENCE_SEMANTICS_UNKNOWN,
        "absence_semantics_research_closed": True,
        "absence_semantics_validated": False,
        "stopping_rule_completed": True,
        "scope_semantics_artifact": scope_artifact,
        "evidence_ledger": evidence_ledger,
        "mandatory_links_proven": ["A_report_scope"],
        "mandatory_links_missing": sorted(missing),
        "absent_report_classification": NO_EXACT_ISIN_OBSERVATION,
        "classification_counts": {
            "NO_EXACT_ISIN_OBSERVATION": 23,
            "NO_REPORTED_KELER_OTC_ACTIVITY": 0,
            "OBSERVED_OTC_ACTIVITY": 3,
        },
        "unused_classification": "NO_REPORTED_KELER_OTC_ACTIVITY",
        "forbidden_interpretations": [
            "zero transactions",
            "zero KELER transactions",
            "zero OTC transactions",
            "no market activity",
            "no market price",
            "zero price",
            "unchanged price",
            "inactive instrument",
            "a missing value suitable for fill",
        ],
        "reopen_policy": {
            "eligible_only_if_all": [
                "new evidence is locally retained",
                "new evidence SHA-256 is verified",
                "source authority is validated",
                "source applies to the 2024-2025 reporting period",
                "source explicitly concerns the public KELER weekly OTC reports",
                "source establishes at least one currently missing mandatory link",
            ],
            "does_not_reopen": [
                "another report with the same table shape",
                "additional absent rows or weekly PDFs",
                "search snippets",
                "unofficial explanations",
                "AI inference",
            ],
            "reconsideration_is_not_promotion": True,
        },
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
    }


def eligible_for_explicit_reconsideration(candidate: Mapping[str, object]) -> bool:
    """Return whether new evidence may be reviewed; never promote semantics here."""
    required_true = ("locally_retained", "official", "report_family_relevant")
    if any(candidate.get(field) is not True for field in required_true):
        return False
    if candidate.get("applicability_status") != "VALIDATED_2024_2025":
        return False
    links = candidate.get("establishes_missing_links")
    if not isinstance(links, list) or not set(links).intersection(REQUIRED_MISSING_LINKS):
        return False
    local_path = candidate.get("local_path")
    expected_hash = candidate.get("sha256")
    if not isinstance(local_path, str) or not isinstance(expected_hash, str) or len(expected_hash) != 64:
        return False
    path = Path(local_path)
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash


def classify_frozen_absence(*, exact_isin_present: bool) -> str:
    """Guard the frozen state against structural or repeated-absence promotion."""
    return "OBSERVED_OTC_ACTIVITY" if exact_isin_present else NO_EXACT_ISIN_OBSERVATION
