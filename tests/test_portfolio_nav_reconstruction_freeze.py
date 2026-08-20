from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.history.portfolio_nav_reconstruction_freeze import (
    FREEZE_STATUS,
    RECONSTRUCTION_NOT_APPROVED,
    PortfolioNavReconstructionFrozenError,
    ReopenEvidence,
    assert_portfolio_forward_label_generation_allowed,
    assert_reconstruction_allowed,
    build_portfolio_nav_reconstruction_freeze,
    qualifies_as_reopen_evidence,
)


def _write(path: Path, value: dict[str, object]) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _inputs(root: Path) -> dict[str, Path]:
    return {
        "methodology": _write(
            root / "methodology.json",
            {
                "validation_status": "PORTFOLIO_NAV_METHODOLOGY_BLOCKED",
                "activation_state": "NOT_ACTIVATED",
                "approval_blockers": ["FX_METHODOLOGY_REQUIRED", "REBALANCE_EFFECTIVE_TIMESTAMP_UNRESOLVED"],
                "evidence_fingerprint": "methodology-evidence",
            },
        ),
        "resolution": _write(
            root / "resolution.json",
            {"validation_status": "PORTFOLIO_NAV_METHODOLOGY_BLOCKERS_PARTIALLY_RESOLVED", "resolution_fingerprint": "resolution-evidence"},
        ),
        "duplicates": _write(root / "duplicates.json", {"resolution_fingerprint": "duplicate-evidence"}),
        "labels": _write(root / "labels.json", {"available_label_count": 0, "label_store_fingerprint": "labels-evidence"}),
        "features": _write(root / "features.json", {"dataset_fingerprint": "features-evidence"}),
    }


def _build(root: Path) -> dict[str, object]:
    paths = _inputs(root)
    return build_portfolio_nav_reconstruction_freeze(
        repository_root=root,
        methodology_path=paths["methodology"],
        blocker_resolution_path=paths["resolution"],
        duplicate_resolution_path=paths["duplicates"],
        label_store_path=paths["labels"],
        feature_dataset_path=paths["features"],
    )


def test_blocked_methodology_produces_reversible_deterministic_freeze(tmp_path: Path) -> None:
    first = _build(tmp_path)
    second = _build(tmp_path)

    assert first == second
    assert first["validation_status"] == FREEZE_STATUS
    assert first["research_closed"] is True
    assert first["reopen_allowed"] is True
    assert first["portfolio_nav_generation_allowed"] is False
    assert first["portfolio_forward_label_generation_allowed"] is False
    evidence = cast(list[dict[str, object]], first["evidence"])
    assert all(not Path(str(item["path"])).is_absolute() for item in evidence)


def test_semantic_evidence_change_changes_freeze_fingerprint(tmp_path: Path) -> None:
    before = _build(tmp_path)
    paths = _inputs(tmp_path)
    _write(
        paths["methodology"],
        {
            "validation_status": "PORTFOLIO_NAV_METHODOLOGY_BLOCKED",
            "activation_state": "NOT_ACTIVATED",
            "approval_blockers": ["FX_METHODOLOGY_REQUIRED"],
            "evidence_fingerprint": "changed-methodology-evidence",
        },
    )
    after = build_portfolio_nav_reconstruction_freeze(
        repository_root=tmp_path,
        methodology_path=paths["methodology"],
        blocker_resolution_path=paths["resolution"],
        duplicate_resolution_path=paths["duplicates"],
        label_store_path=paths["labels"],
        feature_dataset_path=paths["features"],
    )

    assert before["freeze_fingerprint"] != after["freeze_fingerprint"]


def test_only_new_authoritative_evidence_qualifies_for_reopen_review() -> None:
    valid = ReopenEvidence("OFFICIAL_PORTFOLIO_NAV_HISTORY", True, True, True, True, True, "new")
    duplicate = ReopenEvidence("OFFICIAL_PORTFOLIO_NAV_HISTORY", True, True, True, True, True, "existing")
    constituent_growth = ReopenEvidence("MORE_CONSTITUENT_NAV_OBSERVATIONS_ALONE", True, True, True, True, True, "new")

    assert qualifies_as_reopen_evidence(valid, existing_fingerprints=frozenset({"existing"})) is True
    assert qualifies_as_reopen_evidence(duplicate, existing_fingerprints=frozenset({"existing"})) is False
    assert qualifies_as_reopen_evidence(constituent_growth, existing_fingerprints=frozenset()) is False


def test_guard_blocks_only_synthetic_reconstruction_and_not_direct_official_reader(tmp_path: Path) -> None:
    freeze = _build(tmp_path)

    with pytest.raises(PortfolioNavReconstructionFrozenError, match=RECONSTRUCTION_NOT_APPROVED):
        assert_reconstruction_allowed(freeze, reconstruction_requested=True)
    with pytest.raises(PortfolioNavReconstructionFrozenError, match=RECONSTRUCTION_NOT_APPROVED):
        assert_portfolio_forward_label_generation_allowed(
            freeze,
            reconstructed_portfolio_source=True,
        )
    assert_reconstruction_allowed(
        freeze,
        reconstruction_requested=True,
        direct_official_portfolio_source=True,
    )
    assert_reconstruction_allowed(freeze, reconstruction_requested=False)
    assert_portfolio_forward_label_generation_allowed(
        freeze,
        reconstructed_portfolio_source=True,
        direct_official_portfolio_source=True,
    )
