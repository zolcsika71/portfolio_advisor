"""Create the offline terminal backtest-resolvability resolution for HU0000554795."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.backtest_resolvability import (
    DEFAULT_ARTIFACT_REFERENCES,
    BacktestResolvabilityError,
    build_resolution,
)


def load(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BacktestResolvabilityError(f"Unable to load required evidence {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BacktestResolvabilityError(f"Required evidence {path} must contain an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/audit/hu0000554795_backtest_resolvability.json"))
    args = parser.parse_args()
    try:
        evidence = {
            name: load(Path(path))
            for name, path in DEFAULT_ARTIFACT_REFERENCES.items()
        }
        artifact = build_resolution(
            erste=evidence["erste_diagnostics"],
            mnb_coverage=evidence["mnb_otc_coverage"],
            lifecycle=evidence["lifecycle"],
            redemption=evidence["redemption_methodology"],
            sparse=evidence["sparse_trading_semantics"],
            report_scope=evidence["report_scope_semantics"],
            scope_ledger=evidence["scope_research_ledger"],
            absence_freeze=evidence["absence_semantics_freeze"],
            price_evidence=evidence["price_semantics_evidence"],
            price_audit=evidence["price_semantics_audit"],
            alternative_research=evidence["alternative_source_research"],
            alternative_audit=evidence["alternative_source_audit"],
            coverage=evidence["backtest_window_coverage"],
            artifact_references=DEFAULT_ARTIFACT_REFERENCES,
        )
    except BacktestResolvabilityError as exc:
        print(f"HU0000554795 backtest-resolvability audit failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"HU0000554795 resolution: {artifact['resolution_status']}")
    print("Research closed: YES; reopen allowed: YES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
