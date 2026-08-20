from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from portfolio_advisor.history.backtest_resolvability import (
    DEFAULT_ARTIFACT_REFERENCES,
    RESOLUTION_STATUS,
    BacktestResolvabilityError,
    build_resolution,
    reopen_eligibility,
)


def evidence() -> dict[str, dict[str, Any]]:
    return {
        name: json.loads(Path(path).read_text(encoding="utf-8"))
        for name, path in DEFAULT_ARTIFACT_REFERENCES.items()
    }


def build(values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return build_resolution(
        erste=values["erste_diagnostics"],
        mnb_coverage=values["mnb_otc_coverage"],
        lifecycle=values["lifecycle"],
        redemption=values["redemption_methodology"],
        sparse=values["sparse_trading_semantics"],
        report_scope=values["report_scope_semantics"],
        scope_ledger=values["scope_research_ledger"],
        absence_freeze=values["absence_semantics_freeze"],
        price_evidence=values["price_semantics_evidence"],
        price_audit=values["price_semantics_audit"],
        alternative_research=values["alternative_source_research"],
        alternative_audit=values["alternative_source_audit"],
        coverage=values["backtest_window_coverage"],
        artifact_references=DEFAULT_ARTIFACT_REFERENCES,
    )


def test_terminal_resolution_is_deterministic_and_preserves_admission() -> None:
    first = build(evidence())
    second = build(evidence())

    assert first["resolution_status"] == RESOLUTION_STATUS
    assert first["evidence_scope"] == "CURRENT_VALIDATED_PUBLIC_EVIDENCE"
    assert first["research_closed"] is True
    assert first["reopen_allowed"] is True
    assert first["required_interval"] == {"start": "2024-07-02", "end": "2025-06-04"}
    assert first["evidence_fingerprint"] == second["evidence_fingerprint"]
    assert first["backtest_admission"] == {
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
    }
    assert first["affected_windows"]["affected_window_count"] == 132
    assert first["affected_windows"]["newly_complete_windows"] == 0


@pytest.mark.parametrize(
    "key,mutator",
    [
        ("lifecycle", lambda value: value.update({"isin": "HU0000554794"})),
        ("redemption_methodology", lambda value: value.update({"currency": "EUR"})),
        ("absence_semantics_freeze", lambda value: value.update({"absence_semantics_validated": True})),
        ("price_semantics_audit", lambda value: value.update({"price_semantics_status": "MNB_OTC_PRICE_SEMANTICS_VALIDATED"})),
        ("alternative_source_research", lambda value: value.update({"research_outcome": "ALTERNATIVE_PRICE_SOURCE_FOUND"})),
    ],
)
def test_inconsistent_mandatory_evidence_fails_closed(
    key: str, mutator: Callable[[dict[str, Any]], None]
) -> None:
    values = copy.deepcopy(evidence())
    mutator(values[key])
    with pytest.raises(BacktestResolvabilityError):
        build(values)


def test_changed_source_hash_or_terminal_result_changes_fingerprint() -> None:
    baseline = build(evidence())
    values = copy.deepcopy(evidence())
    lifecycle = values["lifecycle"]
    lifecycle["source_document_sha256"] = "0" * 64
    changed = build(values)
    assert changed["evidence_fingerprint"] != baseline["evidence_fingerprint"]


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("AUTHORITATIVE_EXACT_ISIN_HISTORICAL_SERIES", "REOPEN_ELIGIBLE"),
        ("AUTHENTICATED_AUTHORITATIVE_TREASURY_EXPORT", "REOPEN_ELIGIBLE"),
        ("APPLICABLE_AUTHORITATIVE_METHODOLOGY", "REOPEN_ELIGIBLE"),
        ("SEARCH_SNIPPET", "NOT_REOPEN_ELIGIBLE"),
    ],
)
def test_reopen_requires_new_authoritative_retained_applicable_evidence(
    kind: str, expected: str
) -> None:
    candidate = {
        "kind": kind,
        "locally_retained": True,
        "hash_verified": True,
        "authoritative": True,
        "applicable_2024_2025": True,
    }
    assert reopen_eligibility(candidate) == expected


def test_reopen_eligibility_does_not_change_terminal_admission() -> None:
    artifact = build(evidence())
    assert reopen_eligibility(
        {
            "kind": "AUTHORITATIVE_EXACT_ISIN_HISTORICAL_SERIES",
            "locally_retained": True,
            "hash_verified": True,
            "authoritative": True,
            "applicable_2024_2025": True,
        }
    ) == "REOPEN_ELIGIBLE"
    assert artifact["backtest_admission"]["usable_for_backtest"] is False
    assert artifact["portfolio_policy"] == "NOT_DECIDED_IN_THIS_TASK"
