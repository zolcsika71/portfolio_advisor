"""Synthetic official-shaped €STR fixtures; this module never contacts a network."""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    load_capital_defensive_construction_policy,
)
from portfolio_advisor.reference_rates.contracts import canonical_request_parameters
from portfolio_advisor.reference_rates.ecb_estr import (
    ECB_ESTR_MACHINE_URL,
    ECB_ESTR_REQUEST_PARAMETERS,
    EcbEstrAcquisitionReceipt,
    receipt_json,
)

ROOT = Path(__file__).resolve().parents[1]

HISTORY_HEADER = (
    "KEY",
    "FREQ",
    "BENCHMARK_ITEM",
    "DATA_TYPE_EST",
    "TIME_PERIOD",
    "OBS_VALUE",
    "OBS_STATUS",
    "CONF_STATUS",
    "PRE_BREAK_VALUE",
    "COMMENT_OBS",
    "CALCUL_START_DATE",
    "CALCUL_END_DATE",
    "TIME_FORMAT",
    "BREAKS",
    "COMMENT_TS",
    "COMPILING_ORG",
    "COVERAGE",
    "DATA_COMP",
    "DECIMALS",
    "DISS_ORG",
    "PUBL_ECB",
    "PUBL_MU",
    "PUBL_PUBLIC",
    "TIME_PER_COLLECT",
    "TITLE",
    "TITLE_COMPL",
    "UNIT_INDEX_BASE",
    "UNIT_MEASURE",
    "UNIT_MULT",
    "ACTION",
    "VALID_FROM",
    "VALID_TO",
)


def row(
    *,
    observation_date: str = "2026-08-31",
    value: str = "2.185",
    valid_from: str = "2026-09-01T06:05:24Z",
    valid_to: str = "",
    status: str = "A",
    action: str = "Replace",
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    values = {name: "" for name in HISTORY_HEADER}
    values.update(
        {
            "KEY": "EST.B.EU000A2X2A25.WT",
            "FREQ": "B",
            "BENCHMARK_ITEM": "EU000A2X2A25",
            "DATA_TYPE_EST": "WT",
            "TIME_PERIOD": observation_date,
            "OBS_VALUE": value,
            "OBS_STATUS": status,
            "CONF_STATUS": "F",
            "TIME_FORMAT": "P1D",
            "DECIMALS": "3",
            "TIME_PER_COLLECT": "A",
            "TITLE": "Euro short-term rate",
            "TITLE_COMPL": (
                "Euro short-term rate, Volume-weighted trimmed mean rate - Unsecured - "
                "Overnight - Borrowing - Financial corporations"
            ),
            "UNIT_MEASURE": "PC",
            "UNIT_MULT": "0",
            "ACTION": action,
            "VALID_FROM": valid_from,
            "VALID_TO": valid_to,
        }
    )
    if overrides:
        values.update(overrides)
    return values


def csv_bytes(rows: tuple[dict[str, str], ...] | None = None) -> bytes:
    selected = rows or (
        row(
            observation_date="2026-08-28",
            value="2.186",
            valid_from="2026-08-31T06:05:00Z",
        ),
        row(),
    )
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(HISTORY_HEADER)
    for values in selected:
        writer.writerow([values[name] for name in HISTORY_HEADER])
    return stream.getvalue().encode("utf-8")


def write_evidence(
    repository_root: Path,
    *,
    raw: bytes | None = None,
    retrieval_timestamp: str = "2026-09-01T08:30:00+00:00",
    last_modified: str = "Tue, 01 Sep 2026 06:05:24 GMT",
) -> tuple[Path, Path, EcbEstrAcquisitionReceipt]:
    content = raw or csv_bytes()
    digest = hashlib.sha256(content).hexdigest()
    directory = repository_root / "data" / "raw" / "reference_rates" / "ecb" / "estr"
    directory.mkdir(parents=True, exist_ok=True)
    raw_path = directory / f"estr-{digest}.csv"
    raw_path.write_bytes(content)
    receipt = EcbEstrAcquisitionReceipt(
        receipt_schema_version=1,
        request_url=ECB_ESTR_MACHINE_URL,
        request_parameters=canonical_request_parameters(ECB_ESTR_REQUEST_PARAMETERS),
        effective_url=(
            f"{ECB_ESTR_MACHINE_URL}?detail=full&format=csvdata&includeHistory=true"
        ),
        retrieval_timestamp=retrieval_timestamp,
        http_status=200,
        response_content_type="text/csv",
        content_encoding="identity",
        content_length=len(content),
        content_disposition="attachment;filename=data.csv",
        last_modified=last_modified,
        etag=None,
        byte_count=len(content),
        raw_artifact_reference=raw_path.relative_to(repository_root).as_posix(),
        raw_artifact_sha256=digest,
    )
    receipt_path = directory / f"estr-{digest}.receipt.json"
    receipt_path.write_text(receipt_json(receipt), encoding="utf-8")
    return raw_path, receipt_path, receipt


def construction_policy():
    return load_capital_defensive_construction_policy(
        ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
    )
