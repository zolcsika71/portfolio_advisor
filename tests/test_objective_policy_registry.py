"""Behavioral coverage for deterministic objective and policy governance."""

from __future__ import annotations

import json
import socket
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.advisor.active_policy_validation import (
    build_active_policy_validation,
)
from portfolio_advisor.objectives import (
    CAPITAL_POLICY_ARTIFACT,
    CAPITAL_POLICY_ID,
    CAPITAL_POLICY_VERSION,
    ConflictingPolicyRegistrationError,
    DuplicatePolicyRegistrationError,
    InvalidInvestmentPolicyError,
    InvestmentPolicy,
    MultipleActivePoliciesError,
    NoValidatedActivePolicyError,
    PolicyActivationStatus,
    PolicyAvailability,
    PolicyCapabilities,
    PolicyCapabilityStatus,
    PolicyNotFoundError,
    PolicyRegistry,
    PolicyReviewStatus,
    PortfolioObjective,
    UnknownObjectiveError,
    build_default_policy_registry,
    render_registry_audit,
)
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.policy_contract import build_policy_contract

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / CAPITAL_POLICY_ARTIFACT
METHODOLOGY = ROOT / "data/audit/capital_preservation_metrics_ranking_validation.json"
STRICT = ROOT / "data/audit/strict_backtest_pipeline_validation.json"
CONTRACT = ROOT / "data/audit/capital_preservation_ranking_policy_contract.json"
MODEL_DATABASE = ROOT / "database/model_portfolio.sqlite"
RETAINED_DATABASES = (
    MODEL_DATABASE,
    ROOT / "database/official_historical_nav.sqlite",
    ROOT / "database/prospective_portfolio_validation.sqlite",
)


def _capabilities() -> PolicyCapabilities:
    return PolicyCapabilities(
        eligibility=PolicyCapabilityStatus.AVAILABLE_REVIEWED,
        ranking=PolicyCapabilityStatus.AVAILABLE_REVIEWED,
        construction=PolicyCapabilityStatus.AVAILABLE_REVIEWED,
        finalist_comparison=PolicyCapabilityStatus.AVAILABLE_REVIEWED,
        outcome_success_criteria=PolicyCapabilityStatus.NOT_IMPLEMENTED,
    )


def _policy(
    *,
    version: str = "1.0.1",
    review: PolicyReviewStatus = PolicyReviewStatus.APPROVED,
    activation: PolicyActivationStatus = PolicyActivationStatus.ACTIVE,
    mandate: str = "Reviewed mandate",
) -> InvestmentPolicy:
    return InvestmentPolicy(
        objective=PortfolioObjective.CAPITAL_CONSERVATION,
        policy_id=CAPITAL_POLICY_ID,
        version=version,
        schema_version=2,
        review_status=review,
        activation_status=activation,
        mandate=mandate,
        artifact_reference=CAPITAL_POLICY_ARTIFACT,
        fingerprint="a" * 64,
        capabilities=_capabilities(),
    )


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _active_ranking() -> dict[str, object]:
    return build_active_policy_validation(
        database_path=MODEL_DATABASE,
        rules_path=RULES,
        contract_path=CONTRACT,
        methodology_path=METHODOLOGY,
        strict_pipeline_path=STRICT,
    )


def test_portfolio_objective_values_and_exact_parsing_are_stable() -> None:
    assert [value.value for value in PortfolioObjective] == [
        "capital_conservation",
        "dividend_portfolio",
    ]
    assert PortfolioObjective.parse("capital_conservation") is PortfolioObjective.CAPITAL_CONSERVATION
    with pytest.raises(UnknownObjectiveError, match="Unknown portfolio objective"):
        PortfolioObjective.parse("CAPITAL_CONSERVATION")
    with pytest.raises(UnknownObjectiveError):
        PortfolioObjective.parse(" capital_conservation")


