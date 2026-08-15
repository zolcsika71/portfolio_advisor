"""Validate bounded, public OeKB historical NAV acquisition for selected ISINs."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import requests

from portfolio_advisor.history.oekb import (
    OEK_B_HISTORY_URL,
    OEK_B_PLATFORM_CONTEXT,
    OekbAcquisitionError,
    OekbHistory,
    OekbHttpResponse,
    fetch_bounded_oekb_history,
)

ISINS = (
    "AT0000673314",
    "AT0000627484",
    "AT0000A2VH41",
    "AT0000605324",
)
DATE_FROM = date(2025, 1, 1)
DATE_TO = date(2026, 8, 8)


def http_get(url: str, timeout: int) -> OekbHttpResponse:
    """Perform one cookie-free public OeKB request with contract headers."""
    response = requests.get(
        url,
        headers={
            "Accept": "application/json",
            "OeKB-Platform-Context": OEK_B_PLATFORM_CONTEXT,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return OekbHttpResponse(status_code=response.status_code, body=response.content)


def fetch_oekb_history(
    isin: str,
    date_from: date,
    date_to: date,
    limit: int = 100,
    timeout: int = 30,
) -> OekbHistory:
    """Fetch all bounded chunks through the shared OeKB acquisition helper."""
    return fetch_bounded_oekb_history(
        isin=isin,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        timeout=timeout,
        http_get=http_get,
    )


def validation_record(isin: str, history: OekbHistory | None, error: str | None) -> dict[str, object]:
    if history is None:
        return {
            "requested_isin": isin,
            "success": False,
            "error": error,
            "merged_result": {
                "requested_isin": isin,
                "returned_isin": None,
                "currency": None,
                "raw_observation_count": 0,
                "normalized_observation_count": 0,
                "first_date": None,
                "last_date": None,
                "duplicate_count": 0,
                "conflict_count": 0,
                "usability_status": "SOURCE_ERROR",
            },
            "chunks": [],
        }
    success = history.usable
    return {
        "requested_isin": isin,
        "success": success,
        "error": None if success else "No historical observations returned",
        "merged_result": history.summary(),
        "chunks": [chunk.as_dict() for chunk in history.chunks],
    }


def validate_isin(
    isin: str,
    *,
    date_from: date = DATE_FROM,
    date_to: date = DATE_TO,
    limit: int = 100,
    timeout: int = 30,
) -> dict[str, object]:
    print(f"\n{'=' * 70}\nISIN: {isin}\n{'=' * 70}")
    try:
        history = fetch_oekb_history(isin, date_from, date_to, limit, timeout)
    except (OekbAcquisitionError, requests.RequestException, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return validation_record(isin, None, str(exc))

    record = validation_record(isin, history, None)
    summary = history.summary()
    print(f"Chunks requested:      {len(history.chunks)}")
    print(f"Raw observations:      {summary['raw_observation_count']}")
    print(f"Normalized observations: {summary['normalized_observation_count']}")
    print(f"Exact ISIN match:       {history.returned_isin == isin.upper()}")
    print(f"Currency:               {history.currency}")
    print(f"First observation:      {summary['first_date']}")
    print(f"Last observation:       {summary['last_date']}")
    if history.usable:
        print("PASS")
    else:
        print("FAIL: no historical observations returned")
    return record


def write_audit_output(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isin", action="append", choices=ISINS, help="ISIN to validate; repeatable.")
    parser.add_argument("--date-from", type=date.fromisoformat, default=DATE_FROM)
    parser.add_argument("--date-to", type=date.fromisoformat, default=DATE_TO)
    parser.add_argument("--limit", type=int, default=100, help="OeKB page size.")
    parser.add_argument("--timeout", type=int, default=30, help="OeKB request timeout in seconds.")
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=Path("data/audit/oekb_validation.json"),
        help="JSON provenance and validation output.",
    )
    args = parser.parse_args()
    isins = tuple(args.isin) if args.isin else ISINS
    records = [
        validate_isin(
            isin,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=args.limit,
            timeout=args.timeout,
        )
        for isin in isins
    ]
    failures = sum(not bool(record["success"]) for record in records)
    payload: dict[str, Any] = {
        "source_name": "oekb",
        "endpoint_template": OEK_B_HISTORY_URL,
        "required_header_name": "OeKB-Platform-Context",
        "requested_date_range": {
            "date_from": args.date_from.isoformat(),
            "date_to": args.date_to.isoformat(),
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "results": records,
        "summary": {"tested": len(records), "passed": len(records) - failures, "failed": failures},
    }
    write_audit_output(args.audit_output, payload)
    print(f"\nAudit output: {args.audit_output}")
    print(f"Tested: {len(records)}  Passed: {len(records) - failures}  Failed: {failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
