"""Typed, immutable objective and investment-policy domain models."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class PortfolioObjective(StrEnum):
    """Stable objective identities used by reviewed policy governance."""

    CAPITAL_CONSERVATION = "capital_conservation"
    DIVIDEND_PORTFOLIO = "dividend_portfolio"

    @classmethod
    def parse(cls, value: str) -> PortfolioObjective:
        """Parse an exact stable value without aliases or normalization."""
        try:
            return cls(value)
        except ValueError as error:
            raise UnknownObjectiveError(f"Unknown portfolio objective: {value!r}") from error


class PolicyReviewStatus(StrEnum):
    """Review state declared by an immutable policy registration."""

    APPROVED = "approved"
    REVIEWED = "reviewed"
    UNREVIEWED = "unreviewed"


class PolicyActivationStatus(StrEnum):
    """Whether a reviewed policy is eligible for active resolution."""

    ACTIVE = "ACTIVE"
    NOT_ACTIVATED = "NOT_ACTIVATED"


class PolicyCapabilityStatus(StrEnum):
    """Availability of one explicitly governed policy capability."""

    AVAILABLE_REVIEWED = "AVAILABLE_REVIEWED"
    IMPLEMENTED_BLOCKED_BY_DATA = "IMPLEMENTED_BLOCKED_BY_DATA"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    NO_VALIDATED_ACTIVE_POLICY = "NO_VALIDATED_ACTIVE_POLICY"


class PolicyAvailability(StrEnum):
    """Availability of an active, validated policy for an objective."""

    VALIDATED_ACTIVE_POLICY = "VALIDATED_ACTIVE_POLICY"
    NO_VALIDATED_ACTIVE_POLICY = "NO_VALIDATED_ACTIVE_POLICY"


class ObjectiveFrameworkError(RuntimeError):
    """Base class for fail-closed objective-framework errors."""


class UnknownObjectiveError(ObjectiveFrameworkError, ValueError):
    """Raised when an objective is not one of the exact supported values."""


class InvalidInvestmentPolicyError(ObjectiveFrameworkError, ValueError):
    """Raised when immutable policy metadata is internally inconsistent."""


@dataclass(frozen=True, slots=True)
class PolicyCapabilities:
    """Reviewed capability states for a specific policy identity and version."""

    eligibility: PolicyCapabilityStatus
    instrument_screening_ranking: PolicyCapabilityStatus
    construction_policy: PolicyCapabilityStatus
    constructed_portfolio_runtime: PolicyCapabilityStatus
    finalist_comparison: PolicyCapabilityStatus
    outcome_success_criteria: PolicyCapabilityStatus

    @property
    def ranking(self) -> PolicyCapabilityStatus:
        """Deprecated compatibility alias for instrument screening/ranking."""
        warnings.warn(
            "PolicyCapabilities.ranking is deprecated; use instrument_screening_ranking",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.instrument_screening_ranking

    @property
    def construction(self) -> PolicyCapabilityStatus:
        """Deprecated compatibility alias for actual constructed-portfolio runtime."""
        warnings.warn(
            "PolicyCapabilities.construction is deprecated; use "
            "construction_policy or constructed_portfolio_runtime",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.constructed_portfolio_runtime

    def to_dict(self) -> dict[str, str]:
        """Return a stable, explicitly keyed representation."""
        return {
            "constructed_portfolio_runtime": self.constructed_portfolio_runtime.value,
            "construction_policy": self.construction_policy.value,
            "eligibility": self.eligibility.value,
            "finalist_comparison": self.finalist_comparison.value,
            "instrument_screening_ranking": self.instrument_screening_ranking.value,
            "outcome_success_criteria": self.outcome_success_criteria.value,
        }

    def historical_v1_dict(self) -> dict[str, str]:
        """Reproduce the pre-11A registry capability serialization exactly."""
        return {
            "construction": self.construction_policy.value,
            "eligibility": self.eligibility.value,
            "finalist_comparison": self.construction_policy.value,
            "outcome_success_criteria": self.outcome_success_criteria.value,
            "ranking": self.instrument_screening_ranking.value,
        }

    def historical_v2_dict(self) -> dict[str, str]:
        """Reproduce the Milestone 11A capability serialization exactly."""
        value = self.to_dict()
        value["constructed_portfolio_runtime"] = PolicyCapabilityStatus.NOT_IMPLEMENTED.value
        return value


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class InvestmentPolicy:
    """Reviewed policy metadata; financial rules remain in the source artifact."""

    objective: PortfolioObjective
    policy_id: str
    version: str
    schema_version: int
    review_status: PolicyReviewStatus
    activation_status: PolicyActivationStatus
    mandate: str
    artifact_reference: str
    fingerprint: str
    capabilities: PolicyCapabilities

    def __post_init__(self) -> None:
        """Reject incomplete, path-dependent, or contradictory registrations."""
        if not isinstance(self.objective, PortfolioObjective):
            raise InvalidInvestmentPolicyError("objective must be a PortfolioObjective")
        for field, value in (
            ("policy_id", self.policy_id),
            ("version", self.version),
            ("mandate", self.mandate),
            ("artifact_reference", self.artifact_reference),
        ):
            if not value or value != value.strip():
                raise InvalidInvestmentPolicyError(f"{field} must be an exact non-empty value")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise InvalidInvestmentPolicyError("schema_version must be a positive integer")
        if not isinstance(self.review_status, PolicyReviewStatus):
            raise InvalidInvestmentPolicyError("review_status must be a PolicyReviewStatus")
        if not isinstance(self.activation_status, PolicyActivationStatus):
            raise InvalidInvestmentPolicyError("activation_status must be a PolicyActivationStatus")
        if not isinstance(self.capabilities, PolicyCapabilities):
            raise InvalidInvestmentPolicyError("capabilities must be PolicyCapabilities")
        artifact = PurePosixPath(self.artifact_reference)
        if artifact.is_absolute() or ".." in artifact.parts or artifact.as_posix() != self.artifact_reference:
            raise InvalidInvestmentPolicyError("artifact_reference must be repository-relative POSIX path")
        if _SHA256_PATTERN.fullmatch(self.fingerprint) is None:
            raise InvalidInvestmentPolicyError("fingerprint must be a lowercase SHA-256 value")
        if (
            self.activation_status is PolicyActivationStatus.ACTIVE
            and self.review_status is not PolicyReviewStatus.APPROVED
        ):
            raise InvalidInvestmentPolicyError("only an approved policy may be active")

    @property
    def identity(self) -> tuple[PortfolioObjective, str, str]:
        """Return the exact registry key."""
        return (self.objective, self.policy_id, self.version)

    def to_dict(self) -> dict[str, object]:
        """Return deterministic, environment-neutral audit metadata."""
        return {
            "activation_status": self.activation_status.value,
            "artifact_reference": self.artifact_reference,
            "capabilities": self.capabilities.to_dict(),
            "fingerprint": self.fingerprint,
            "mandate": self.mandate,
            "objective": self.objective.value,
            "policy_id": self.policy_id,
            "review_status": self.review_status.value,
            "schema_version": self.schema_version,
            "version": self.version,
        }

    def historical_v1_dict(self) -> dict[str, object]:
        """Reproduce the registry-schema-v1 policy payload for historical audits."""
        value = self.to_dict()
        value["capabilities"] = self.capabilities.historical_v1_dict()
        return value

    def historical_v2_dict(self) -> dict[str, object]:
        """Reproduce registry schema v2 before the 11B runtime correction."""
        value = self.to_dict()
        value["capabilities"] = self.capabilities.historical_v2_dict()
        return value
