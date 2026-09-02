"""Read-only deterministic validation of retained and installed NY Fed SOFR evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    ConstructionPolicyValidationError,
    load_capital_defensive_construction_policy,
)
from portfolio_advisor.reference_rates.sofr import SofrError, validate_sofr_database

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--raw-artifact", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        policy = load_capital_defensive_construction_policy(arguments.policy)
        result = validate_sofr_database(
            target=arguments.target,
            repository_root=arguments.repository_root,
            raw_artifact=arguments.raw_artifact,
            receipt_path=arguments.receipt,
            policy=policy,
        )
    except (ConstructionPolicyValidationError, OSError, SofrError, ValueError) as error:
        print(f"SOFR validation failed: {error}", file=sys.stderr)
        return 2
    output = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        sys.stdout.write(output)
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
