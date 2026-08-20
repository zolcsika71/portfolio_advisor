"""Discover and retain bounded official MNB/KELER weekly OTC PDF reports.

Network access exists only in this explicit command.  Parsing, importing and
all audit commands remain local-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from portfolio_advisor.history.mnb_otc import TARGET_HU_ISIN, TARGET_HU_NAME
from portfolio_advisor.history.mnb_otc_acquisition import (
    OFFICIAL_HOST,
    MnbOtcAcquisitionError,
    acquire_official_reports,
    discover_official_reports,
)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected ISO date, got {value!r}") from exc


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def acquisition_manifest(
    start: date, end: date, discovered: tuple[object, ...], acquired: tuple[object, ...]
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "source": "mnb_otc",
        "target_isin": TARGET_HU_ISIN,
        "target_instrument": TARGET_HU_NAME,
        "target_acquisition_interval": {
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "discovery_method": "MNB_PUBLIC_ADVANCED_SEARCH_POST",
        "discovery_status": "OFFICIAL_BOUNDED_LISTING_VERIFIED",
        "source_authority": "MNB public publication infrastructure / KELER publication",
        "source_host": OFFICIAL_HOST,
        "discovered_reports": [item.as_dict() for item in discovered],
        "acquisition_records": [item.as_dict() for item in acquired],
        "network_scope": "This explicit acquisition command only; parser/import/audit commands are offline.",
        "no_opaque_id_bruteforce": True,
        "no_unbounded_crawling": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", type=parse_date, required=True)
    parser.add_argument("--to", dest="end", type=parse_date, required=True)
    parser.add_argument("--isin", default=TARGET_HU_ISIN)
    parser.add_argument("--raw-directory", type=Path, default=Path("data/mnb_otc/raw"))
    parser.add_argument(
        "--provenance-output",
        type=Path,
        default=Path("data/mnb_otc/acquisition_provenance.json"),
    )
    args = parser.parse_args()
    if args.isin != TARGET_HU_ISIN:
        print(
            "MNB OTC acquisition fails closed: only exact ISIN HU0000554795 is supported",
            file=sys.stderr,
        )
        return 1
    try:
        discovered = discover_official_reports(args.start, args.end)
        acquired = acquire_official_reports(discovered, args.raw_directory)
        write_json(
            args.provenance_output,
            acquisition_manifest(args.start, args.end, discovered, acquired),
        )
    except MnbOtcAcquisitionError as exc:
        print(f"MNB OTC official acquisition failed closed: {exc}", file=sys.stderr)
        return 1
    successful = sum(record.status == "REPORT_ACQUIRED" for record in acquired)
    duplicates = sum(
        record.status == "REPORT_ACQUIRED_DUPLICATE" for record in acquired
    )
    failed = sum(
        record.status.endswith("FAILED") or "CONFLICT" in record.status
        for record in acquired
    )
    print(f"MNB official report listings: {len(discovered)}")
    print(f"MNB PDFs newly retained: {successful}")
    print(f"MNB duplicate PDFs: {duplicates}")
    print(f"MNB failed/rejected downloads: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