def test_default_registry_reuses_validated_capital_identity_contract_and_fingerprint() -> None:
    before = _file_hash(RULES)
    registry = build_default_policy_registry(ROOT)
    policy = registry.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION)
    rules = load_ranking_rules(RULES)
    contract = build_policy_contract(
        rules_path=RULES,
        methodology_path=METHODOLOGY,
        strict_pipeline_path=STRICT,
    )

    assert (policy.policy_id, policy.version, policy.schema_version) == (
        CAPITAL_POLICY_ID,
        CAPITAL_POLICY_VERSION,
        rules.schema_version,
    )
    assert policy.artifact_reference == CAPITAL_POLICY_ARTIFACT
    assert policy.fingerprint == before
    assert contract["final_policy_status"] == "RANKING_POLICY_ACTIVE"
    assert contract["policy_identity"]["name"] == policy.policy_id
    assert _file_hash(RULES) == before


def test_dividend_is_supported_but_has_no_policy_or_fallback() -> None:
    registry = build_default_policy_registry(ROOT)
    assert registry.supported_objectives == (
        PortfolioObjective.CAPITAL_CONSERVATION,
        PortfolioObjective.DIVIDEND_PORTFOLIO,
    )
    assert (
        registry.availability(PortfolioObjective.DIVIDEND_PORTFOLIO)
        is PolicyAvailability.NO_VALIDATED_ACTIVE_POLICY
    )
    with pytest.raises(NoValidatedActivePolicyError, match="NO_VALIDATED_ACTIVE_POLICY"):
        registry.resolve_active_policy(PortfolioObjective.DIVIDEND_PORTFOLIO)
    with pytest.raises(PolicyNotFoundError):
        registry.exact_policy(
            PortfolioObjective.DIVIDEND_PORTFOLIO,
            CAPITAL_POLICY_ID,
            CAPITAL_POLICY_VERSION,
        )
    with pytest.raises(UnknownObjectiveError):
        registry.availability("income")


def test_duplicate_and_conflicting_registrations_never_replace() -> None:
    policy = _policy()
    registry = PolicyRegistry(policies=(policy,))
    with pytest.raises(DuplicatePolicyRegistrationError):
        registry.register(policy)
    with pytest.raises(ConflictingPolicyRegistrationError):
        registry.register(replace(policy, mandate="Different reviewed metadata"))
    assert registry.exact_policy(*policy.identity) == policy


def test_versions_remain_distinct_and_active_ambiguity_fails_closed() -> None:
    version_101 = _policy(version="1.0.1")
    version_102 = _policy(version="1.0.2", activation=PolicyActivationStatus.NOT_ACTIVATED)
    registry = PolicyRegistry(policies=(version_102, version_101))
    assert registry.exact_policy(
        PortfolioObjective.CAPITAL_CONSERVATION, CAPITAL_POLICY_ID, "1.0.1"
    ) is version_101
    assert registry.exact_policy(
        PortfolioObjective.CAPITAL_CONSERVATION, CAPITAL_POLICY_ID, "1.0.2"
    ) is version_102
    assert registry.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION) is version_101
    with pytest.raises(PolicyNotFoundError):
        registry.exact_policy(
            PortfolioObjective.CAPITAL_CONSERVATION, CAPITAL_POLICY_ID, "1.0.3"
        )

    ambiguous = PolicyRegistry(policies=(version_101, replace(version_102, activation_status=PolicyActivationStatus.ACTIVE)))
    with pytest.raises(MultipleActivePoliciesError):
        ambiguous.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION)


def test_inactive_and_unreviewed_policies_do_not_activate_implicitly() -> None:
    inactive = _policy(activation=PolicyActivationStatus.NOT_ACTIVATED)
    registry = PolicyRegistry(policies=(inactive,))
    with pytest.raises(NoValidatedActivePolicyError):
        registry.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION)

    unreviewed = replace(
        inactive,
        review_status=PolicyReviewStatus.UNREVIEWED,
        version="0.1.0",
    )
    unreviewed_registry = PolicyRegistry(policies=(unreviewed,))
    with pytest.raises(NoValidatedActivePolicyError):
        unreviewed_registry.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION)
    with pytest.raises(InvalidInvestmentPolicyError, match="only an approved policy"):
        replace(unreviewed, activation_status=PolicyActivationStatus.ACTIVE)


