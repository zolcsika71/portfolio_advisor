"""Offline end-to-end validation of strict backtest result admission."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from portfolio_advisor.backtesting.eligibility import StrictCoverageEligibilityGate
from portfolio_advisor.backtesting.validation import (
    StrictPipelineValidationError,
    validate_strict_pipeline,
)
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.history.repository import HistoricalPortfolioRepository

DEFAULT_COVERAGE = Path("data/audit/backtest_window_coverage.json")
DEFAULT_POLICY = Path("data/audit/backtest_missing_data_policy_analysis.json")
DEFAULT_DATABASE = Path("database/model_portfolio.sqlite")
DEFAULT_OUTPUT = Path("data/audit/strict_backtest_pipeline_validation.json")


def _load_json(path: Path, label: str) -> dict[str, object]:
    if not path.is_file():
        raise StrictPipelineValidationError(f"{label} is missing: {path}")
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrictPipelineValidationError(f"{label} is malformed: {path}") from exc
    if not isinstance(value, dict):
        raise StrictPipelineValidationError(f"{label} root must be an object")
    return value


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
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--policy-analysis", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        coverage = _load_json(args.coverage, "backtest coverage artifact")
        policy = _load_json(args.policy_analysis, "missing-data policy analysis")
        history = HistoricalPortfolioRepository(ModelPortfolioRepository(args.database))
        gate = StrictCoverageEligibilityGate.from_default_artifacts()
        result = validate_strict_pipeline(
            history=history,
            gate=gate,
            coverage_payload=coverage,
            policy_payload=policy,
        )
        _write_json_atomic(args.output, result)
    except (StrictPipelineValidationError, ValueError, RuntimeError) as error:
        print(f"Strict backtest pipeline validation failed: {error}", file=sys.stderr)
        return 2
    dataset = result["dataset"]
    if not isinstance(dataset, dict):  # Defensive; builder validates this structure.
        print("Strict backtest pipeline validation failed: invalid result", file=sys.stderr)
        return 2
    print("Strict backtest pipeline: VALIDATED")
    print(f"Total windows: {dataset['total_windows']}")
    print(f"Official eligible: {dataset['official_eligible_windows']}")
    print(f"Rejected: {dataset['rejected_windows']}")
    print(f"JSON output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
