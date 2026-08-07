"""Reproducible min-max normalization with an explicit tied-value policy."""

from __future__ import annotations

from collections.abc import Mapping


def normalize_metric(values: Mapping[str, float], direction: str) -> dict[str, float]:
    """Normalize scores to [0, 1], giving identical raw values a score of 1.

    When all candidates tie, none is penalized for a metric that supplies no
    discrimination. ``direction`` is either ``higher`` or ``lower``.
    """
    if direction not in {"higher", "lower"}:
        raise ValueError("direction must be 'higher' or 'lower'")
    if not values:
        return {}
    lower, upper = min(values.values()), max(values.values())
    if upper == lower:
        return {name: 1.0 for name in values}
    if direction == "higher":
        return {name: (value - lower) / (upper - lower) for name, value in values.items()}
    return {name: (upper - value) / (upper - lower) for name, value in values.items()}
