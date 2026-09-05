"""Print the deterministic synthetic-only Phase F3A wealth-foundation audit."""

from __future__ import annotations

import sys
from pathlib import Path

from portfolio_advisor.metrics.policy_contract import (
    PHASE_F1_POLICY_ARTIFACT,
    PhaseF1PolicyValidationError,
    load_phase_f1_portfolio_metrics_policy,
)
from portfolio_advisor.metrics.portfolio_wealth import PhaseF3AValidationError
from portfolio_advisor.metrics.wealth_foundation_audit import (
    build_phase_f3a_wealth_foundation_audit,
    render_phase_f3a_wealth_foundation_audit,
)
from portfolio_advisor.objectives.construction_policy import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    ConstructionPolicyValidationError,
    load_capital_defensive_construction_policy,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Validate both policies and emit one timestamp-free audit to stdout."""
    try:
        metrics_policy = load_phase_f1_portfolio_metrics_policy(
            ROOT / PHASE_F1_POLICY_ARTIFACT
        )
        construction_policy = load_capital_defensive_construction_policy(
            ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
        )
        payload = build_phase_f3a_wealth_foundation_audit(
            metrics_policy=metrics_policy,
            construction_policy=construction_policy,
        )
        sys.stdout.write(render_phase_f3a_wealth_foundation_audit(payload))
    except (
        ConstructionPolicyValidationError,
        OSError,
        PhaseF1PolicyValidationError,
        PhaseF3AValidationError,
        ValueError,
    ) as error:
        print(f"Phase F3A wealth-foundation audit failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
