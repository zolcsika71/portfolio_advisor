"""Generate audit-only MNB OTC source-quality manifest from local SQLite evidence."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from portfolio_advisor.history.mnb_otc import (
    MnbOtcObservation,
    MnbOtcRepository,
    quality_summary,
)


def load_discovery_summary(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"status": "LOCAL_INVENTORY_NOT_GENERATED"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"MNB OTC discovery manifest is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("source") != "mnb_otc":
        raise RuntimeError("MNB OTC discovery manifest is malformed")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise TypeError("MNB OTC discovery manifest has no summary")
    return dict(summary)


def build_manifest(
    database_path: Path, discovery_path: Path = Path("data/audit/mnb_otc_report_discovery.json")
) -> dict[str, object]:
    observations = MnbOtcRepository(database_path).observations()
    acquisition_summary = load_discovery_summary(discovery_path)
    by_isin: dict[str, list[MnbOtcObservation]] = defaultdict(list)
    for observation in observations:
        by_isin[observation.isin].append(observation)
    records: list[dict[str, object]] = []
    for isin, items in sorted(by_isin.items()):
        first = items[0]
        records.append(
            {
                "source": "mnb_otc",
                "status": "VALIDATED_OTC_EVIDENCE_NOT_APPROVED",
                "isin": isin,
                "instrument_name": first.instrument_name,
                "currency": first.currency,
                "price_semantics": "weekly OTC transaction-price aggregate",
                "price_type": first.price_type,
                "frequency": first.frequency,
                "quality": quality_summary(items),
                "acquisition_summary": acquisition_summary,
                "provenance": [
                    {
                        "source_document": item.source_document,
                        "source_document_hash": item.source_document_hash,
                    }
                    for item in items
                ],
                "nav_equivalent": False,
                "backtest_return_series_approved": False,
                "eligible_for_fallback_coverage": False,
            }
        )
    return {
        "schema_version": 1,
        "scope": "MNB/KELER weekly OTC source evidence only; no NAV or backtest approval",
        "source": "mnb_otc",
        "database": str(database_path),
        "discovery_manifest": str(discovery_path),
        "acquisition_summary": acquisition_summary,
        "status": "NO_LOCAL_MNB_OTC_REPORTS" if not records else "EVIDENCE_GENERATED_NOT_APPROVED",
        "results": records,
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
    }


def write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument(
        "--discovery", type=Path, default=Path("data/audit/mnb_otc_report_discovery.json")
    )
    parser.add_argument("--output", type=Path, default=Path("data/audit/mnb_otc_coverage.json"))
    args = parser.parse_args()
    manifest = build_manifest(args.database, args.discovery)
    write_manifest(args.output, manifest)
    print(f"MNB OTC audit status: {manifest['status']}")
    print(f"MNB OTC exact-ISIN records: {len(manifest['results'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
