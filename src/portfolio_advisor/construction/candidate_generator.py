"""Pruned deterministic highest-ranked feasible-set selection."""

from __future__ import annotations

from collections import Counter
from datetime import date

from portfolio_advisor.objectives import CapitalDefensiveConstructionPolicy

from .constraints import policy_integer
from .diversification import (
    category_completion_possible,
    diversification_limits,
    is_diversified,
)
from .models import RankedConstructionInstrument


def select_highest_ranked_feasible_set(
    instruments: tuple[RankedConstructionInstrument, ...],
    policy: CapitalDefensiveConstructionPolicy,
    *,
    require_common_nav_window: bool = True,
) -> tuple[RankedConstructionInstrument, ...] | None:
    """Return the first feasible set in rank-vector/ISIN order using DFS pruning."""
    required = policy_integer(policy, "allocation", "security_count")
    minimum_intervals = policy_integer(
        policy, "historical_nav", "minimum_aligned_return_intervals"
    )
    minimum_span = policy_integer(
        policy, "historical_nav", "minimum_history_span_calendar_days"
    )
    _, maximum_per_group = diversification_limits(policy)
    ordered = tuple(sorted(instruments, key=lambda item: (item.rank, item.isin)))

    def search(
        index: int,
        selected: tuple[RankedConstructionInstrument, ...],
        common_dates: frozenset[date],
    ) -> tuple[RankedConstructionInstrument, ...] | None:
        if len(selected) == required:
            common_date_values = sorted(common_dates)
            if (
                is_diversified(selected, policy)
                and (
                    not require_common_nav_window
                    or (
                        len(common_date_values) - 1 >= minimum_intervals
                        and (common_date_values[-1] - common_date_values[0]).days
                        >= minimum_span
                    )
                )
            ):
                return selected
            return None
        remaining = ordered[index:]
        if not category_completion_possible(selected, remaining, required, policy):
            return None
        if index >= len(ordered):
            return None
        item = ordered[index]
        counts = Counter(value.group for value in selected)
        if item.group is not None and counts[item.group] < maximum_per_group:
            item_dates = frozenset(item.nav.observation_dates)
            intersection = item_dates if not selected else common_dates & item_dates
            ordered_dates = sorted(intersection)
            if (
                not require_common_nav_window
                or (
                    len(ordered_dates) - 1 >= minimum_intervals
                    and (ordered_dates[-1] - ordered_dates[0]).days >= minimum_span
                )
            ):
                included = search(index + 1, selected + (item,), intersection)
                if included is not None:
                    return included
        return search(index + 1, selected, common_dates)

    return search(0, (), frozenset())
