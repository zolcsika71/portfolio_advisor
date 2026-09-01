"""Audit the reviewed Capital Defensive policy without constructing a portfolio."""

from __future__ import annotations

import sys
from pathlib import Path

from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    ObjectiveFrameworkError,
    build_default_policy_registry,
    load_capital_defensive_construction_policy,
)
from portfolio_advisor.objectives.construction_audit import (
    render_construction_policy_audit,
)


def main() -> int:
    """Validate authoritative artifacts and emit deterministic readiness JSON."""
    root = Path(__file__).resolve().parents[1]
    try:
        policy = load_capital_defensive_construction_policy(
            root / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
        )
        output = render_construction_policy_audit(
            policy,
            build_default_policy_registry(root),
        )
    except (ObjectiveFrameworkError, OSError, ValueError, RuntimeError) as error:
        print(f"Capital Defensive construction policy audit failed: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