def test_registry_serialization_and_fingerprint_ignore_registration_order() -> None:
    capital = _policy()
    inactive = replace(capital, version="1.0.0", activation_status=PolicyActivationStatus.NOT_ACTIVATED)
    first = PolicyRegistry(policies=(capital, inactive))
    second = PolicyRegistry(policies=(inactive, capital))
    assert first.policies == second.policies
    assert first.to_audit_dict() == second.to_audit_dict()
    assert first.registry_fingerprint() == second.registry_fingerprint()
    assert json.dumps(first.to_audit_dict(), sort_keys=True) == json.dumps(
        second.to_audit_dict(), sort_keys=True
    )


def test_registry_audit_is_deterministic_private_free_and_objective_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked_network)
    registry = build_default_policy_registry(ROOT)
    first = render_registry_audit(registry)
    second = render_registry_audit(build_default_policy_registry(ROOT))
    payload = json.loads(first)

    assert first == second
    assert str(ROOT) not in first
    assert "selected_portfolio" not in first
    assert "portfolio_holding" not in first
    assert "account_identifier" not in first
    assert payload["database_boundary"] == {
        "objective_specific_schema_created": False,
        "status": "OBJECTIVE_NEUTRAL",
    }
    assert payload["production_cutover"] == "NOT_AUTHORIZED"
    assert len(payload["registry_fingerprint"]) == 64


def test_capabilities_report_reviewed_construction_without_overclaiming_later_work() -> None:
    registry = build_default_policy_registry(ROOT)
    capital = registry.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION)
    assert capital.capabilities.eligibility is PolicyCapabilityStatus.AVAILABLE_REVIEWED
    assert capital.capabilities.ranking is PolicyCapabilityStatus.AVAILABLE_REVIEWED
    assert capital.capabilities.construction is PolicyCapabilityStatus.AVAILABLE_REVIEWED
    assert (
        capital.capabilities.finalist_comparison
        is PolicyCapabilityStatus.AVAILABLE_REVIEWED
    )
    assert capital.capabilities.outcome_success_criteria is PolicyCapabilityStatus.NOT_IMPLEMENTED
    objectives = cast(list[dict[str, object]], registry.to_audit_dict()["objectives"])
    dividend_capabilities = cast(dict[str, str], objectives[1]["capabilities"])
    assert set(dividend_capabilities.values()) == {
        PolicyCapabilityStatus.NO_VALIDATED_ACTIVE_POLICY.value
    }


def test_registry_construction_does_not_mutate_retained_databases_or_create_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = {path: _file_hash(path) for path in RETAINED_DATABASES}
    monkeypatch.chdir(tmp_path)
    build_default_policy_registry(ROOT)
    assert {path: _file_hash(path) for path in RETAINED_DATABASES} == before
    assert list(tmp_path.iterdir()) == []


def test_historical_policy_references_and_capital_ranking_remain_unchanged() -> None:
    prospective_before = _file_hash(ROOT / "database/prospective_portfolio_validation.sqlite")
    before = _active_ranking()
    policy = build_default_policy_registry(ROOT).resolve_active_policy(
        PortfolioObjective.CAPITAL_CONSERVATION
    )
    after = _active_ranking()

    assert policy.policy_id == CAPITAL_POLICY_ID
    assert policy.version == CAPITAL_POLICY_VERSION
    assert before == after
    current_before = cast(dict[str, object], before["current_universe"])
    current_after = cast(dict[str, object], after["current_universe"])
    assert current_before["selected_portfolio"] == "PB Konzervatív MultiCCY"
    assert current_before["ranking"] == current_after["ranking"]
    assert _file_hash(ROOT / "database/prospective_portfolio_validation.sqlite") == prospective_before
