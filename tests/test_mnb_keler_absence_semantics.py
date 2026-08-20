from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.history.mnb_keler_absence_semantics import (
    AbsenceSemanticsFreezeError,
    build_absence_semantics_freeze,
    classify_frozen_absence,
    eligible_for_explicit_reconsideration,
)


def scope() -> dict[str, object]:
    return {
        "semantic_status": "REPORT_SCOPE_SEMANTICS_NOT_FOUND",
        "evidence_chain": {
            "absence_semantics_validated": False,
            "report_completeness_validated": False,
            "row_inclusion_rule_validated": False,
            "zero_transaction_omission_validated": False,
            "transaction_count_semantics_validated": False,
        },
    }


def ledger() -> dict[str, object]:
    return {
        "research_status": "REPORT_SCOPE_SEMANTICS_NOT_FOUND",
        "stopping_rule_completed": True,
    }


def sparse() -> dict[str, object]:
    return {
        "semantic_status_counts": {
            "OBSERVED_OTC_ACTIVITY": 3,
            "NO_EXACT_ISIN_OBSERVATION": 23,
            "NO_REPORTED_KELER_OTC_ACTIVITY": 0,
        }
    }


def test_not_found_scope_research_freezes_unknown_without_promotion() -> None:
    artifact = build_absence_semantics_freeze(
        scope_semantics=scope(),
        research_ledger=ledger(),
        sparse_semantics=sparse(),
        scope_artifact="scope.json",
        evidence_ledger="ledger.json",
    )

    assert artifact["absence_semantics_research_closed"] is True
    assert artifact["absence_semantics_validated"] is False
    assert artifact["absent_report_classification"] == "NO_EXACT_ISIN_OBSERVATION"
    counts = cast(dict[str, object], artifact["classification_counts"])
    assert counts["NO_REPORTED_KELER_OTC_ACTIVITY"] == 0


@pytest.mark.parametrize("trigger", [False, True])
def test_structure_or_repeated_absence_cannot_promote_frozen_semantics(trigger: bool) -> None:
    assert classify_frozen_absence(exact_isin_present=trigger) == (
        "OBSERVED_OTC_ACTIVITY" if trigger else "NO_EXACT_ISIN_OBSERVATION"
    )


def test_bad_baseline_or_reclassification_fails_closed() -> None:
    malformed = sparse()
    malformed["semantic_status_counts"] = {
        "OBSERVED_OTC_ACTIVITY": 3,
        "NO_EXACT_ISIN_OBSERVATION": 22,
        "NO_REPORTED_KELER_OTC_ACTIVITY": 1,
    }
    with pytest.raises(AbsenceSemanticsFreezeError):
        build_absence_semantics_freeze(
            scope_semantics=scope(),
            research_ledger=ledger(),
            sparse_semantics=malformed,
            scope_artifact="scope.json",
            evidence_ledger="ledger.json",
        )


def test_reopen_requires_new_retained_applicable_authoritative_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "methodology.pdf"
    source.write_bytes(b"new official evidence")
    candidate = {
        "locally_retained": True,
        "official": True,
        "report_family_relevant": True,
        "applicability_status": "VALIDATED_2024_2025",
        "establishes_missing_links": ["B_report_completeness"],
        "local_path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }

    assert eligible_for_explicit_reconsideration(candidate) is True
    assert classify_frozen_absence(exact_isin_present=False) == "NO_EXACT_ISIN_OBSERVATION"


@pytest.mark.parametrize(
    "field,value",
    [
        ("official", False),
        ("locally_retained", False),
        ("report_family_relevant", False),
        ("applicability_status", "APPLICABILITY_NOT_VALIDATED"),
        ("establishes_missing_links", []),
    ],
)
def test_unofficial_or_inapplicable_material_cannot_reopen(
    tmp_path: Path, field: str, value: object
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"candidate")
    candidate: dict[str, object] = {
        "locally_retained": True,
        "official": True,
        "report_family_relevant": True,
        "applicability_status": "VALIDATED_2024_2025",
        "establishes_missing_links": ["B_report_completeness"],
        "local_path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    candidate[field] = value
    assert eligible_for_explicit_reconsideration(candidate) is False
