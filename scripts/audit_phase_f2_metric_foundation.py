"""Write or print the deterministic Phase F2 metric-foundation audit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.history.nav_provenance import (
    NavProvenanceError,
    validate_phase_e_nav,
)
from portfolio_advisor.metrics.foundation_audit import (
    build_phase_f2_foundation_audit,
    render_phase_f2_foundation_audit,
)
from portfolio_advisor.metrics.policy_contract import (
    PHASE_F1_POLICY_ARTIFACT,
    PhaseF1PolicyValidationError,
    load_phase_f1_portfolio_metrics_policy,
)

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "database/portfolio_advisor.sqlite",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / PHASE_F1_POLICY_ARTIFACT,
    )
    parser.add_argument(
        "--phase-e-index",
        type=Path,
        default=ROOT / "data/raw/nav/erste_market/phase-e-index.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional audit path; stdout is used when omitted.",
    )
    arguments = parser.parse_args(argv)
    try:
        policy = load_phase_f1_portfolio_metrics_policy(arguments.policy)
        phase_e_validation = validate_phase_e_nav(
            repository_root=ROOT,
            target=arguments.database,
            index_path=arguments.phase_e_index,
            legacy_source=arguments.database,
        )
        audit = build_phase_f2_foundation_audit(
            policy=policy,
            phase_e_validation=phase_e_validation,
        )
        rendered = render_phase_f2_foundation_audit(audit)
    except (NavProvenanceError, OSError, PhaseF1PolicyValidationError, ValueError) as error:
        print(f"Phase F2 metric-foundation audit failed: {error}", file=sys.stderr)
        return 2
    if arguments.output is None:
        sys.stdout.write(rendered)
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
