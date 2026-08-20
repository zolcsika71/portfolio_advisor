"""Inventory local MNB/KELER OTC report artifacts; never performs network I/O."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.mnb_otc import MnbOtcRepository
from portfolio_advisor.history.mnb_otc_inventory import (
    LocalReportRecord,
    build_manual_acquisition_manifest,
    inventory_local_reports,
    inventory_summary,
)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_acquisition_provenance(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"MNB OTC acquisition provenance is unreadable: {exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("source") != "mnb_otc":
        raise RuntimeError("MNB OTC acquisition provenance is malformed")
    return payload


def _enrich_artifact(
    record: dict[str, object], provenance: dict[str, object]
) -> dict[str, object]:
    details = provenance.get("acquisition_records")
    if not isinstance(details, list):
        return record
    matching = [
        item
        for item in details
        if isinstance(item, dict) and item.get("sha256") == record["sha256"]
    ]
    if len(matching) > 1:
        raise RuntimeError("MNB OTC acquisition provenance contains duplicate hashes")
    if not matching:
        return record
    enriched = dict(record)
    item = matching[0]
    for field in (
        "source_authority",
        "source_host",
        "source_url",
        "acquisition_timestamp",
        "publication_id",
    ):
        if field in item:
            enriched[field] = item[field]
    return enriched


def build_discovery_manifest(
    records: tuple[LocalReportRecord, ...], provenance: dict[str, object] | None = None
) -> dict[str, object]:
    provenance = provenance or {}
    artifact_records = [
        _enrich_artifact(record.as_dict(), provenance) for record in records
    ]
    acquisition_records = provenance.get("acquisition_records", [])
    if not isinstance(acquisition_records, list):
        raise TypeError("MNB OTC acquisition records are malformed")
    acquired = [item for item in acquisition_records if isinstance(item, dict)]
    return {
        "schema_version": 2,
        "source": "mnb_otc",
        "target_isin": "HU0000554795",
        "target_instrument": "K250604 Egyéves Magyar Állampapír",
        "discovery_method": provenance.get(
            "discovery_method", "LOCAL_INVENTORY_AND_MANUAL_AUTHORITATIVE_DOWNLOAD"
        ),
        "discovery_status": provenance.get(
            "discovery_status",
            "AUTOMATED_DISCOVERY_REJECTED_NO_DOCUMENTED_MACHINE_ARCHIVE",
        ),
        "authoritative_listing_host": "kozzetetelek.mnb.hu",
        "target_acquisition_interval": provenance.get("target_acquisition_interval"),
        "discovered_reports": provenance.get("discovered_reports", []),
        "candidate_reports": artifact_records,
        "already_local_reports": [
            record for record in artifact_records if record["artifact_type"] == "PDF"
        ],
        "downloaded_reports": [
            item
            for item in acquired
            if item.get("acquisition_status") == "REPORT_ACQUIRED"
        ],
        "rejected_reports": [
            item
            for item in acquired
            if "CONFLICT" in str(item.get("acquisition_status"))
        ],
        "failed_downloads": [
            item
            for item in acquired
            if str(item.get("acquisition_status")).endswith("FAILED")
        ],
        "duplicate_reports": [
            record for record in artifact_records if record["duplicate_of"] is not None
        ],
        "exact_isin_positive_reports": [
            record
            for record in artifact_records
            if record["contains_exact_isin"] is True
            and record["artifact_type"] == "PDF"
        ],
        "summary": inventory_summary(records),
        "network_scope": provenance.get(
            "network_scope", "No network I/O; local inventory only."
        ),
    }


def build_acquisition_manifest(
    records: tuple[LocalReportRecord, ...], provenance: dict[str, object]
) -> dict[str, object]:
    """Keep report acquisition coverage distinct from target-observation coverage."""
    manifest = build_manual_acquisition_manifest(records)
    if not provenance:
        return manifest
    acquisition_records = provenance.get("acquisition_records", [])
    if not isinstance(acquisition_records, list):
        raise TypeError("MNB OTC acquisition records are malformed")
    report_status_counts: dict[str, int] = {}
    for item in acquisition_records:
        if not isinstance(item, dict):
            raise TypeError("MNB OTC acquisition record is malformed")
        status = item.get("acquisition_status")
        if not isinstance(status, str):
            raise TypeError("MNB OTC acquisition record has no status")
        report_status_counts[status] = report_status_counts.get(status, 0) + 1
    manifest.update(
        {
            "schema_version": 2,
            "discovery_method": provenance["discovery_method"],
            "remote_discovery_status": provenance["discovery_status"],
            "target_acquisition_interval": provenance["target_acquisition_interval"],
            "source_authority": provenance["source_authority"],
            "source_host": provenance["source_host"],
            "discovered_reports": provenance["discovered_reports"],
            "remote_acquisition_records": acquisition_records,
            "remote_acquisition_status_counts": dict(
                sorted(report_status_counts.items())
            ),
            "expected_reporting_periods": [],
            "unacquired_period_status": "REPORT_DISCOVERY_UNRESOLVED_CADENCE_NOT_ESTABLISHED",
            "unresolved_periods": [],
            "acquisition_coverage_note": (
                "A locally retained report establishes acquisition evidence only; an exact-ISIN "
                "row remains separate observation evidence. No expected-period denominator is "
                "emitted because retained official periods are irregular; missing reports are "
                "never NO_TRADES."
            ),
        }
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Repository root to inventory recursively.",
    )
    parser.add_argument(
        "--database", type=Path, default=Path("database/model_portfolio.sqlite")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/audit/mnb_otc_report_discovery.json")
    )
    parser.add_argument(
        "--acquisition-output",
        type=Path,
        default=Path("data/mnb_otc/report_acquisition_manifest.json"),
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        default=Path("data/mnb_otc/acquisition_provenance.json"),
    )
    args = parser.parse_args()
    try:
        imported_hashes = frozenset(
            item.source_document_hash
            for item in MnbOtcRepository(args.database).observations()
        )
        records = inventory_local_reports(
            args.root.resolve(), imported_hashes=imported_hashes
        )
        provenance = load_acquisition_provenance(args.provenance)
        write_json(args.output, build_discovery_manifest(records, provenance))
        write_json(
            args.acquisition_output, build_acquisition_manifest(records, provenance)
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"MNB OTC local inventory failed closed: {exc}", file=sys.stderr)
        return 1
    summary = inventory_summary(records)
    print(f"MNB OTC candidate reports: {summary['candidate_report_count']}")
    print(
        f"MNB OTC exact-ISIN PDF reports: {summary['importable_exact_isin_pdf_reports']}"
    )
    print(
        "Remote discovery: "
        + (
            "OFFICIAL BOUNDED LISTING INVENTORIED"
            if provenance
            else "MANUAL AUTHORITATIVE DOWNLOAD REQUIRED"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
