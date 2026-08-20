"""Strict constituent-history eligibility for official backtest outcomes.

This module consumes retained, offline coverage and terminal-resolvability
artifacts.  It deliberately does not discover sources, prepare prices, or
calculate returns.  A coverage failure is an expected policy rejection; a
malformed evidence artifact remains a fail-closed error.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from portfolio_advisor.history.backtest_missing_data_policy import (
    MissingDataPolicyError,
    aggregate_holdings,
)
from portfolio_advisor.history.models import ForwardWindow
from portfolio_advisor.history.repository import HistoricalPortfolioRepository

from .models import BacktestEligibility, ConstituentDiagnostic, UnresolvedConstituent

STRICT_REJECT_WINDOW = "STRICT_REJECT_WINDOW"
BACKTEST_ELIGIBLE = "BACKTEST_ELIGIBLE"
BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT = "BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT"
TERMINAL_RESOLUTION_STATUS = "BACKTEST_UNRESOLVABLE_WITH_CURRENT_PUBLIC_EVIDENCE"
WEIGHT_TOTAL = Decimal(100)


class BacktestEligibilityError(RuntimeError):
    """Retained coverage or composition evidence cannot be used safely."""


class BacktestEligibilityGate(Protocol):
    """Evaluate official-backtest eligibility for one exact portfolio window."""

    def evaluate(
        self,
        *,
        history: HistoricalPortfolioRepository,
        portfolio_name: str,
        window: ForwardWindow,
    ) -> BacktestEligibility:
        """Return a deterministic, evidence-based decision."""


@dataclass(frozen=True, slots=True)
class StrictCoverageEligibilityGate:
    """Offline strict gate backed by the current coverage/resolution audits."""

    coverage_windows: Mapping[tuple[str, str, int], Mapping[str, object]]
    terminal_resolutions: Mapping[str, tuple[str, str]]

    @classmethod
    def from_default_artifacts(cls) -> StrictCoverageEligibilityGate:
        root = Path(__file__).resolve().parents[3]
        return cls.from_artifacts(
            root / "data/audit/backtest_window_coverage.json",
            tuple(sorted((root / "data/audit").glob("*_backtest_resolvability.json"))),
        )

    @classmethod
    def from_artifacts(
        cls,
        coverage_path: Path,
        terminal_resolution_paths: tuple[Path, ...] = (),
    ) -> StrictCoverageEligibilityGate:
        coverage_payload = _load_object(coverage_path, "backtest coverage")
        raw_windows = coverage_payload.get("windows")
        if not isinstance(raw_windows, list):
            raise BacktestEligibilityError("backtest coverage artifact has no windows list")
        windows: dict[tuple[str, str, int], Mapping[str, object]] = {}
        for raw in raw_windows:
            if not isinstance(raw, Mapping):
                raise BacktestEligibilityError("backtest coverage contains a malformed window")
            key = _window_key(raw)
            if key in windows:
                raise BacktestEligibilityError(f"duplicate coverage window: {key!r}")
            windows[key] = raw
        if not windows:
            raise BacktestEligibilityError("backtest coverage artifact has no windows")
        resolutions: dict[str, tuple[str, str]] = {}
        for path in terminal_resolution_paths:
            resolution = _load_object(path, "terminal resolvability")
            isin = _text(resolution.get("isin"), "terminal resolution ISIN").upper()
            if resolution.get("resolution_status") != TERMINAL_RESOLUTION_STATUS:
                continue
            admission = resolution.get("backtest_admission")
            if not isinstance(admission, Mapping) or any(
                admission.get(field) is not False
                for field in (
                    "nav_equivalent",
                    "backtest_return_series_approved",
                    "usable_for_backtest",
                )
            ):
                raise BacktestEligibilityError("terminal resolution has unsafe admission semantics")
            if resolution.get("research_closed") is not True:
                raise BacktestEligibilityError("terminal resolution must be research-closed")
            reference = _repository_relative(path)
            prior = resolutions.setdefault(isin, (TERMINAL_RESOLUTION_STATUS, reference))
            if prior != (TERMINAL_RESOLUTION_STATUS, reference):
                raise BacktestEligibilityError(f"conflicting terminal resolutions for {isin}")
        return cls(windows, resolutions)

    def evaluate(
        self,
        *,
        history: HistoricalPortfolioRepository,
        portfolio_name: str,
        window: ForwardWindow,
    ) -> BacktestEligibility:
        key = (window.evaluation_date.isoformat(), portfolio_name, window.horizon_days)
        raw = self.coverage_windows.get(key)
        if raw is None:
            raise BacktestEligibilityError(
                "No retained coverage evidence exists for "
                f"{portfolio_name!r} on {window.evaluation_date.isoformat()} / {window.horizon_days} days"
            )
        required = _isin_set(raw.get("required_isins"), "required_isins")
        missing = _isin_set(raw.get("missing_isins"), "missing_isins")
        unusable = _isin_set(raw.get("unusable_isins"), "unusable_isins")
        unresolved = missing | unusable
        if not unresolved.issubset(required):
            raise BacktestEligibilityError("coverage has unresolved ISIN outside required constituents")
        status = _text(raw.get("status"), "coverage status")
        if status == "COMPLETE" and unresolved:
            raise BacktestEligibilityError("complete coverage window contains unresolved constituents")
        if status != "COMPLETE" and not unresolved:
            raise BacktestEligibilityError("non-complete coverage window lacks explicit unresolved constituents")

        try:
            holdings = aggregate_holdings(
                {
                    "isin": holding.isin,
                    "allocation": holding.allocation,
                    "asset_class": holding.asset_class,
                    "currency": holding.currency,
                }
                for holding in history.holdings_at(window.evaluation_date)
                if holding.portfolio_name == portfolio_name
            )
        except MissingDataPolicyError as exc:
            raise BacktestEligibilityError(str(exc)) from exc
        holding_by_isin = {str(item["isin"]): item for item in holdings}
        if set(holding_by_isin) != required:
            raise BacktestEligibilityError(
                "retained coverage constituent set does not match the point-in-time source portfolio"
            )

        unresolved_weight = sum(
            (Decimal(str(holding_by_isin[isin]["weight"])) for isin in unresolved), Decimal()
        )
        resolvable_weight = WEIGHT_TOTAL - unresolved_weight
        constituents = tuple(
            ConstituentDiagnostic(
                isin=str(item["isin"]),
                weight=float(Decimal(str(item["weight"]))),
                asset_class=item["asset_class"] if isinstance(item["asset_class"], str) else None,
                currency=item["currency"] if isinstance(item["currency"], str) else None,
            )
            for item in holdings
        )
        blocking = tuple(
            UnresolvedConstituent(
                isin=isin,
                category=_blocking_category(isin, status, missing, self.terminal_resolutions),
                weight=float(Decimal(str(holding_by_isin[isin]["weight"]))),
                resolution_reference=(
                    self.terminal_resolutions[isin][1] if isin in self.terminal_resolutions else None
                ),
            )
            for isin in sorted(unresolved)
        )
        eligible = not unresolved and unresolved_weight == 0
        return BacktestEligibility(
            eligible=eligible,
            status=BACKTEST_ELIGIBLE if eligible else BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT,
            policy_id=STRICT_REJECT_WINDOW,
            coverage_status=status,
            resolvable_weight=float(resolvable_weight),
            unresolved_weight=float(unresolved_weight),
            blocking_constituents=blocking,
            constituent_weights=constituents,
            diagnostics_allowed=not eligible,
        )


def _blocking_category(
    isin: str,
    coverage_status: str,
    missing: set[str],
    terminal_resolutions: Mapping[str, tuple[str, str]],
) -> str:
    if isin in terminal_resolutions:
        return "TERMINAL_UNRESOLVABLE"
    if coverage_status == "RECONCILIATION_REQUIRED":
        return "RECONCILIATION_REQUIRED"
    if coverage_status == "LIFECYCLE_METHODOLOGY_REQUIRED":
        return "LIFECYCLE_METHODOLOGY_REQUIRED"
    if isin in missing:
        return "TEMPORARY_DATA_GAP"
    return "OTHER_UNUSABLE"


def _load_object(path: Path, label: str) -> Mapping[str, object]:
    if not path.is_file():
        raise BacktestEligibilityError(f"{label} artifact is missing: {path}")
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BacktestEligibilityError(f"{label} artifact is malformed: {path}") from exc
    if not isinstance(value, Mapping):
        raise BacktestEligibilityError(f"{label} artifact root must be an object")
    return value


def _window_key(raw: Mapping[str, object]) -> tuple[str, str, int]:
    observation_date = _text(raw.get("observation_date"), "coverage observation_date")
    try:
        date.fromisoformat(observation_date)
    except ValueError as exc:
        raise BacktestEligibilityError("coverage observation_date is invalid") from exc
    portfolio_name = _text(raw.get("portfolio_name"), "coverage portfolio_name")
    horizon = raw.get("horizon")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise BacktestEligibilityError("coverage horizon must be a positive integer")
    return observation_date, portfolio_name, horizon


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BacktestEligibilityError(f"{label} must be a non-empty string")
    return value.strip()


def _isin_set(value: object, label: str) -> set[str]:
    if not isinstance(value, list):
        raise BacktestEligibilityError(f"coverage {label} must be a list")
    result = {_text(item, f"coverage {label} ISIN").upper() for item in value}
    if len(result) != len(value):
        raise BacktestEligibilityError(f"coverage {label} contains duplicate ISINs")
    return result


def _repository_relative(path: Path) -> str:
    root = Path(__file__).resolve().parents[3]
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
