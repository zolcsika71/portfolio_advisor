"""Deprecated exploratory model-versus-instrument workflow API."""

from .capital_conservation import (
    CapitalConservationWorkflowError,
    build_capital_conservation_reference_workflow,
    record_capital_conservation_user_choice,
)
from .comparison import (
    COMPARISON_POLICY_ID,
    COMPARISON_POLICY_VERSION,
    FinalistComparisonError,
    build_finalist_comparison_policy,
    compare_finalists,
)
from .models import (
    CapitalConservationReferenceWorkflow,
    CapitalConservationUserChoice,
    RecommendationStatus,
    UserChoiceOption,
    UserChoiceState,
)

__all__ = [
    "COMPARISON_POLICY_ID",
    "COMPARISON_POLICY_VERSION",
    "CapitalConservationReferenceWorkflow",
    "CapitalConservationUserChoice",
    "CapitalConservationWorkflowError",
    "FinalistComparisonError",
    "RecommendationStatus",
    "UserChoiceOption",
    "UserChoiceState",
    "build_capital_conservation_reference_workflow",
    "build_finalist_comparison_policy",
    "compare_finalists",
    "record_capital_conservation_user_choice",
]
