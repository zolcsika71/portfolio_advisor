"""Offline validation of current backtest metrics and point-in-time ranking semantics."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.history.repository import HistoricalPortfolioRepository
from portfolio_advisor.metrics.methodology_validation import (
    CapitalPreservationValidationError,
    build_methodology_validation,
)

DEFAULT_DATABASE = Path("database/model_portfolio.sqlite")
DEFAULT_RULES = Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml")
DEFAULT_STRICT = Path("data/audit/strict_backtest_pipeline_validation.json")
DEFAULT_OUTPUT = Path("data/audit/capital_preservation_metrics_ranking_validation.json")


def _load_json(path: Path, label: str) -> dict[str, object]:
    if not path.is_file():
        raise CapitalPreservationValidationError(f"{label} is missing: {path}")
    try:
        result: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapitalPreservationValidationError(f"{label} is malformed: {path}") from exc
    if not isinstance(result, dict):
        raise CapitalPreservationValidationError(f"{label} root must be an object")
    return result


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
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--strict-validation", type=Path, default=DEFAULT_STRICT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        strict = _load_json(args.strict_validation, "strict pipeline validation")
        if strict.get("validation_status") != "STRICT_BACKTEST_PIPELINE_VALIDATED":
            raise CapitalPreservationValidationError("strict pipeline is not validated")
        history = HistoricalPortfolioRepository(ModelPortfolioRepository(args.database))
        payload = build_methodology_validation(
            rules_path=args.rules,
            nav_history_available=history.nav_history_available(),
        )
        payload["source_artifact_references"] = {
            "strict_pipeline_validation": "data/audit/strict_backtest_pipeline_validation.json",
            "ranking_rules": "data/knowledge/validated_rules/capital_preservation_ranking.yaml",
        }
        _write_json_atomic(args.output, payload)
    except (CapitalPreservationValidationError, ValueError, RuntimeError) as error:
        print(f"Capital-preservation methodology validation failed: {error}", file=sys.stderr)
        return 2
    print(f"Capital-preservation methodology: {payload['validation_status']}")
    print(f"Alignment: {payload['capital_preservation_alignment']}")
    print(f"JSON output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
