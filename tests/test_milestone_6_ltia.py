"""Sanitized tests for Milestone 6 LTIA reconciliation primitives."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.tbsz.ltia_reconciliation import (
    IdentityResolver,
    LtiaIdentityStatus,
    LtiaPosition,
    apply_confirmation_store,
    classify_equivalence,
    project_current_positions,
    validate_manual_confirmation,
)


def test_identity_hierarchy_and_no_fuzzy_promotion() -> None:
    resolver = IdentityResolver({"Fund A": {"US0378331005"}, "Fund B": {"US5949181045", "IE00B7KFL990"}}, {"Manual": "US0378331005"})
    assert resolver.resolve("anything", "US0378331005").status is LtiaIdentityStatus.CONFIRMED_EXPLICIT_ISIN
    assert resolver.resolve("Manual").status is LtiaIdentityStatus.CONFIRMED_MANUAL_ALIAS
    assert resolver.resolve("Fund A").status is LtiaIdentityStatus.CONFIRMED_UNIQUE_EXACT_NAME
    assert resolver.resolve("Fund B").status is LtiaIdentityStatus.AMBIGUOUS_IDENTITY
    assert resolver.resolve("Fnd A", fuzzy_candidates=("US0378331005",)).status is LtiaIdentityStatus.IDENTITY_CANDIDATE
    assert resolver.resolve("bad", "INVALID").status is LtiaIdentityStatus.CONFLICTING_IDENTITY


def test_manual_confirmation_is_dry_run_idempotent_and_rejects_conflict() -> None:
    canonical = {"US0378331005", "US5949181045"}
    assert validate_manual_confirmation(source_name="Fund", selected_isin="US0378331005", canonical_isins=canonical, existing={})["status"] == "DRY_RUN_VALID"
    assert validate_manual_confirmation(source_name="Fund", selected_isin="US0378331005", canonical_isins=canonical, existing={"fund": "US0378331005"})["idempotent"]
    with pytest.raises(ValueError, match="contradictory"):
        validate_manual_confirmation(source_name="Fund", selected_isin="US5949181045", canonical_isins=canonical, existing={"fund": "US0378331005"})


def test_equivalence_and_projection_preserve_lineage_without_fx() -> None:
    assert classify_equivalence({"content_sha256": "a"}, {"content_sha256": "a"}) == "BYTE_IDENTICAL_SOURCE"
    one = LtiaPosition("A", (1,), date(2025, 1, 1), "Fund", "HU0000000001", LtiaIdentityStatus.CONFIRMED_EXPLICIT_ISIN, None, None, Decimal(10), "HUF", None)
    two = LtiaPosition("B", (2,), date(2025, 1, 1), "Fund", "HU0000000001", LtiaIdentityStatus.CONFIRMED_EXPLICIT_ISIN, None, None, Decimal(20), "HUF", None)
    unresolved = LtiaPosition("C", (3,), None, "Unknown", None, LtiaIdentityStatus.UNRESOLVED_IDENTITY, None, None, Decimal(5), "EUR", None)
    account, consolidated = project_current_positions((one, two, unresolved), precedence_proven=True)
    assert len(account) == 3 and consolidated[0]["market_value"] == Decimal(30) and consolidated[0]["source_snapshot_ids"] == (1, 2)
    assert consolidated[-1]["status"] == "UNRESOLVED_IDENTITY_NOT_AGGREGATED"
    assert project_current_positions((one,), precedence_proven=False)[1][0]["status"] == "UNRESOLVED_CURRENT_STATE_PRECEDENCE"


def test_confirmation_store_is_atomic_backed_up_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "confirmations.json"
    assert apply_confirmation_store(path, {"Fund": "US0378331005"}, apply=False)["status"] == "DRY_RUN"
    assert apply_confirmation_store(path, {"Fund": "US0378331005"}, apply=True)["status"] == "APPLIED"
    assert apply_confirmation_store(path, {"Fund": "US0378331005"}, apply=True)["status"] == "IDEMPOTENT_NO_CHANGE"
    assert apply_confirmation_store(path, {"Other": "US5949181045"}, apply=True)["status"] == "APPLIED"
    assert path.with_name("confirmations.json.backup").is_file()


def test_manual_confirmation_rejects_ambiguous_or_conflicting_approval() -> None:
    resolver = IdentityResolver({"Fund": {"US0378331005", "US5949181045"}})
    assert resolver.resolve("Fund").status is LtiaIdentityStatus.AMBIGUOUS_IDENTITY
    with pytest.raises(ValueError, match="contradictory"):
        validate_manual_confirmation(
            source_name="Fund",
            selected_isin="US0378331005",
            canonical_isins={"US0378331005"},
            existing={"fund": "US5949181045"},
        )
