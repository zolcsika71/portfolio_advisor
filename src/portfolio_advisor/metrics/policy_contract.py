"""Strict Milestone 11C Phase F1 portfolio-metrics methodology contract."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json

PHASE_F1_POLICY_SCHEMA_VERSION = 1
PHASE_F1_POLICY_ID = "CAPITAL_DEFENSIVE_PORTFOLIO_METRICS_POLICY"
PHASE_F1_POLICY_VERSION = "1.0.0"
PHASE_F1_POLICY_ARTIFACT = (
    "data/knowledge/validated_rules/capital_defensive_portfolio_metrics.yaml"
)
PHASE_F1_POLICY_FINGERPRINT = (
    "b0c3540efb50e142dcc9dceee258ffd8054e24e9f986d0bf7a1c84114272b2c4"
)
PHASE_F1_PROFILE_TOKEN = "APPROVE_PHASE_F1_PROFILE"

PHASE_F1_DECISION_TOKENS = {
    "F1-D01": "APPEND_COMPLETE_BOUNDED_PREFIXES_TO_COMMON_WINDOW_START",
    "F1-D02": "EUR_FIRST_HUF_BLOCKED",
    "F1-D03": "PHASE_E_RELEASE_COMMIT_UTC_ASOF",
    "F1-D04": "STRICT_EIGHT_WAY_INTERSECTION",
    "F1-D05": "OBSERVED_ENDPOINTS_EXACT_ELAPSED_DAYS",
    "F1-D06": "BUY_AND_HOLD_DRIFT",
    "F1-D07": "UNREMUNERATED_NOMINAL_CASH",
    "F1-D08": "ESTR_OBSERVED_SEGMENT_ACT360",
    "F1-D09": "HUF_FAIL_CLOSED_PENDING_AUTHORITATIVE_CONVENTION",
    "F1-D10": "GEOMETRIC_CHAIN_ENDPOINT_RECONCILE",
    "F1-D11": "ACT365F_LOG_ELAPSED_TIME_SAMPLE_VOLATILITY",
    "F1-D12": "EXACT_INTERVAL_RISK_FREE_RETURN",
    "F1-D13": "ALIGNED_BENCHMARK_MAR_SORTINO",
    "F1-D14": "FAIL_WHOLE_RISK_ADJUSTED_RESULT",
    "F1-D15": "ENFORCE_365_AND_252_SAME_WINDOW",
    "F1-D16": "BLOCK_UNLESS_ACCUMULATING_OR_DISTRIBUTIONS_ADMITTED",
    "F1-D17": "DECIMAL50_Q18_OUTPUT_EXPLICIT_RECONCILIATION",
    "F1-D18": "NEW_REAL_PORTFOLIO_POLICY_FAMILY",
    "F1-D19": "INDEPENDENT_SAME_CURRENCY_NO_CROSS_CURRENCY_RANKING",
    "F1-D20": "ADDITIVE_EXACT_LINEAGE_SCHEMA",
    "F1-D21": "LATEST_MINIMAL_COMMON_365D_252_WINDOW",
    "F1-D22": "DEFER_SCORING_VALUES_PENDING_F2_REVIEW",
}

_SEMVER = re.compile(r"0|[1-9][0-9]*\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


class PhaseF1PolicyValidationError(ValueError):
    """The Phase F1 methodology artifact differs from the approved ballot."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise PhaseF1PolicyValidationError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class PhaseF1PortfolioMetricsPolicy:
    """Immutable approved methodology identity; it performs no calculations."""

    _canonical_payload: str
    artifact_reference: str

    @property
    def schema_version(self) -> int:
        value = self.artifact_payload()["schema_version"]
        if isinstance(value, bool) or not isinstance(value, int):  # pragma: no cover
            raise PhaseF1PolicyValidationError("canonical schema version is invalid")
        return value

    @property
    def policy_id(self) -> str:
        return str(self.artifact_payload()["policy_id"])

    @property
    def version(self) -> str:
        return str(self.artifact_payload()["version"])

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.artifact_payload())

    @property
    def decision_tokens(self) -> tuple[tuple[str, str], ...]:
        approval = self.artifact_payload()["approval"]
        assert isinstance(approval, dict)
        decisions = approval["decisions"]
        assert isinstance(decisions, dict)
        return tuple(sorted((str(key), str(value)) for key, value in decisions.items()))

    def artifact_payload(self) -> dict[str, object]:
        result = json.loads(self._canonical_payload)
        if not isinstance(result, dict):  # pragma: no cover - constructor invariant
            raise PhaseF1PolicyValidationError("canonical policy payload is not an object")
        return result

    def canonical_json(self) -> str:
        return self._canonical_payload

    def audit_payload(self) -> dict[str, object]:
        payload = self.artifact_payload()
        implementation = payload["implementation_boundary"]
        distributions = payload["distributions"]
        delivery = payload["delivery_sequence"]
        assert isinstance(implementation, dict)
        assert isinstance(distributions, dict)
        assert isinstance(delivery, dict)
        return {
            "artifact_reference": self.artifact_reference,
            "decision_tokens": dict(self.decision_tokens),
            "eur_real_candidate_status": distributions["current_eur_candidate_status"],
            "fingerprint": self.fingerprint,
            "huf_status": delivery["huf_status"],
            "implementation_boundary": implementation,
            "policy_id": self.policy_id,
            "schema_version": self.schema_version,
            "status": payload["status"],
            "version": self.version,
        }

    def render_audit(self) -> str:
        return canonical_json(self.audit_payload()) + "\n"


