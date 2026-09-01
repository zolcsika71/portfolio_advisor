"""Deterministic, fail-closed objective-policy registry."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.models import RankingRules
from portfolio_advisor.ranking.policy_contract import build_policy_contract

from .construction_policy import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    CapitalDefensiveConstructionPolicy,
    load_capital_defensive_construction_policy,
)
from .models import (
    InvestmentPolicy,
    ObjectiveFrameworkError,
    PolicyActivationStatus,
    PolicyAvailability,
    PolicyCapabilities,
    PolicyCapabilityStatus,
    PolicyReviewStatus,
    PortfolioObjective,
    UnknownObjectiveError,
)

REGISTRY_SCHEMA_VERSION = 3
HISTORICAL_REGISTRY_SCHEMA_VERSION = 1
MILESTONE_11A_REGISTRY_SCHEMA_VERSION = 2
CAPITAL_POLICY_ARTIFACT = "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
CAPITAL_POLICY_ID = "CAPITAL_PRESERVATION_RANKING_POLICY"
CAPITAL_POLICY_VERSION = "1.0.1"
CAPITAL_POLICY_MANDATE = (
    "3–12-month horizon; capital conservation first; risk-adjusted return second."
)
CAPITAL_METHODOLOGY_ARTIFACT = "data/audit/capital_preservation_metrics_ranking_validation.json"
CAPITAL_STRICT_PIPELINE_ARTIFACT = "data/audit/strict_backtest_pipeline_validation.json"


class PolicyRegistrationError(ObjectiveFrameworkError):
    """Base class for rejected policy registrations."""


class DuplicatePolicyRegistrationError(PolicyRegistrationError):
    """Raised when an identical policy identity is registered twice."""


class ConflictingPolicyRegistrationError(PolicyRegistrationError):
    """Raised when one policy identity is registered with different metadata."""


class PolicyNotFoundError(ObjectiveFrameworkError):
    """Raised when an exact policy identity/version is absent."""


class NoValidatedActivePolicyError(ObjectiveFrameworkError):
    """Raised when a supported objective has no validated active policy."""

    availability = PolicyAvailability.NO_VALIDATED_ACTIVE_POLICY


class MultipleActivePoliciesError(ObjectiveFrameworkError):
    """Raised when active-policy resolution would be ambiguous."""


class CapitalPolicyValidationError(ObjectiveFrameworkError):
    """Raised when retained policy artifacts do not prove the champion identity."""


class PolicyRegistry:
    """In-memory deterministic registry with no objective fallback."""

    def __init__(
        self,
        *,
        policies: Iterable[InvestmentPolicy] = (),
        construction_policies: Iterable[CapitalDefensiveConstructionPolicy] = (),
    ) -> None:
        self._supported = tuple(sorted(PortfolioObjective, key=lambda objective: objective.value))
        self._policies: dict[tuple[PortfolioObjective, str, str], InvestmentPolicy] = {}
        for ranking_policy in policies:
            self.register(ranking_policy)
        self._construction_policies: dict[
            tuple[PortfolioObjective, str, str], CapitalDefensiveConstructionPolicy
        ] = {}
        for construction_policy in construction_policies:
            self.register_construction_policy(construction_policy)

    @property
    def supported_objectives(self) -> tuple[PortfolioObjective, ...]:
        """Return supported objectives in stable value order."""
        return self._supported

    @property
    def policies(self) -> tuple[InvestmentPolicy, ...]:
        """Return all registered identities in deterministic order."""
        return tuple(
            self._policies[key]
            for key in sorted(
                self._policies,
                key=lambda item: (item[0].value, item[1], item[2]),
            )
        )

    def register(self, policy: InvestmentPolicy) -> None:
        """Register once; never replace or coalesce an existing identity."""
        if policy.objective not in self._supported:
            raise UnknownObjectiveError(
                f"Policy objective is not supported: {policy.objective.value}"
            )
        existing = self._policies.get(policy.identity)
        if existing is not None:
            if existing == policy:
                raise DuplicatePolicyRegistrationError(
                    f"Policy already registered: {policy.policy_id} v{policy.version}"
                )
            raise ConflictingPolicyRegistrationError(
                f"Conflicting registration: {policy.policy_id} v{policy.version}"
            )
        self._policies[policy.identity] = policy

    @property
    def construction_policies(self) -> tuple[CapitalDefensiveConstructionPolicy, ...]:
        """Return reviewed construction contracts in deterministic identity order."""
        return tuple(
            self._construction_policies[key]
            for key in sorted(
                self._construction_policies,
                key=lambda item: (item[0].value, item[1], item[2]),
            )
        )

    def register_construction_policy(
        self, policy: CapitalDefensiveConstructionPolicy
    ) -> None:
        """Register a construction policy version without replacing prior evidence."""
        existing = self._construction_policies.get(policy.identity)
        if existing is not None:
            if existing == policy:
                raise DuplicatePolicyRegistrationError(
                    f"Construction policy already registered: {policy.policy_id} v{policy.version}"
                )
            raise ConflictingPolicyRegistrationError(
                f"Conflicting construction policy: {policy.policy_id} v{policy.version}"
            )
        self._construction_policies[policy.identity] = policy

    def exact_construction_policy(
        self,
        objective: PortfolioObjective | str,
        policy_id: str,
        version: str,
    ) -> CapitalDefensiveConstructionPolicy:
        """Resolve an exact construction-policy identity with no version fallback."""
        parsed = self._require_supported(objective)
        try:
            return self._construction_policies[(parsed, policy_id, version)]
        except KeyError as error:
            raise PolicyNotFoundError(
                f"Construction policy is not registered for {parsed.value}: "
                f"{policy_id} v{version}"
            ) from error

    def exact_policy(
        self,
        objective: PortfolioObjective | str,
        policy_id: str,
        version: str,
    ) -> InvestmentPolicy:
        """Resolve an exact identity without aliases, fallback, or version substitution."""
        parsed = self._require_supported(objective)
        try:
            return self._policies[(parsed, policy_id, version)]
        except KeyError as error:
            raise PolicyNotFoundError(
                f"Policy is not registered for {parsed.value}: {policy_id} v{version}"
            ) from error

    def resolve_active_policy(self, objective: PortfolioObjective | str) -> InvestmentPolicy:
        """Resolve the sole approved active policy, or fail with a typed reason."""
        parsed = self._require_supported(objective)
        active = [
            policy
            for policy in self.policies
            if policy.objective is parsed
            and policy.review_status is PolicyReviewStatus.APPROVED
            and policy.activation_status is PolicyActivationStatus.ACTIVE
        ]
        if not active:
            raise NoValidatedActivePolicyError(
                f"{parsed.value}: {PolicyAvailability.NO_VALIDATED_ACTIVE_POLICY.value}"
            )
        if len(active) != 1:
            raise MultipleActivePoliciesError(
                f"{parsed.value} has {len(active)} validated active policies"
            )
        return active[0]

    def availability(self, objective: PortfolioObjective | str) -> PolicyAvailability:
        """Report active-policy availability without conflating objective support."""
        parsed = self._require_supported(objective)
        try:
            self.resolve_active_policy(parsed)
        except NoValidatedActivePolicyError:
            return PolicyAvailability.NO_VALIDATED_ACTIVE_POLICY
        return PolicyAvailability.VALIDATED_ACTIVE_POLICY

    def registry_fingerprint(self, *, schema_version: int = REGISTRY_SCHEMA_VERSION) -> str:
        """Fingerprint canonical, path-neutral policy registry content."""
        return canonical_fingerprint(self._registry_payload(schema_version))

    def to_audit_dict(self) -> dict[str, object]:
        """Return deterministic public audit data with explicit unavailable states."""
        inventory: list[dict[str, object]] = []
        for objective in self.supported_objectives:
            availability = self.availability(objective)
            if availability is PolicyAvailability.VALIDATED_ACTIVE_POLICY:
                active_policy = self.resolve_active_policy(objective)
                policy: dict[str, object] | None = active_policy.to_dict()
                capabilities = active_policy.capabilities.to_dict()
                construction_policy = next(
                    (
                        item.registry_dict()
                        for item in self.construction_policies
                        if item.identity[0] is objective
                    ),
                    None,
                )
            else:
                policy = None
                construction_policy = None
                capabilities = {
                    name: PolicyCapabilityStatus.NO_VALIDATED_ACTIVE_POLICY.value
                    for name in (
                        "constructed_portfolio_runtime",
                        "construction_policy",
                        "eligibility",
                        "finalist_comparison",
                        "instrument_screening_ranking",
                        "outcome_success_criteria",
                    )
                }
            inventory.append(
                {
                    "active_policy": policy,
                    "availability": availability.value,
                    "capabilities": capabilities,
                    "construction_policy": construction_policy,
                    "objective": objective.value,
                }
            )
        return {
            "audit_schema_version": REGISTRY_SCHEMA_VERSION,
            "database_boundary": {
                "objective_specific_schema_created": False,
                "status": "OBJECTIVE_NEUTRAL",
            },
            "objectives": inventory,
            "production_cutover": "NOT_AUTHORIZED",
            "registry_fingerprint": self.registry_fingerprint(),
            "historical_registry_fingerprints": {
                str(HISTORICAL_REGISTRY_SCHEMA_VERSION): self.registry_fingerprint(
                    schema_version=HISTORICAL_REGISTRY_SCHEMA_VERSION
                ),
                str(MILESTONE_11A_REGISTRY_SCHEMA_VERSION): self.registry_fingerprint(
                    schema_version=MILESTONE_11A_REGISTRY_SCHEMA_VERSION
                ),
            },
            "supported_objectives": [objective.value for objective in self.supported_objectives],
        }

    def _registry_payload(self, schema_version: int) -> dict[str, object]:
        if schema_version == HISTORICAL_REGISTRY_SCHEMA_VERSION:
            return {
                "policies": [policy.historical_v1_dict() for policy in self.policies],
                "registry_schema_version": HISTORICAL_REGISTRY_SCHEMA_VERSION,
                "supported_objectives": [
                    objective.value for objective in self.supported_objectives
                ],
            }
        if schema_version == MILESTONE_11A_REGISTRY_SCHEMA_VERSION:
            return {
                "construction_policies": [
                    policy.registry_dict() for policy in self.construction_policies
                ],
                "policies": [policy.historical_v2_dict() for policy in self.policies],
                "registry_schema_version": MILESTONE_11A_REGISTRY_SCHEMA_VERSION,
                "supported_objectives": [
                    objective.value for objective in self.supported_objectives
                ],
            }
        if schema_version != REGISTRY_SCHEMA_VERSION:
            raise ValueError(f"unsupported registry schema version: {schema_version}")
        return {
            "construction_policies": [
                policy.registry_dict() for policy in self.construction_policies
            ],
            "policies": [policy.to_dict() for policy in self.policies],
            "registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "supported_objectives": [objective.value for objective in self.supported_objectives],
        }

    def _require_supported(self, objective: PortfolioObjective | str) -> PortfolioObjective:
        parsed = objective if isinstance(objective, PortfolioObjective) else PortfolioObjective.parse(objective)
        if parsed not in self._supported:
            raise UnknownObjectiveError(f"Unsupported portfolio objective: {parsed.value}")
        return parsed


def build_default_policy_registry(repository_root: Path | None = None) -> PolicyRegistry:
    """Build the reviewed repository registry from authoritative policy artifacts."""
    root = repository_root or Path(__file__).resolve().parents[3]
    rules_path = root / CAPITAL_POLICY_ARTIFACT
    methodology_path = root / CAPITAL_METHODOLOGY_ARTIFACT
    strict_path = root / CAPITAL_STRICT_PIPELINE_ARTIFACT
    construction_path = root / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
    rules = load_ranking_rules(rules_path)
    contract = build_policy_contract(
        rules_path=rules_path,
        methodology_path=methodology_path,
        strict_pipeline_path=strict_path,
    )
    _validate_capital_policy_identity(rules, contract)
    capital = InvestmentPolicy(
        objective=PortfolioObjective.CAPITAL_CONSERVATION,
        policy_id=rules.policy_name,
        version=rules.version,
        schema_version=rules.schema_version,
        review_status=PolicyReviewStatus.APPROVED,
        activation_status=PolicyActivationStatus.ACTIVE,
        mandate=CAPITAL_POLICY_MANDATE,
        artifact_reference=CAPITAL_POLICY_ARTIFACT,
        fingerprint=sha256(rules_path.read_bytes()).hexdigest(),
        capabilities=PolicyCapabilities(
            eligibility=PolicyCapabilityStatus.AVAILABLE_REVIEWED,
            instrument_screening_ranking=PolicyCapabilityStatus.AVAILABLE_REVIEWED,
            construction_policy=PolicyCapabilityStatus.AVAILABLE_REVIEWED,
            constructed_portfolio_runtime=PolicyCapabilityStatus.IMPLEMENTED_BLOCKED_BY_DATA,
            finalist_comparison=PolicyCapabilityStatus.NOT_IMPLEMENTED,
            outcome_success_criteria=PolicyCapabilityStatus.NOT_IMPLEMENTED,
        ),
    )
    construction = load_capital_defensive_construction_policy(construction_path)
    return PolicyRegistry(policies=(capital,), construction_policies=(construction,))


def render_registry_audit(registry: PolicyRegistry) -> str:
    """Render byte-stable, human-readable canonical JSON."""
    return json.dumps(registry.to_audit_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _validate_capital_policy_identity(
    rules: RankingRules, contract: Mapping[str, Any]
) -> None:
    identity = contract.get("policy_identity")
    if not isinstance(identity, Mapping):
        raise CapitalPolicyValidationError("policy contract identity is missing")
    expected = {
        "activation_state": "ACTIVE",
        "name": CAPITAL_POLICY_ID,
        "policy_schema_version": rules.schema_version,
        "policy_version": CAPITAL_POLICY_VERSION,
        "review_status": "approved",
    }
    if dict(identity) != expected:
        raise CapitalPolicyValidationError("policy loader and contract identity do not match champion")
    if contract.get("final_policy_status") != "RANKING_POLICY_ACTIVE":
        raise CapitalPolicyValidationError("capital policy contract is not active")
    if rules.policy_name != CAPITAL_POLICY_ID or rules.version != CAPITAL_POLICY_VERSION:
        raise CapitalPolicyValidationError("capital policy artifact identity changed")
    if rules.status != "approved":
        raise CapitalPolicyValidationError("capital policy artifact is not approved")
