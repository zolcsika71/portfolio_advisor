"""Deterministic category concentration and feasibility checks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from portfolio_advisor.objectives import CapitalDefensiveConstructionPolicy

from .constraints import policy_integer
from .models import RankedConstructionInstrument


def diversification_limits(
    policy: CapitalDefensiveConstructionPolicy,
) -> tuple[int, int]:
    return (
        policy_integer(
            policy, "diversification", "minimum_distinct_conflict_free_groups"
        ),
        policy_integer(policy, "diversification", "maximum_holdings_per_group"),
    )


def is_diversified(
    selected: Sequence[RankedConstructionInstrument],
    policy: CapitalDefensiveConstructionPolicy,
) -> bool:
    minimum_groups, maximum_per_group = diversification_limits(policy)
    groups = [item.group for item in selected]
    if any(group is None for group in groups):
        return False
    counts = Counter(groups)
    return len(counts) >= minimum_groups and max(counts.values(), default=0) <= maximum_per_group


def category_completion_possible(
    selected: Sequence[RankedConstructionInstrument],
    remaining: Sequence[RankedConstructionInstrument],
    required_count: int,
    policy: CapitalDefensiveConstructionPolicy,
) -> bool:
    """Cheap deterministic pruning without enumerating feasible sets."""
    minimum_groups, maximum_per_group = diversification_limits(policy)
    needed = required_count - len(selected)
    if needed < 0 or len(remaining) < needed:
        return False
    counts = Counter(item.group for item in selected)
    if any(group is None for group in counts) or any(
        count > maximum_per_group for count in counts.values()
    ):
        return False
    possible_groups = set(counts) | {item.group for item in remaining if item.group is not None}
    if len(possible_groups) < minimum_groups:
        return False
    capacity = sum(maximum_per_group - counts[group] for group in possible_groups)
    return capacity >= needed
