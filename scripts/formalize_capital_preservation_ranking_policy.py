"""Generate the offline capital-preservation ranking policy contract."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from portfolio_advisor.ranking.policy_contract import (
    PolicyContractValidationError,
    build_policy_contract,
)

DEFAULT_RULES = Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml")
DEFAULT_METHODOLOGY = Path("data/audit/capital_preservation_metrics_ranking_validation.json")
DEFAULT_STRICT = Path("data/audit/strict_backtest_pipeline_validation.json")
DEFAULT_OUTPUT = Path("data/audit/capital_preservation_ranking_policy_contract.json")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--methodology", type=Path, default=DEFAULT_METHODOLOGY)
    parser.add_argument("--strict-validation", type=Path, default=DEFAULT_STRICT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        payload = build_policy_contract(
            rules_path=args.rules, methodology_path=args.methodology, strict_pipeline_path=args.strict_validation
        )
        _write_json_atomic(args.output, payload)
    except (PolicyContractValidationError, ValueError, RuntimeError) as error:
        print(f"Policy formalization failed: {error}", file=sys.stderr)
        return 2
    print(f"Capital-preservation ranking policy: {payload['final_policy_status']}")
    print(f"JSON output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
