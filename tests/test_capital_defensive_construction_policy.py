"""Milestone 11A tests for the reviewed Capital Defensive policy contract."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from portfolio_advisor.construction import construct_capital_conservation_shortlist
from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ID,
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_VERSION,
    CAPITAL_POLICY_ARTIFACT,
    ConflictingPolicyRegistrationError,
    ConstructionPolicyValidationError,
    DuplicatePolicyRegistrationError,
    NoValidatedActivePolicyError,
    PolicyCapabilityStatus,
    PolicyRegistry,
    PortfolioObjective,
    build_default_policy_registry,
    load_capital_defensive_construction_policy,
    render_construction_policy_audit,
    validate_construction_cash_input,
)
from portfolio_advisor.workflows import build_capital_conservation_reference_workflow

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
RANKING = ROOT / CAPITAL_POLICY_ARTIFACT
RANKING_FINGERPRINT = "d3cc192857459963eab539d93457396756b341ad8941e6c0832cedf7450091ba"
HISTORICAL_REGISTRY_FINGERPRINT = (
    "409cf497029938e6f754c7dd51993b794cabc394f92bc7d1dd99ca1ce2c5c55d"
)
MILESTONE_11A_REGISTRY_FINGERPRINT = (
    "ddc2fc0d45a8e2f9788a6e36589c3956cf30d2347b081b22b4a66465ed244d57"
)


def _policy_file(tmp_path: Path, mutate) -> Path:  # type: ignore[no-untyped-def]
    value = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    mutate(value)
    path = tmp_path / "construction.yaml"
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_valid_policy_load_canonical_fingerprint_and_exact_approved_values() -> None:
    policy = load_capital_defensive_construction_policy(ARTIFACT)
    assert (policy.policy_id, policy.version, policy.schema_version) == (
        CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ID,
        CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_VERSION,
        1,
    )
    assert (policy.objective, policy.strategy, policy.status) == (
        "CAPITAL_CONSERVATION",
        "CAPITAL_DEFENSIVE",
        "APPROVED",
    )
    assert policy.runtime_construction_readiness == "NOT_IMPLEMENTED"
    assert policy.supported_currencies == ("EUR", "USD", "HUF")
    assert policy.fingerprint == load_capital_defensive_construction_policy(ARTIFACT).fingerprint
    payload = policy.artifact_payload()
    assert payload["allocation"] == {
        "brokerage_rounding": "OUTSIDE_SCOPE",
        "cash_reserve_weight": "0.20",
        "order_quantities": "OUTSIDE_SCOPE",
        "ranking_feature_weights_are_portfolio_weights": False,
        "security_count": 8,
        "total_cash_weight": "0.20",
        "total_security_weight": "0.80",
        "transaction_units": "OUTSIDE_SCOPE",
        "weight_per_security": "0.10",
        "weights_are_governed_allocation_contract": True,
    }
    assert json.loads(policy.canonical_json()) == payload


def test_canonical_fingerprint_ignores_yaml_key_order(tmp_path: Path) -> None:
    value = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    reordered = {key: value[key] for key in reversed(tuple(value))}
    path = tmp_path / "reordered.yaml"
    path.write_text(yaml.safe_dump(reordered, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert load_capital_defensive_construction_policy(path).fingerprint == (
        load_capital_defensive_construction_policy(ARTIFACT).fingerprint
    )


def test_unknown_field_and_invalid_schema_version_are_rejected(tmp_path: Path) -> None:
    unknown = _policy_file(tmp_path, lambda value: value.update({"fallback": "PROHIBITED"}))
    with pytest.raises(ConstructionPolicyValidationError, match="fields differ"):
        load_capital_defensive_construction_policy(unknown)
    invalid = _policy_file(tmp_path, lambda value: value.update({"schema_version": 2}))
    with pytest.raises(ConstructionPolicyValidationError, match="schema_version"):
        load_capital_defensive_construction_policy(invalid)


@pytest.mark.parametrize("field", ["objective", "strategy"])
def test_unsupported_objective_or_strategy_is_rejected(tmp_path: Path, field: str) -> None:
    path = _policy_file(tmp_path, lambda value: value.update({field: "UNAPPROVED"}))
    with pytest.raises(ConstructionPolicyValidationError, match=field):
        load_capital_defensive_construction_policy(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("cash_reserve_weight", "0.19"), ("security_count", 7)],
)
def test_invalid_cash_reserve_or_holding_count_is_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    path = _policy_file(tmp_path, lambda data: data["allocation"].update({field: value}))
    with pytest.raises(ConstructionPolicyValidationError, match=field):
        load_capital_defensive_construction_policy(path)


def test_weights_must_reconcile_to_one_hundred_percent(tmp_path: Path) -> None:
    def mutate(data: dict[str, object]) -> None:
        allocation = data["allocation"]
        assert isinstance(allocation, dict)
        allocation["total_security_weight"] = "0.79"

    path = _policy_file(tmp_path, mutate)
    with pytest.raises(ConstructionPolicyValidationError, match="total_security_weight|reconcile"):
        load_capital_defensive_construction_policy(path)


def test_cash_input_requires_one_supported_positive_exact_decimal() -> None:
    policy = load_capital_defensive_construction_policy(ARTIFACT)
    assert validate_construction_cash_input(policy, {"EUR": Decimal("1000.01")}) == (
        "EUR",
        Decimal("1000.01"),
    )
    with pytest.raises(ConstructionPolicyValidationError, match="exactly one"):
        validate_construction_cash_input(
            policy, {"EUR": Decimal(1), "USD": Decimal(1)}
        )
    with pytest.raises(ConstructionPolicyValidationError, match="unsupported"):
        validate_construction_cash_input(policy, {"GBP": Decimal(1)})
    with pytest.raises(ConstructionPolicyValidationError, match="Decimal"):
        validate_construction_cash_input(policy, {"EUR": 1.0})  # type: ignore[dict-item]
    for invalid in (Decimal(0), Decimal(-1), Decimal("NaN")):
        with pytest.raises(ConstructionPolicyValidationError, match="positive"):
            validate_construction_cash_input(policy, {"EUR": invalid})


@pytest.mark.parametrize(
    ("field", "value"),
    [("minimum_distinct_conflict_free_groups", 9), ("maximum_group_weight", "0.50")],
)
def test_invalid_diversification_limits_are_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    path = _policy_file(tmp_path, lambda data: data["diversification"].update({field: value}))
    with pytest.raises(ConstructionPolicyValidationError):
        load_capital_defensive_construction_policy(path)


def test_missing_nav_field_and_invalid_staleness_history_relationship_are_rejected(
    tmp_path: Path,
) -> None:
    missing = _policy_file(
        tmp_path,
        lambda data: data["historical_nav"].pop("minimum_aligned_return_intervals"),
    )
    with pytest.raises(ConstructionPolicyValidationError, match="missing"):
        load_capital_defensive_construction_policy(missing)
    stale = _policy_file(
        tmp_path,
        lambda data: data["historical_nav"].update(
            {"maximum_observation_staleness_calendar_days": 365}
        ),
    )
    with pytest.raises(ConstructionPolicyValidationError, match="staleness/history"):
        load_capital_defensive_construction_policy(stale)


def test_unapproved_reference_source_and_missing_provenance_are_rejected(tmp_path: Path) -> None:
    bad_source = _policy_file(
        tmp_path,
        lambda data: data["reference_rates"]["EUR"].update(
            {"official_source_url": "https://example.invalid/estr"}
        ),
    )
    with pytest.raises(ConstructionPolicyValidationError, match="unapproved reference-rate"):
        load_capital_defensive_construction_policy(bad_source)
    missing = _policy_file(
        tmp_path,
        lambda data: data["reference_rate_methodology"].pop("preserved_fields"),
    )
    with pytest.raises(ConstructionPolicyValidationError, match="missing"):
        load_capital_defensive_construction_policy(missing)


def test_duplicate_and_conflicting_construction_policy_versions_are_rejected() -> None:
    policy = load_capital_defensive_construction_policy(ARTIFACT)
    registry = PolicyRegistry(construction_policies=(policy,))
    with pytest.raises(DuplicatePolicyRegistrationError):
        registry.register_construction_policy(policy)
    with pytest.raises(ConflictingPolicyRegistrationError):
        registry.register_construction_policy(
            replace(policy, artifact_reference="reviewed/conflicting.yaml")
        )
    prospective = replace(
        policy,
        version="1.1.0",
        artifact_reference="reviewed/capital-defensive-v1.1.0.yaml",
    )
    registry.register_construction_policy(prospective)
    assert registry.exact_construction_policy(
        PortfolioObjective.CAPITAL_CONSERVATION,
        CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ID,
        "1.1.0",
    ) is prospective


def test_registry_fingerprints_are_deterministic_and_historical_v1_is_reproducible() -> None:
    first = build_default_policy_registry(ROOT)
    second = build_default_policy_registry(ROOT)
    assert first.registry_fingerprint() == second.registry_fingerprint()
    assert first.registry_fingerprint(schema_version=1) == HISTORICAL_REGISTRY_FINGERPRINT
    assert first.registry_fingerprint(schema_version=2) == MILESTONE_11A_REGISTRY_FINGERPRINT
    assert first.registry_fingerprint() != HISTORICAL_REGISTRY_FINGERPRINT
    assert first.registry_fingerprint() != MILESTONE_11A_REGISTRY_FINGERPRINT
    assert sha256(RANKING.read_bytes()).hexdigest() == RANKING_FINGERPRINT


def test_capability_corrections_and_dividend_unavailability() -> None:
    registry = build_default_policy_registry(ROOT)
    capital = registry.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION)
    assert capital.capabilities.eligibility is PolicyCapabilityStatus.AVAILABLE_REVIEWED
    assert (
        capital.capabilities.instrument_screening_ranking
        is PolicyCapabilityStatus.AVAILABLE_REVIEWED
    )
    assert capital.capabilities.construction_policy is PolicyCapabilityStatus.AVAILABLE_REVIEWED
    assert (
        capital.capabilities.constructed_portfolio_runtime
        is PolicyCapabilityStatus.IMPLEMENTED_BLOCKED_BY_DATA
    )
    assert capital.capabilities.finalist_comparison is PolicyCapabilityStatus.NOT_IMPLEMENTED
    assert capital.capabilities.outcome_success_criteria is PolicyCapabilityStatus.NOT_IMPLEMENTED
    with pytest.raises(NoValidatedActivePolicyError):
        registry.resolve_active_policy(PortfolioObjective.DIVIDEND_PORTFOLIO)
    dividend = registry.to_audit_dict()["objectives"][1]  # type: ignore[index]
    assert set(dividend["capabilities"].values()) == {"NO_VALIDATED_ACTIVE_POLICY"}  # type: ignore[index,union-attr]


def test_intermediate_apis_remain_import_compatible_but_are_not_capabilities() -> None:
    assert callable(construct_capital_conservation_shortlist)
    assert callable(build_capital_conservation_reference_workflow)
    capital = build_default_policy_registry(ROOT).resolve_active_policy(
        PortfolioObjective.CAPITAL_CONSERVATION
    )
    assert (
        capital.capabilities.constructed_portfolio_runtime.value
        == "IMPLEMENTED_BLOCKED_BY_DATA"
    )
    assert capital.capabilities.finalist_comparison.value == "NOT_IMPLEMENTED"


def test_policy_audit_is_byte_stable_and_explicitly_no_go() -> None:
    policy = load_capital_defensive_construction_policy(ARTIFACT)
    first = render_construction_policy_audit(policy, build_default_policy_registry(ROOT))
    second = render_construction_policy_audit(policy, build_default_policy_registry(ROOT))
    assert first == second
    payload = json.loads(first)
    assert payload["construction_output"] == {
        "can_produce_constructed_portfolio": False,
        "statement": "NO_CONSTRUCTED_PORTFOLIO_CAN_YET_BE_PRODUCED",
    }
    assert payload["runtime_dependencies"] == {
        "current_nav": "MISSING",
        "official_reference_rates": "MISSING",
        "portfolio_construction": "NOT_IMPLEMENTED",
        "portfolio_metrics": "NOT_IMPLEMENTED",
        "portfolio_persistence": "NOT_IMPLEMENTED",
        "schema": "MISSING",
    }
