"""Synthetic official-shaped SOFR fixtures; this module never contacts a network."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    load_capital_defensive_construction_policy,
)
from portfolio_advisor.reference_rates.contracts import canonical_request_parameters
from portfolio_advisor.reference_rates.sofr import (
    SOFR_MACHINE_URL,
    SOFR_REQUEST_PARAMETERS,
    SofrAcquisitionReceipt,
    receipt_json,
)

ROOT = Path(__file__).resolve().parents[1]


def row(
    observation_date: str,
    *,
    product: str = "SOFR",
    rate: float = 2.18,
    revision: str = "",
    contingency: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {
        "effectiveDate": observation_date,
        "type": product,
        "percentRate": rate,
        "percentPercentile1": 2.11,
        "percentPercentile25": 2.15,
        "percentPercentile75": 2.21,
        "percentPercentile99": 2.29,
        "revisionIndicator": revision,
    }
    if contingency:
        for field in (
            "percentPercentile1",
            "percentPercentile25",
            "percentPercentile75",
            "percentPercentile99",
        ):
            result[field] = "NA"
        result["footnoteId"] = 2
    return result


def valid_rows() -> list[dict[str, object]]:
    start = date(2018, 4, 2)
    rows = [row((start + timedelta(days=index)).isoformat()) for index in range(2101)]
    rows.append(row("2026-08-31", contingency=True))
    return rows


def json_bytes(rows: list[dict[str, object]] | None = None) -> bytes:
    return json.dumps(
        {"refRates": valid_rows() if rows is None else rows},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def write_evidence(
    repository_root: Path,
    *,
    raw: bytes | None = None,
    retrieval_timestamp: str = "2026-09-02T17:00:39+00:00",
) -> tuple[Path, Path, SofrAcquisitionReceipt]:
    content = raw or json_bytes()
    digest = hashlib.sha256(content).hexdigest()
    directory = (
        repository_root
        / "data"
        / "raw"
        / "reference_rates"
        / "new_york_fed"
        / "sofr"
    )
    directory.mkdir(parents=True, exist_ok=True)
    raw_path = directory / f"sofr-{digest}.json"
    raw_path.write_bytes(content)
    receipt = SofrAcquisitionReceipt(
        receipt_schema_version=1,
        request_url=SOFR_MACHINE_URL,
        request_parameters=canonical_request_parameters(SOFR_REQUEST_PARAMETERS),
        effective_url=(
            f"{SOFR_MACHINE_URL}?startDate=2018-04-02&endDate=2026-08-31&type=rate"
        ),
        retrieval_timestamp=retrieval_timestamp,
        http_status=200,
        response_content_type="application/json;charset=utf-8",
        content_encoding="",
        content_length=len(content),
        response_date="Wed, 02 Sep 2026 17:00:38 GMT",
        last_modified=None,
        etag=None,
        byte_count=len(content),
        raw_artifact_reference=raw_path.relative_to(repository_root).as_posix(),
        raw_artifact_sha256=digest,
    )
    receipt_path = raw_path.with_suffix(".receipt.json")
    receipt_path.write_text(receipt_json(receipt), encoding="utf-8")
    return raw_path, receipt_path, receipt


def construction_policy():
    return load_capital_defensive_construction_policy(
        ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
    )
