"""Simulate unapproved portfolio missing-data policies from local audit evidence only."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

from portfolio_advisor.database.repository import (
    ModelPortfolioRepository,
    RepositoryError,
)
from portfolio_advisor.history.backtest_missing_data_policy import (
    MissingDataPolicyError,
    build_policy_analysis,
)

DEFAULT_COVERAGE = Path("data/audit/backtest_window_coverage.json")
DEFAULT_TERMINAL_RESOLUTION = Path("data/audit/hu0000554795_backtest_resolvability.json")
DEFAULT_HU_LIFECYCLE = Path("data/audit/hu0000554795_lifecycle.json")
DEFAULT_DATABASE = Path("database/model_portfolio.sqlite")
DEFAULT_OUTPUT = Path("data/audit/backtest_missing_data_policy_analysis.json")
DEFAULT_CSV = Path("data/audit/backtest_missing_data_policy_windows.csv")


def load_object(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MissingDataPolicyError(f"Unable to load {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MissingDataPolicyError(f"{label} must contain an object")
    return payload


def holdings_by_snapshot(
    repository: ModelPortfolioRepository, coverage: dict[str, object]
) -> dict[tuple[str, str], list[dict[str, object]]]:
    windows = coverage.get("windows")
    if not isinstance(windows, list):
        raise MissingDataPolicyError("Coverage artifact has no window list")
    keys: set[tuple[str, str]] = set()
    for window in windows:
        if not isinstance(window, dict):
            raise MissingDataPolicyError("Coverage artifact has malformed window")
        observation_date = window.get("observation_date")
        portfolio_name = window.get("portfolio_name")
        if not isinstance(observation_date, str) or not isinstance(portfolio_name, str):
            raise MissingDataPolicyError("Coverage window has no snapshot identity")
        keys.add((observation_date, portfolio_name))
    result: dict[tuple[str, str], list[dict[str, object]]] = {}
    dated: dict[str, list[str]] = {}
    for observation_date, portfolio_name in sorted(keys):
        dated.setdefault(observation_date, []).append(portfolio_name)
    for observation_date, portfolio_names in dated.items():
        try:
            source_date = date.fromisoformat(observation_date)
            holdings = repository.load_holdings(source_date)
        except (RepositoryError, ValueError) as exc:
            raise MissingDataPolicyError(f"Cannot load source holdings for {observation_date}: {exc}") from exc
        grouped: dict[str, list[dict[str, object]]] = {}
        for holding in holdings:
            grouped.setdefault(holding.portfolio_name, []).append(
                {
                    "isin": holding.isin,
                    "allocation": holding.allocation,
                    "asset_class": holding.asset_class,
                    "currency": holding.currency,
                }
            )
        for portfolio_name in portfolio_names:
            selected = grouped.get(portfolio_name)
            if selected is None:
                raise MissingDataPolicyError(
                    f"Portfolio {portfolio_name!r} is missing at {observation_date}"
                )
            result[(observation_date, portfolio_name)] = selected
    return result


def hu_lifecycle_by_window(payload: dict[str, object]) -> dict[tuple[str, str, int], dict[str, object]]:
    if payload.get("isin") != "HU0000554795" or payload.get("maturity_validated") is not True:
        raise MissingDataPolicyError("HU0000554795 lifecycle audit is invalid")
    rows = payload.get("affected_window_lifecycle")
    if not isinstance(rows, list) or len(rows) != 132:
        raise MissingDataPolicyError("HU0000554795 lifecycle audit has invalid window evidence")
    result: dict[tuple[str, str, int], dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise MissingDataPolicyError("HU0000554795 lifecycle row is malformed")
        observation_date = row.get("observation_date")
        portfolio_name = row.get("portfolio_name")
        horizon = row.get("horizon")
        classification = row.get("lifecycle_classification")
        if (
            not isinstance(observation_date, str)
            or not isinstance(portfolio_name, str)
            or isinstance(horizon, bool)
            or not isinstance(horizon, int)
            or not isinstance(classification, str)
        ):
            raise MissingDataPolicyError("HU0000554795 lifecycle row has invalid identity")
        key = (observation_date, portfolio_name, horizon)
        if key in result:
            raise MissingDataPolicyError("HU0000554795 lifecycle audit has duplicate window identity")
        result[key] = row
    return result


def _write_csv(path: Path, analysis: dict[str, object]) -> None:
    rows = analysis.get("window_simulations")
    if not isinstance(rows, list):
        raise MissingDataPolicyError("Policy analysis has no window simulations")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "portfolio_name",
        "observation_date",
        "horizon",
        "required_start",
        "required_end",
        "current_coverage_status",
        "lifecycle_classification",
        "constituent_count",
        "resolvable_weight",
        "unresolved_weight",
        "unresolved_constituent_count",
        "unresolved_isins",
        "terminal_unresolved_isins",
        "terminal_constituent_weights",
        "strict_reject_eligible",
        "partial_diagnostics_outcome",
        "threshold_100_eligible",
        "threshold_99_eligible",
        "threshold_95_eligible",
        "threshold_90_eligible",
        "threshold_80_eligible",
        "renormalization_factor",
        "renormalization_outcome",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            if not isinstance(row, dict):
                raise MissingDataPolicyError("Policy simulation row is malformed")
            thresholds = row.get("thresholds")
            strict = row.get("strict_reject")
            partial = row.get("partial_diagnostics")
            renormalization = row.get("renormalization")
            if not all(isinstance(value, dict) for value in (thresholds, strict, partial, renormalization)):
                raise MissingDataPolicyError("Policy simulation details are malformed")
            writer.writerow(
                {
                    "portfolio_name": row["portfolio_name"],
                    "observation_date": row["observation_date"],
                    "horizon": row["horizon"],
                    "required_start": row["required_start"],
                    "required_end": row["required_end"],
                    "current_coverage_status": row["current_coverage_status"],
                    "lifecycle_classification": row["lifecycle_classification"],
                    "constituent_count": row["constituent_count"],
                    "resolvable_weight": row["resolvable_weight"],
                    "unresolved_weight": row["unresolved_weight"],
                    "unresolved_constituent_count": row["unresolved_constituent_count"],
                    "unresolved_isins": json.dumps(row["unresolved_isins"], ensure_ascii=False),
                    "terminal_unresolved_isins": json.dumps(row["terminal_unresolved_isins"], ensure_ascii=False),
                    "terminal_constituent_weights": json.dumps(
                        row["terminal_constituent_weights"], ensure_ascii=False, sort_keys=True
                    ),
                    "strict_reject_eligible": strict["eligible"],
                    "partial_diagnostics_outcome": partial["simulation_outcome"],
                    "threshold_100_eligible": thresholds["100"]["eligible"],
                    "threshold_99_eligible": thresholds["99"]["eligible"],
                    "threshold_95_eligible": thresholds["95"]["eligible"],
                    "threshold_90_eligible": thresholds["90"]["eligible"],
                    "threshold_80_eligible": thresholds["80"]["eligible"],
                    "renormalization_factor": renormalization["renormalization_factor"],
                    "renormalization_outcome": renormalization["simulation_outcome"],
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--terminal-resolution", type=Path, action="append")
    parser.add_argument("--hu-lifecycle", type=Path, default=DEFAULT_HU_LIFECYCLE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--csv-output", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()
    terminal_paths = args.terminal_resolution or [DEFAULT_TERMINAL_RESOLUTION]
    try:
        coverage = load_object(args.coverage, "coverage audit")
        resolutions = [load_object(path, "terminal resolution") for path in terminal_paths]
        lifecycle = hu_lifecycle_by_window(load_object(args.hu_lifecycle, "HU lifecycle audit"))
        terminal = {
            str(payload.get("isin")): payload
            for payload in resolutions
            if isinstance(payload.get("isin"), str)
        }
        if len(terminal) != len(resolutions):
            raise MissingDataPolicyError("Terminal resolutions must have distinct exact ISINs")
        source_holdings = holdings_by_snapshot(ModelPortfolioRepository(args.database), coverage)
        references = {
            "backtest_window_coverage": str(args.coverage),
            "database_snapshot_source": str(args.database),
            "hu0000554795_lifecycle": str(args.hu_lifecycle),
            **{
                f"terminal_resolution_{payload['isin']!s}": str(path)
                for path, payload in sorted(
                    zip(terminal_paths, resolutions, strict=True), key=lambda item: str(item[1]["isin"])
                )
            },
        }
        if any(path.startswith("/") for path in references.values()):
            raise MissingDataPolicyError("Audit references must be repository-relative")
        analysis = build_policy_analysis(
            coverage_payload=coverage,
            holdings_by_snapshot=source_holdings,
            terminal_resolutions=terminal,
            artifact_references=references,
            lifecycle_by_window=lifecycle,
        )
        _write_csv(args.csv_output, analysis)
    except (MissingDataPolicyError, RepositoryError) as exc:
        print(f"Backtest missing-data policy audit failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dataset = analysis["current_dataset"]
    recommendation = analysis["recommendation"]
    print(f"Policy simulation windows: {dataset['total_actual_windows']}")
    print(f"Primary future candidate: {recommendation['primary_policy_candidate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
