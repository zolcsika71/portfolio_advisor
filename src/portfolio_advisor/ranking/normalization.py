"""Reproducible min-max normalization with an explicit tied-value policy."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite


def normalize_metric(values: Mapping[str, float], direction: str) -> dict[str, float]:
    """Normalize scores to [0, 1], giving identical raw values a score of 1.

    When all candidates tie, none is penalized for a metric that supplies no
    discrimination. ``direction`` is ``HIGHER_BETTER`` or ``LOWER_BETTER``.
    """
    if direction not in {"HIGHER_BETTER", "LOWER_BETTER"}:
        raise ValueError("direction must be HIGHER_BETTER or LOWER_BETTER")
    if not values:
        return {}
    if any(not isfinite(value) for value in values.values()):
        raise ValueError("normalization values must be finite")
    lower, upper = min(values.values()), max(values.values())
    if upper == lower:
        return {name: 1.0 for name in values}
    if direction == "HIGHER_BETTER":
        return {name: (value - lower) / (upper - lower) for name, value in values.items()}
    return {name: (upper - value) / (upper - lower) for name, value in values.items()}
