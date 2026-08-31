"""Immutable governed models for the capital-conservation reference workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json


class FinalistKind(StrEnum):
    MODEL_PORTFOLIO = "MODEL_PORTFOLIO"
    SHORTLIST_INSTRUMENT = "SHORTLIST_INSTRUMENT"


class ComparisonAvailability(StrEnum):
    PARTIALLY_COMPARABLE = "PARTIALLY_COMPARABLE"
    UNAVAILABLE = "UNAVAILABLE"


class DimensionPreference(StrEnum):
    MODEL_PORTFOLIO = "MODEL_PORTFOLIO"
    SHORTLIST_INSTRUMENT = "SHORTLIST_INSTRUMENT"
    TIE = "TIE"
    UNAVAILABLE = "UNAVAILABLE"


class RecommendationStatus(StrEnum):
    RECOMMEND_MODEL_PORTFOLIO = "RECOMMEND_MODEL_PORTFOLIO"
    RECOMMEND_SHORTLIST_CANDIDATE = "RECOMMEND_SHORTLIST_CANDIDATE"
    NO_CLEAR_RECOMMENDATION = "NO_CLEAR_RECOMMENDATION"
    INSUFFICIENT_COMPARABLE_EVIDENCE = "INSUFFICIENT_COMPARABLE_EVIDENCE"


class UserChoiceState(StrEnum):
    AWAITING_USER_CHOICE = "AWAITING_USER_CHOICE"
    USER_CHOICE_RECORDED = "USER_CHOICE_RECORDED"


class UserChoiceOption(StrEnum):
    SELECT_MODEL_PORTFOLIO = "SELECT_MODEL_PORTFOLIO"
    SELECT_SHORTLIST_CANDIDATE = "SELECT_SHORTLIST_CANDIDATE"
    DEFER = "DEFER"
    DECLINE = "DECLINE"


class BoundaryStatus(StrEnum):
    NOT_PERFORMED = "NOT_PERFORMED"


@dataclass(frozen=True, slots=True)
class FinalistProvenance:
    snapshot_id: int
    snapshot_date: date
    source_file: str
    source_file_sha256: str
    source_sheet_id: int
    source_sheet_name: str
    evidence_ids: tuple[int, ...]
    source_row_numbers: tuple[int, ...]
    source_dataset_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_ids": list(self.evidence_ids),
            "snapshot_date": self.snapshot_date.isoformat(),
            "snapshot_id": self.snapshot_id,
            "source_dataset_fingerprint": self.source_dataset_fingerprint,
            "source_file": self.source_file,
            "source_file_sha256": self.source_file_sha256,
            "source_row_numbers": list(self.source_row_numbers),
            "source_sheet_id": self.source_sheet_id,
            "source_sheet_name": self.source_sheet_name,
        }


@dataclass(frozen=True, slots=True)
class WorkflowFinalist:
    kind: FinalistKind
    stable_id: str
    database_local_id: int
    display_name: str
    rank: int
    eligible: bool
    total_score: float
    feature_values: tuple[tuple[str, float | None], ...]
    provenance: FinalistProvenance

    def to_dict(self) -> dict[str, object]:
        return {
            "database_local_id": self.database_local_id,
            "display_name": self.display_name,
            "eligible": self.eligible,
            "feature_values": dict(self.feature_values),
            "kind": self.kind.value,
            "provenance": self.provenance.to_dict(),
            "rank": self.rank,
            "stable_id": self.stable_id,
            "total_score": self.total_score,
        }


@dataclass(frozen=True, slots=True)
class ComparisonDimension:
    dimension_id: str
    availability: ComparisonAvailability
    model_value: float | None
    shortlist_value: float | None
    unit: str
    horizon: str
    direction: str
    preference: DimensionPreference
    reason_code: str

    def to_dict(self) -> dict[str, object]:
        return {
            "availability": self.availability.value,
            "dimension_id": self.dimension_id,
            "direction": self.direction,
            "horizon": self.horizon,
            "model_value": self.model_value,
            "preference": self.preference.value,
            "reason_code": self.reason_code,
            "shortlist_value": self.shortlist_value,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class FinalistComparisonPolicy:
    policy_id: str
    version: str
    schema_version: int
    status: str
    objective: str
    method: str
    dimensions: tuple[tuple[str, str, str, str], ...]
    minimum_comparable_dimensions: int
    score_comparison: str

    def to_dict(self) -> dict[str, object]:
        return {
            "dimensions": [
                {"direction": direction, "horizon": horizon, "id": name, "unit": unit}
                for name, direction, unit, horizon in self.dimensions
            ],
            "method": self.method,
            "minimum_comparable_dimensions": self.minimum_comparable_dimensions,
            "objective": self.objective,
            "policy_id": self.policy_id,
            "schema_version": self.schema_version,
            "score_comparison": self.score_comparison,
            "status": self.status,
            "version": self.version,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.to_dict())


@dataclass(frozen=True, slots=True)
class GovernedRecommendation:
    status: RecommendationStatus
    recommended_finalist_id: str | None
    alternative_finalist_id: str | None
    reason_codes: tuple[str, ...]
    unavailable_dimensions: tuple[str, ...]
    comparison_policy_id: str
    comparison_policy_version: str
    comparison_policy_fingerprint: str
    dimensions: tuple[ComparisonDimension, ...]

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "alternative_finalist_id": self.alternative_finalist_id,
            "comparison_policy_fingerprint": self.comparison_policy_fingerprint,
            "comparison_policy_id": self.comparison_policy_id,
            "comparison_policy_version": self.comparison_policy_version,
            "dimensions": [dimension.to_dict() for dimension in self.dimensions],
            "reason_codes": list(self.reason_codes),
            "recommended_finalist_id": self.recommended_finalist_id,
            "status": self.status.value,
            "unavailable_dimensions": list(self.unavailable_dimensions),
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.fingerprint_payload())

    def to_dict(self) -> dict[str, object]:
        return {**self.fingerprint_payload(), "recommendation_fingerprint": self.fingerprint}


@dataclass(frozen=True, slots=True)
class CapitalConservationReferenceWorkflow:
    workflow_id: str
    workflow_version: str
    objective: str
    common_as_of_date: date
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    registry_fingerprint: str
    capability_states: tuple[tuple[str, str], ...]
    model_finalist: WorkflowFinalist
    shortlist_finalist: WorkflowFinalist
    recommendation: GovernedRecommendation
    user_choice_state: UserChoiceState
    valid_choice_options: tuple[UserChoiceOption, ...]
    allocation_status: BoundaryStatus = BoundaryStatus.NOT_PERFORMED
    cash_deployment_status: BoundaryStatus = BoundaryStatus.NOT_PERFORMED
    fx_conversion_status: BoundaryStatus = BoundaryStatus.NOT_PERFORMED
    persistence_status: BoundaryStatus = BoundaryStatus.NOT_PERFORMED

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "allocation_status": self.allocation_status.value,
            "capability_states": dict(self.capability_states),
            "cash_deployment_status": self.cash_deployment_status.value,
            "common_as_of_date": self.common_as_of_date.isoformat(),
            "fx_conversion_status": self.fx_conversion_status.value,
            "model_finalist": self.model_finalist.to_dict(),
            "objective": self.objective,
            "persistence_status": self.persistence_status.value,
            "policy_fingerprint": self.policy_fingerprint,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "recommendation": self.recommendation.to_dict(),
            "registry_fingerprint": self.registry_fingerprint,
            "shortlist_finalist": self.shortlist_finalist.to_dict(),
            "user_choice": None,
            "user_choice_state": self.user_choice_state.value,
            "valid_choice_options": [choice.value for choice in self.valid_choice_options],
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
        }

    @property
    def workflow_fingerprint(self) -> str:
        return canonical_fingerprint(self.fingerprint_payload())

    def to_dict(self) -> dict[str, object]:
        return {**self.fingerprint_payload(), "workflow_fingerprint": self.workflow_fingerprint}

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class CapitalConservationUserChoice:
    workflow_fingerprint: str
    recommendation_fingerprint: str
    choice: UserChoiceOption
    selected_finalist_id: str | None
    disagrees_with_recommendation: bool
    state: UserChoiceState = UserChoiceState.USER_CHOICE_RECORDED
    persistence_status: BoundaryStatus = BoundaryStatus.NOT_PERFORMED

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "choice": self.choice.value,
            "disagrees_with_recommendation": self.disagrees_with_recommendation,
            "persistence_status": self.persistence_status.value,
            "recommendation_fingerprint": self.recommendation_fingerprint,
            "selected_finalist_id": self.selected_finalist_id,
            "state": self.state.value,
            "workflow_fingerprint": self.workflow_fingerprint,
        }

    @property
    def choice_fingerprint(self) -> str:
        return canonical_fingerprint(self.fingerprint_payload())

    def to_dict(self) -> dict[str, object]:
        return {**self.fingerprint_payload(), "choice_fingerprint": self.choice_fingerprint}