def load_phase_f1_portfolio_metrics_policy(
    path: Path,
    *,
    artifact_reference: str = PHASE_F1_POLICY_ARTIFACT,
) -> PhaseF1PortfolioMetricsPolicy:
    """Load the one exact human-approved Phase F1 methodology artifact."""
    _validate_artifact_reference(artifact_reference)
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except OSError as error:
        raise PhaseF1PolicyValidationError(f"could not read Phase F1 policy: {path}") from error
    except (yaml.YAMLError, PhaseF1PolicyValidationError) as error:
        raise PhaseF1PolicyValidationError(f"malformed Phase F1 policy: {error}") from error
    payload = _normalize(raw)
    if not isinstance(payload, dict):
        raise PhaseF1PolicyValidationError("Phase F1 policy must be a mapping")
    _validate_identity_and_ballot(payload)
    fingerprint = canonical_fingerprint(payload)
    if fingerprint != PHASE_F1_POLICY_FINGERPRINT:
        raise PhaseF1PolicyValidationError(
            "Phase F1 policy content differs from the approved methodology profile"
        )
    return PhaseF1PortfolioMetricsPolicy(
        _canonical_payload=canonical_json(payload),
        artifact_reference=artifact_reference,
    )


def _validate_identity_and_ballot(payload: Mapping[str, object]) -> None:
    if payload.get("schema_version") != PHASE_F1_POLICY_SCHEMA_VERSION:
        raise PhaseF1PolicyValidationError("unsupported Phase F1 policy schema_version")
    if payload.get("policy_id") != PHASE_F1_POLICY_ID:
        raise PhaseF1PolicyValidationError("unsupported Phase F1 policy identity")
    version = payload.get("version")
    if (
        not isinstance(version, str)
        or _SEMVER.fullmatch(version) is None
        or version != PHASE_F1_POLICY_VERSION
    ):
        raise PhaseF1PolicyValidationError("unsupported Phase F1 policy version")
    if payload.get("objective") != "CAPITAL_CONSERVATION":
        raise PhaseF1PolicyValidationError("unsupported Phase F1 objective")
    if payload.get("strategy") != "CAPITAL_DEFENSIVE":
        raise PhaseF1PolicyValidationError("unsupported Phase F1 strategy")
    if payload.get("status") != "APPROVED":
        raise PhaseF1PolicyValidationError("Phase F1 policy is not approved")
    approval = payload.get("approval")
    if not isinstance(approval, dict):
        raise PhaseF1PolicyValidationError("Phase F1 approval record is missing")
    if approval.get("profile_token") != PHASE_F1_PROFILE_TOKEN:
        raise PhaseF1PolicyValidationError("Phase F1 profile token differs from approval")
    if approval.get("decision_count") != len(PHASE_F1_DECISION_TOKENS):
        raise PhaseF1PolicyValidationError("Phase F1 decision count differs from approval")
    if approval.get("decisions") != PHASE_F1_DECISION_TOKENS:
        raise PhaseF1PolicyValidationError("Phase F1 decision tokens differ from approval")


def _normalize(value: object) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise PhaseF1PolicyValidationError("Phase F1 policy keys must be strings")
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, float):
        raise PhaseF1PolicyValidationError("binary floating-point policy values are prohibited")
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise PhaseF1PolicyValidationError(
        f"unsupported Phase F1 policy value type: {type(value).__name__}"
    )


def _validate_artifact_reference(value: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PhaseF1PolicyValidationError("artifact reference must be canonical relative text")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise PhaseF1PolicyValidationError("artifact reference must be canonical and repository-relative")
