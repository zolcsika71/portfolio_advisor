"""Canonical audit rendering for the reviewed construction-policy contract."""

from __future__ import annotations

import json
from typing import cast

from .construction_policy import CapitalDefensiveConstructionPolicy
from .models import PortfolioObjective
from .registry import HISTORICAL_REGISTRY_SCHEMA_VERSION, PolicyRegistry


def construction_policy_audit_payload(
    policy: CapitalDefensiveConstructionPolicy,
    registry: PolicyRegistry,
) -> dict[str, object]:
    """Build a deterministic, path-neutral, privacy-free readiness audit."""
    active = registry.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION)
    registered = registry.exact_construction_policy(*policy.identity)
    if registered.fingerprint != policy.fingerprint:
        raise ValueError("construction policy fingerprint conflicts with registry")
    policy_payload = policy.artifact_payload()
    approved_constraints = {
        name: policy_payload[name]
        for name in (
            "allocation",
            "candidate_generation",
            "cash_input",
            "currency_behavior",
            "diversification",
            "historical_nav",
            "portfolio_risk",
            "reference_rate_methodology",
        )
    }
    dependencies = cast(dict[str, str], policy_payload["runtime_dependencies"])
    return {
        "approved_constraints": approved_constraints,
        "artifact_fingerprint": policy.fingerprint,
        "artifact_reference": policy.artifact_reference,
        "audit_schema_version": 1,
        "capability_states": active.capabilities.to_dict(),
        "construction_output": {
            "can_produce_constructed_portfolio": False,
            "statement": "NO_CONSTRUCTED_PORTFOLIO_CAN_YET_BE_PRODUCED",
        },
        "historical_registry_fingerprints": {
            str(HISTORICAL_REGISTRY_SCHEMA_VERSION): registry.registry_fingerprint(
                schema_version=HISTORICAL_REGISTRY_SCHEMA_VERSION
            )
        },
        "objective": policy.objective,
        "official_reference_rate_mappings": {
            rate.currency: rate.to_dict() for rate in policy.reference_rates
        },
        "policy_id": policy.policy_id,
        "policy_version": policy.version,
        "production_cutover": "NOT_AUTHORIZED",
        "ranking_policy_fingerprint": active.fingerprint,
        "registry_fingerprint": registry.registry_fingerprint(),
        "runtime_construction_readiness": policy.runtime_construction_readiness,
        "runtime_dependencies": dependencies,
        "schema_version": policy.schema_version,
        "status": policy.status,
        "strategy": policy.strategy,
        "supported_currencies": list(policy.supported_currencies),
    }


def render_construction_policy_audit(
    policy: CapitalDefensiveConstructionPolicy,
    registry: PolicyRegistry,
) -> str:
    """Render byte-stable indented JSON for operator review."""
    return json.dumps(
        construction_policy_audit_payload(policy, registry),
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ) + "\n"
