from __future__ import annotations

import hashlib
import json
import sqlite3
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, cast

import pytest

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.history.nav_correction_evidence import (
    ArtifactReference,
    ObservationReferenceType,
    inspect_nav_correction_candidate,
)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write(root: Path, relative: str, raw: bytes) -> dict[str, object]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "relative_path": relative,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_count": len(raw),
    }


def _write_json(root: Path, relative: str, value: object) -> dict[str, object]:
    return _write(root, relative, _json_bytes(value))


def _key(**changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "isin": "HU0000000011",
        "share_class": "Synthetic EUR Class",
        "valuation_date": "2026-01-02",
        "value_type": "NAV",
        "currency": "EUR",
        "unit": "PER_UNIT",
        "precision_basis": "PUBLISHED_DECIMAL_TEXT",
    }
    result.update(changes)
    return result


def _binding(
    root: Path,
    *,
    observation_id: str,
    reference_type: str,
    source: dict[str, object],
    source_locator: str,
    key: dict[str, object],
    value: str,
) -> dict[str, object]:
    record = {
        "bindings": [
            {
                "schema_version": 1,
                "record_type": "NAV_OBSERVATION_REFERENCE_BINDING",
                "observation_id": observation_id,
                "reference_type": reference_type,
                "source_artifact_sha256": source["raw_sha256"],
                "source_locator": source_locator,
                "key": key,
                "raw_value": value,
            }
        ]
    }
    artifact = _write_json(root, f"bindings/{observation_id}.json", record)
    return {"artifact": artifact, "locator": "/bindings/0"}


def _receipt(
    root: Path,
    *,
    name: str,
    role: str,
    artifact: dict[str, object],
    timestamp: str = "2026-01-03T12:00:00Z",
) -> tuple[dict[str, object], dict[str, object]]:
    receipt = _write_json(
        root,
        f"receipts/{name}.json",
        {
            "schema_version": 1,
            "record_type": "NAV_CORRECTION_ACQUISITION_RECEIPT",
            "artifact": artifact,
            "retrieved_at_utc": timestamp,
        },
    )
    event: dict[str, object] = {
        "event_id": f"acq:{name}",
        "artifact_role": role,
        "artifact": artifact,
        "receipt": receipt,
        "timestamp_locator": "/retrieved_at_utc",
        "retrieved_at_utc": timestamp,
    }
    return receipt, event


def _distribution_evidence_receipt(
    root: Path,
    *,
    name: str,
    artifact: dict[str, object],
    timestamp: str,
) -> dict[str, object]:
    return _write_json(
        root,
        f"receipts/{name}.distribution-evidence.json",
        {
            "schema_version": 1,
            "record_type": "DISTRIBUTION_EVIDENCE_ACQUISITION_RECEIPT",
            "artifact": {
                "path": artifact["relative_path"],
                "sha256": artifact["raw_sha256"],
            },
            "retrieved_at_utc": timestamp,
        },
    )


def _legacy_database(root: Path, value: str = "1.1000") -> dict[str, object]:
    path = root / "snapshots/legacy.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE instrument (
                instrument_id INTEGER PRIMARY KEY,
                isin TEXT NOT NULL
            );
            CREATE TABLE instrument_nav_observation (
                instrument_nav_observation_id INTEGER PRIMARY KEY,
                instrument_id INTEGER NOT NULL,
                observation_date TEXT NOT NULL,
                nav_value REAL NOT NULL,
                currency_code TEXT NOT NULL,
                value_type TEXT NOT NULL,
                source_provider TEXT NOT NULL,
                source_identifier TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL
            );
            """
        )
        connection.execute("INSERT INTO instrument VALUES (7, 'HU0000000011')")
        connection.execute(
            "INSERT INTO instrument_nav_observation VALUES (11,7,?,?,?,?,?,?,?)",
            (
                "2026-01-02",
                float(value),
                "EUR",
                "NAV",
                "synthetic_provider",
                "series-7",
                "a" * 64,
            ),
        )
    raw = path.read_bytes()
    return {
        "relative_path": "snapshots/legacy.sqlite",
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_count": len(raw),
    }


def _phase_e_database(root: Path, value: str = "1.1000") -> dict[str, object]:
    path = root / "snapshots/phase-e.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE nav_import_manifest (
                nav_import_manifest_id INTEGER PRIMARY KEY,
                contract_version INTEGER NOT NULL,
                nav_evidence_source_id INTEGER NOT NULL,
                instrument_id INTEGER NOT NULL,
                exact_isin TEXT NOT NULL,
                share_class_name TEXT NOT NULL,
                manifest_fingerprint TEXT NOT NULL,
                provider_instrument_id TEXT NOT NULL,
                import_status TEXT NOT NULL
            );
            CREATE TABLE nav_evidence_source (
                nav_evidence_source_id INTEGER PRIMARY KEY,
                source_fingerprint TEXT NOT NULL,
                source_governance TEXT NOT NULL
            );
            CREATE TABLE nav_observation_version (
                nav_observation_version_id INTEGER PRIMARY KEY,
                nav_import_manifest_id INTEGER NOT NULL,
                instrument_id INTEGER NOT NULL,
                exact_isin TEXT NOT NULL,
                observation_date TEXT NOT NULL,
                nav_decimal TEXT NOT NULL,
                currency_code TEXT NOT NULL,
                provider_observation_identity TEXT NOT NULL,
                raw_artifact_sha256 TEXT NOT NULL,
                observation_fingerprint TEXT NOT NULL,
                quality_status TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO nav_import_manifest VALUES (3,1,2,7,?,?,?,?,?)",
            (
                "HU0000000011",
                "Synthetic EUR Class",
                "b" * 64,
                "provider-7",
                "VALIDATED_ADMITTED",
            ),
        )
        connection.execute(
            "INSERT INTO nav_evidence_source VALUES (2,?,?)",
            ("e" * 64, "APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE"),
        )
        connection.execute(
            "INSERT INTO nav_observation_version VALUES (13,3,7,?,?,?,?,?,?,?,?)",
            (
                "HU0000000011",
                "2026-01-02",
                value,
                "EUR",
                "epoch-1",
                "c" * 64,
                "d" * 64,
                "ADMITTED_VALIDATED",
            ),
        )
    raw = path.read_bytes()
    return {
        "relative_path": "snapshots/phase-e.sqlite",
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_count": len(raw),
    }


def _candidate(
    root: Path,
    *,
    reference_type: ObservationReferenceType = ObservationReferenceType.RETAINED_RAW_JSON_V1,
    retained_value: str = "1.1000",
    corrected_value: str = "1.0900",
    document_locator: bool = False,
) -> tuple[dict[str, object], dict[str, Any]]:
    key = _key()
    if reference_type is ObservationReferenceType.RETAINED_RAW_JSON_V1:
        raw_artifact = _write_json(
            root,
            "sources/raw.json",
            {"series": [{"date": "2026-01-02", "value": retained_value}]},
        )
        observation_id = "observation:raw:1"
        retained: dict[str, object] = {
            "reference_type": reference_type.value,
            "observation_id": observation_id,
            "artifact": raw_artifact,
            "value_locator": "/series/0/value",
            "binding": _binding(
                root,
                observation_id=observation_id,
                reference_type=reference_type.value,
                source=raw_artifact,
                source_locator="/series/0/value",
                key=key,
                value=retained_value,
            ),
            "raw_value": retained_value,
        }
    elif reference_type is ObservationReferenceType.LEGACY_SQLITE_V1:
        database = _legacy_database(root, retained_value)
        observation_id = "observation:legacy:11"
        retained = {
            "reference_type": reference_type.value,
            "observation_id": observation_id,
            "database_snapshot": database,
            "row_id": 11,
            "source_provider": "synthetic_provider",
            "source_identifier": "series-7",
            "source_fingerprint": "a" * 64,
            "binding": _binding(
                root,
                observation_id=observation_id,
                reference_type=reference_type.value,
                source=database,
                source_locator="instrument_nav_observation/11",
                key=key,
                value=retained_value,
            ),
            "raw_value": retained_value,
        }
    else:
        database = _phase_e_database(root, retained_value)
        observation_id = "observation:phase-e:13"
        retained = {
            "reference_type": reference_type.value,
            "observation_id": observation_id,
            "database_snapshot": database,
            "row_id": 13,
            "manifest_fingerprint": "b" * 64,
            "observation_fingerprint": "d" * 64,
            "raw_artifact_sha256": "c" * 64,
            "source_fingerprint": "e" * 64,
            "provider_observation_identity": "epoch-1",
            "raw_value": retained_value,
        }

    if document_locator:
        correction_artifact = _write(
            root, "sources/correction.pdf", b"synthetic pdf bytes"
        )
        locator_type = "DOCUMENT_LOCATOR"
        locator = "PDF p.1 synthetic T180 row"
    else:
        correction_artifact = _write_json(
            root,
            "sources/correction.json",
            {
                "claims": [
                    {
                        **key,
                        "corrected_value": corrected_value,
                    }
                ]
            },
        )
        locator_type = "JSON_POINTER"
        locator = "/claims/0"
    publication_artifact = _write_json(
        root,
        "sources/publication.json",
        {"record": "synthetic publication binding"},
    )
    _, correction_event = _receipt(
        root,
        name="correction",
        role="ISSUER_CORRECTION",
        artifact=correction_artifact,
    )
    events = [correction_event]
    if reference_type is ObservationReferenceType.RETAINED_RAW_JSON_V1:
        _, raw_event = _receipt(
            root,
            name="raw",
            role="RETAINED_RAW_OBSERVATION",
            artifact=cast(dict[str, object], retained["artifact"]),
        )
        events.append(raw_event)
    _, publication_event = _receipt(
        root,
        name="publication",
        role="PUBLICATION_RECORD",
        artifact=publication_artifact,
    )
    events.append(publication_event)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "NAV_CORRECTION_EVIDENCE_CANDIDATE",
        "candidate_id": "candidate:synthetic:1",
        "key": key,
        "retained_observation": retained,
        "issuer_correction": {
            "correction_id": "correction:synthetic:1",
            "artifact": correction_artifact,
            "locator_type": locator_type,
            "locator": locator,
            "raw_value": corrected_value,
        },
        "acquisition_events": events,
        "publication_references": [
            {
                "publication_id": "publication:synthetic:1",
                "artifact": publication_artifact,
                "locator_type": "JSON_POINTER",
                "locator": "/record",
                "recorded_availability_bound_utc": "2026-01-04T23:59:59Z",
            }
        ],
        "correction_relationship": {
            "relationship_type": "CORRECTION_APPLIES_TO_KEY",
            "correction_id": "correction:synthetic:1",
            "target_observation_id": observation_id,
        },
        "provenance_limitations": [
            "synthetic documentary assertion is not issuer authentication"
        ],
    }
    candidate_ref = _write_json(root, "candidate.json", payload)
    return candidate_ref, payload


def _chart_candidate(
    root: Path,
    *,
    raw_value_token: str = "1.1897",
    raw_value: str = "1.1897",
    timestamp_token: str = "1767312000000",
    duplicate_title: bool = False,
) -> tuple[dict[str, object], dict[str, Any]]:
    key = _key(
        share_class="Synthetic EUR T180",
        valuation_date="2026-01-02",
    )
    chart_bytes = (
        '{"decimals":6,"id":"11002","instrument_id":"11002",'
        '"isin":"HU0000000011","last_close":"1.2000","series":'
        f'[[{timestamp_token},{raw_value_token}]],"ticker":"Synthetic EUR T180",'
        '"title":"Synthetic EUR T180"'
        + (',"title":"Synthetic EUR T180"' if duplicate_title else "")
        + "}\n"
    ).encode()
    chart = _write(root, "chart/response.bin", chart_bytes)
    transport_document = {
        "body_complete": True,
        "byte_count": chart["byte_count"],
        "content_encoding": "gzip",
        "content_type": "text/html; charset=UTF-8",
        "final_url": "https://www.erstemarket.hu/funds/chart/11002",
        "http_status": 200,
        "max_response_bytes": 8 * 1024 * 1024,
        "provider": "ERSTE_MARKET_APPROVED_NAV",
        "raw_artifact_reference": chart["relative_path"],
        "raw_artifact_sha256": chart["raw_sha256"],
        "redirect_history": [],
        "request_role": "series",
        "requested_isin": "HU0000000011",
        "requested_url": "https://www.erstemarket.hu/funds/chart/11002",
        "response_headers": {
            "content-encoding": "gzip",
            "content-type": "text/html; charset=UTF-8",
            "date": "Sat, 03 Jan 2026 12:00:00 GMT",
        },
        "retention_status": "QUARANTINED_RESPONSE",
        "retrieval_timestamp": "2026-01-03T12:00:00+00:00",
        "schema_version": 1,
        "transport_error": None,
    }
    transport = _write_json(root, "chart/transport.json", transport_document)
    identity = _write(
        root,
        "chart/identity.html",
        b"<h1>Befektetesi alapok Synthetic EUR T180</h1>",
    )
    identity_document = {
        "byte_count": identity["byte_count"],
        "content_type": "text/html; charset=UTF-8",
        "http_status": 200,
        "provider": "ERSTE_MARKET_APPROVED_NAV",
        "raw_artifact_reference": identity["relative_path"],
        "raw_artifact_sha256": identity["raw_sha256"],
        "request_role": "identity",
        "request_url": (
            "https://www.erstemarket.hu/befektetesi_alapok/alap/HU0000000011"
        ),
        "requested_isin": "HU0000000011",
        "response_headers": {
            "content-encoding": "gzip",
            "content-type": "text/html; charset=UTF-8",
            "date": "Sat, 03 Jan 2026 11:59:59 GMT",
        },
        "retrieval_timestamp": "2026-01-03T11:59:59+00:00",
        "schema_version": 1,
    }
    identity_receipt = _write_json(
        root, "chart/identity-receipt.json", identity_document
    )
    assessment_core = {
        "assessment_scope": "IN_MEMORY_ONLY_NO_ARTIFACT_OR_DATABASE_ADMISSION",
        "currency": "EUR",
        "dataset_fingerprint": "a" * 64,
        "first_observation_date": "2026-01-02",
        "instrument_id": "11002",
        "isin": "HU0000000011",
        "last_observation_date": "2026-01-02",
        "media_contract_version": 1,
        "normalized_media_type": "text/html; charset=utf-8",
        "observation_count": 1,
        "provider": "ERSTE_MARKET_APPROVED_NAV",
        "raw_artifact_sha256": chart["raw_sha256"],
        "receipt_sha256": transport["raw_sha256"],
        "source_governance": "APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE",
        "transport_classification": "QUARANTINED_REJECTED_RESPONSE",
    }
    semantic = _write_json(
        root,
        "chart/semantic.json",
        {
            "assessment": {
                **assessment_core,
                "assessment_fingerprint": canonical_fingerprint(assessment_core),
                "semantic_status": "SEMANTIC_ADMISSIBLE_IN_MEMORY_ONLY",
            },
            "raw_artifact_reference": chart["relative_path"],
            "raw_artifact_sha256": chart["raw_sha256"],
            "receipt_type": "ERSTE_MARKET_CHART_SEMANTIC_ADMISSION",
            "schema_version": 1,
            "transport_receipt_reference": transport["relative_path"],
            "transport_receipt_sha256": transport["raw_sha256"],
        },
    )
    observation_id = "observation:chart:1"
    retained = {
        "reference_type": "RETAINED_RAW_JSON_V1",
        "observation_id": observation_id,
        "artifact": chart,
        "series_locator": "/series/0",
        "provider_observation_identity": "1767312000000",
        "transport_receipt": transport,
        "semantic_receipt": semantic,
        "identity_artifact": identity,
        "identity_receipt": identity_receipt,
        "binding": _binding(
            root,
            observation_id=observation_id,
            reference_type="RETAINED_RAW_JSON_V1",
            source=chart,
            source_locator="/series/0",
            key=key,
            value=raw_value,
        ),
        "raw_value": raw_value,
    }
    correction = _write_json(
        root,
        "sources/chart-correction.json",
        {"claims": [{**key, "corrected_value": "1.1885"}]},
    )
    publication = _write_json(root, "sources/chart-publication.json", {"record": "ok"})
    _, correction_event = _receipt(
        root, name="chart-correction", role="ISSUER_CORRECTION", artifact=correction
    )
    _, publication_event = _receipt(
        root, name="chart-publication", role="PUBLICATION_RECORD", artifact=publication
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "NAV_CORRECTION_EVIDENCE_CANDIDATE",
        "candidate_id": "candidate:chart:1",
        "key": key,
        "retained_observation": retained,
        "issuer_correction": {
            "correction_id": "correction:chart:1",
            "artifact": correction,
            "locator_type": "JSON_POINTER",
            "locator": "/claims/0",
            "raw_value": "1.1885",
        },
        "acquisition_events": [
            correction_event,
            {
                "event_id": "acq:chart",
                "artifact_role": "RETAINED_RAW_OBSERVATION",
                "artifact": chart,
                "receipt": transport,
                "timestamp_locator": "/retrieval_timestamp",
                "retrieved_at_utc": "2026-01-03T12:00:00Z",
            },
            {
                "event_id": "acq:identity",
                "artifact_role": "RETAINED_IDENTITY",
                "artifact": identity,
                "receipt": identity_receipt,
                "timestamp_locator": "/retrieval_timestamp",
                "retrieved_at_utc": "2026-01-03T11:59:59Z",
            },
            publication_event,
        ],
        "publication_references": [
            {
                "publication_id": "publication:chart:1",
                "artifact": publication,
                "locator_type": "JSON_POINTER",
                "locator": "/record",
                "recorded_availability_bound_utc": "2026-01-04T23:59:59Z",
            }
        ],
        "correction_relationship": {
            "relationship_type": "CORRECTION_APPLIES_TO_KEY",
            "correction_id": "correction:chart:1",
            "target_observation_id": observation_id,
        },
        "provenance_limitations": [
            "synthetic chart identity is not issuer authentication"
        ],
    }
    return _write_json(root, "chart-candidate.json", payload), payload


def _inspect(root: Path, reference: dict[str, object]):
    return inspect_nav_correction_candidate(
        repository_root=root,
        candidate_reference=ArtifactReference(**reference),  # type: ignore[arg-type]
    )


def _use_distribution_evidence_receipts(root: Path, payload: dict[str, Any]) -> None:
    for event in payload["acquisition_events"]:
        event["receipt"] = _distribution_evidence_receipt(
            root,
            name=cast(str, event["event_id"]).replace(":", "-"),
            artifact=cast(dict[str, object], event["artifact"]),
            timestamp=cast(str, event["retrieved_at_utc"]),
        )


def _rewrite_receipt(
    root: Path, event: dict[str, object], update: dict[str, object]
) -> dict[str, object]:
    receipt = cast(dict[str, object], event["receipt"])
    relative = cast(str, receipt["relative_path"])
    document = cast(dict[str, object], json.loads((root / relative).read_text()))
    document.update(update)
    replacement = _write_json(root, relative, document)
    event["receipt"] = replacement
    return document


@pytest.mark.parametrize("reference_type", list(ObservationReferenceType))
def test_supported_reference_types_validate_structurally(
    tmp_path: Path, reference_type: ObservationReferenceType
) -> None:
    candidate, _ = _candidate(tmp_path, reference_type=reference_type)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is True, result.validation_errors
    assert result.validation_errors == ()
    assert result.candidate is not None
    assert result.candidate.retained_observation.reference_type is reference_type
    assert result.admitted is False
    assert result.operational_eligibility_granted is False
    assert result.persistent_or_operational_state_changed is False


def test_retained_erste_chart_chain_validates_structurally(tmp_path: Path) -> None:
    candidate, _ = _chart_candidate(tmp_path)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is True, result.validation_errors
    assert any("Erste Market chart" in item for item in result.verified_bindings)
    assert result.issuer_authenticated is False
    assert result.historical_availability_established is False
    assert result.admitted is False
    assert result.operational_eligibility_granted is False


def test_erste_chart_decimal_tokens_are_exact_and_context_independent(
    tmp_path: Path,
) -> None:
    candidate, _ = _chart_candidate(
        tmp_path, raw_value_token="1.1897000e0", raw_value="1.1897"
    )

    with localcontext() as context:
        context.prec = 2
        result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is True, result.validation_errors
    assert result.candidate is not None
    assert result.candidate.retained_observation.decimal_value == Decimal("1.1897")


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"timestamp_token": '"1767312000000"'}, "timestamp must be"),
        ({"raw_value_token": "NaN"}, "malformed strict chart JSON"),
        ({"duplicate_title": True}, "malformed strict chart JSON"),
    ],
)
def test_erste_chart_rejects_invalid_source_tokens(
    tmp_path: Path, changes: dict[str, object], expected: str
) -> None:
    candidate, _ = _chart_candidate(tmp_path, **changes)  # type: ignore[arg-type]

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is False
    assert expected in result.validation_errors[0]


def test_erste_chart_epoch_identity_and_date_are_checked(tmp_path: Path) -> None:
    candidate, payload = _chart_candidate(tmp_path)
    payload["retained_observation"]["provider_observation_identity"] = "1767312000001"
    candidate = _write_json(tmp_path, "wrong-provider-identity.json", payload)
    assert _inspect(tmp_path, candidate).validation_errors == (
        "chart provider observation identity differs from occurrence",
    )

    candidate, payload = _chart_candidate(tmp_path / "date")
    payload["key"]["valuation_date"] = "2026-01-03"
    candidate = _write_json(tmp_path / "date", "wrong-chart-date.json", payload)
    assert _inspect(tmp_path / "date", candidate).validation_errors == (
        "chart valuation date differs from candidate",
    )


def test_erste_chart_transport_timestamp_and_locator_are_checked(
    tmp_path: Path,
) -> None:
    candidate, payload = _chart_candidate(tmp_path)
    chart_event = next(
        event
        for event in payload["acquisition_events"]
        if event["event_id"] == "acq:chart"
    )
    chart_event["retrieved_at_utc"] = "2026-01-03T12:00:01Z"
    candidate = _write_json(tmp_path, "wrong-chart-timestamp.json", payload)
    assert _inspect(tmp_path, candidate).validation_errors == (
        "Erste Market transport receipt timestamp differs from event",
    )

    candidate, payload = _chart_candidate(tmp_path / "locator")
    chart_event = next(
        event
        for event in payload["acquisition_events"]
        if event["event_id"] == "acq:chart"
    )
    chart_event["timestamp_locator"] = "/retrieved_at_utc"
    candidate = _write_json(tmp_path / "locator", "wrong-chart-locator.json", payload)
    assert _inspect(tmp_path / "locator", candidate).validation_errors == (
        "timestamp_locator does not match Erste Market transport profile",
    )


def test_erste_chart_transport_artifact_binding_is_checked(tmp_path: Path) -> None:
    candidate, payload = _chart_candidate(tmp_path)
    retained = cast(dict[str, object], payload["retained_observation"])
    transport_ref = cast(dict[str, object], retained["transport_receipt"])
    transport_path = tmp_path / cast(str, transport_ref["relative_path"])
    transport = cast(dict[str, object], json.loads(transport_path.read_text()))
    transport["raw_artifact_sha256"] = "f" * 64
    replacement = _write_json(
        tmp_path, cast(str, transport_ref["relative_path"]), transport
    )
    retained["transport_receipt"] = replacement
    chart_event = next(
        event
        for event in payload["acquisition_events"]
        if event["event_id"] == "acq:chart"
    )
    chart_event["receipt"] = replacement
    candidate = _write_json(tmp_path, "wrong-chart-artifact-binding.json", payload)

    assert _inspect(tmp_path, candidate).validation_errors == (
        "Erste Market transport receipt artifact binding differs from event",
    )


def test_erste_chart_semantic_chain_and_full_key_binding_are_checked(
    tmp_path: Path,
) -> None:
    candidate, payload = _chart_candidate(tmp_path)
    retained = cast(dict[str, object], payload["retained_observation"])
    semantic_ref = cast(dict[str, object], retained["semantic_receipt"])
    semantic_path = tmp_path / cast(str, semantic_ref["relative_path"])
    semantic = cast(dict[str, object], json.loads(semantic_path.read_text()))
    semantic["raw_artifact_sha256"] = "f" * 64
    retained["semantic_receipt"] = _write_json(
        tmp_path, cast(str, semantic_ref["relative_path"]), semantic
    )
    candidate = _write_json(tmp_path, "broken-semantic-chain.json", payload)
    assert _inspect(tmp_path, candidate).validation_errors == (
        "Erste Market semantic receipt chain differs from candidate",
    )

    candidate, payload = _chart_candidate(tmp_path / "binding")
    retained = cast(dict[str, object], payload["retained_observation"])
    binding_ref = cast(dict[str, object], retained["binding"])
    binding_artifact = cast(dict[str, object], binding_ref["artifact"])
    binding_path = tmp_path / "binding" / cast(str, binding_artifact["relative_path"])
    binding = cast(dict[str, object], json.loads(binding_path.read_text()))
    cast(dict[str, object], cast(list[object], binding["bindings"])[0])["raw_value"] = (
        "1.1800"
    )
    binding_ref["artifact"] = _write_json(
        tmp_path / "binding", cast(str, binding_artifact["relative_path"]), binding
    )
    candidate = _write_json(tmp_path / "binding", "broken-chart-binding.json", payload)
    assert _inspect(tmp_path / "binding", candidate).validation_errors == (
        "observation binding differs from candidate",
    )


def test_erste_chart_requires_the_explicit_receipts_in_acquisition_events(
    tmp_path: Path,
) -> None:
    candidate, payload = _chart_candidate(tmp_path)
    retained = cast(dict[str, object], payload["retained_observation"])
    transport_ref = cast(dict[str, object], retained["transport_receipt"])
    original = (tmp_path / cast(str, transport_ref["relative_path"])).read_bytes()
    copied_receipt = _write(tmp_path, "chart/transport-copy.json", original)
    chart_event = next(
        event
        for event in payload["acquisition_events"]
        if event["event_id"] == "acq:chart"
    )
    chart_event["receipt"] = copied_receipt
    candidate = _write_json(tmp_path, "wrong-chain-event.json", payload)

    assert _inspect(tmp_path, candidate).validation_errors == (
        "chart transport receipt is not the retained-observation acquisition event",
    )


def test_erste_chart_receipts_reject_unknown_or_mixed_envelopes(
    tmp_path: Path,
) -> None:
    candidate, payload = _chart_candidate(tmp_path)
    retained = cast(dict[str, object], payload["retained_observation"])
    transport_ref = cast(dict[str, object], retained["transport_receipt"])
    transport_path = tmp_path / cast(str, transport_ref["relative_path"])
    transport = cast(dict[str, object], json.loads(transport_path.read_text()))
    transport["record_type"] = "NAV_CORRECTION_ACQUISITION_RECEIPT"
    replacement = _write_json(
        tmp_path, cast(str, transport_ref["relative_path"]), transport
    )
    retained["transport_receipt"] = replacement
    chart_event = next(
        event
        for event in payload["acquisition_events"]
        if event["event_id"] == "acq:chart"
    )
    chart_event["receipt"] = replacement
    candidate = _write_json(tmp_path, "mixed-chart-receipt.json", payload)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is False
    assert "Erste Market transport receipt fields differ" in result.validation_errors[0]


def test_retained_distribution_evidence_receipts_validate_structurally(
    tmp_path: Path,
) -> None:
    candidate, payload = _candidate(
        tmp_path, reference_type=ObservationReferenceType.PHASE_E_SQLITE_V1
    )
    _use_distribution_evidence_receipts(tmp_path, payload)
    candidate = _write_json(tmp_path, "distribution-receipts.json", payload)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is True, result.validation_errors
    assert result.validation_errors == ()
    assert any(
        "retained distribution-evidence receipt" in binding
        for binding in result.verified_bindings
    )
    assert result.issuer_authenticated is False
    assert result.historical_availability_established is False
    assert result.admitted is False
    assert result.operational_eligibility_granted is False


def test_modern_receipt_profile_remains_distinct_and_valid(tmp_path: Path) -> None:
    candidate, _ = _candidate(
        tmp_path, reference_type=ObservationReferenceType.PHASE_E_SQLITE_V1
    )

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is True, result.validation_errors
    assert any(
        binding.startswith("receipt-bound acquisition event")
        for binding in result.verified_bindings
    )
    assert not any(
        "retained distribution-evidence receipt" in binding
        for binding in result.verified_bindings
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("record_type", "OTHER_RECEIPT", "unsupported acquisition receipt record_type"),
        ("record_type", 7, "receipt record_type must be non-empty text"),
        ("schema_version", 2, "unsupported acquisition receipt schema_version"),
        ("schema_version", True, "unsupported acquisition receipt schema_version"),
        ("schema_version", "1", "unsupported acquisition receipt schema_version"),
    ],
)
def test_distribution_receipt_rejects_wrong_discriminator_or_version(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    candidate, payload = _candidate(
        tmp_path, reference_type=ObservationReferenceType.PHASE_E_SQLITE_V1
    )
    _use_distribution_evidence_receipts(tmp_path, payload)
    event = cast(dict[str, object], payload["acquisition_events"][0])
    _rewrite_receipt(tmp_path, event, {field: value})
    candidate = _write_json(tmp_path, f"bad-receipt-{field}.json", payload)

    assert _inspect(tmp_path, candidate).validation_errors == (expected,)


@pytest.mark.parametrize("defect", ["missing", "extra", "mixed"])
def test_distribution_receipt_rejects_unsupported_or_mixed_fields(
    tmp_path: Path, defect: str
) -> None:
    candidate, payload = _candidate(
        tmp_path, reference_type=ObservationReferenceType.PHASE_E_SQLITE_V1
    )
    _use_distribution_evidence_receipts(tmp_path, payload)
    event = cast(dict[str, object], payload["acquisition_events"][0])
    receipt = cast(dict[str, object], event["receipt"])
    relative = cast(str, receipt["relative_path"])
    document = cast(dict[str, object], json.loads((tmp_path / relative).read_text()))
    if defect == "missing":
        cast(dict[str, object], document["artifact"]).pop("sha256")
        expected = "distribution-evidence acquisition receipt artifact fields differ"
    elif defect == "extra":
        document["metadata"] = {"claim": "not permitted"}
        expected = "acquisition receipt fields differ"
    else:
        cast(dict[str, object], document["artifact"])["relative_path"] = cast(
            dict[str, object], event["artifact"]
        )["relative_path"]
        expected = "mixed acquisition receipt artifact profiles"
    event["receipt"] = _write_json(tmp_path, relative, document)
    candidate = _write_json(tmp_path, f"bad-receipt-fields-{defect}.json", payload)

    assert expected in _inspect(tmp_path, candidate).validation_errors[0]


@pytest.mark.parametrize(
    "defect",
    ["path", "hash", "timestamp", "malformed_timestamp", "timestamp_type", "locator"],
)
def test_distribution_receipt_rejects_binding_or_timestamp_mismatch(
    tmp_path: Path, defect: str
) -> None:
    candidate, payload = _candidate(
        tmp_path, reference_type=ObservationReferenceType.PHASE_E_SQLITE_V1
    )
    _use_distribution_evidence_receipts(tmp_path, payload)
    event = cast(dict[str, object], payload["acquisition_events"][0])
    if defect == "locator":
        event["timestamp_locator"] = "/retrieval_timestamp"
        expected = "timestamp_locator does not match acquisition receipt profile"
    else:
        receipt = cast(dict[str, object], event["receipt"])
        relative = cast(str, receipt["relative_path"])
        document = cast(
            dict[str, object], json.loads((tmp_path / relative).read_text())
        )
        if defect == "path":
            cast(dict[str, object], document["artifact"])["path"] = "sources/other.json"
            expected = "distribution-evidence acquisition receipt artifact binding differs from event"
        elif defect == "hash":
            cast(dict[str, object], document["artifact"])["sha256"] = "f" * 64
            expected = "distribution-evidence acquisition receipt artifact binding differs from event"
        elif defect == "timestamp":
            document["retrieved_at_utc"] = "2026-01-03T12:00:01Z"
            expected = "acquisition receipt timestamp differs from event"
        elif defect == "malformed_timestamp":
            document["retrieved_at_utc"] = "2026-01-03T12:00:00+00:00"
            expected = "receipt retrieved_at_utc must be canonical UTC text ending Z"
        else:
            document["retrieved_at_utc"] = 7
            expected = "receipt retrieved_at_utc must be non-empty text"
        event["receipt"] = _write_json(tmp_path, relative, document)
    candidate = _write_json(tmp_path, f"bad-receipt-binding-{defect}.json", payload)

    assert _inspect(tmp_path, candidate).validation_errors == (expected,)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("path", 7, "receipt artifact path must be non-empty text"),
        ("sha256", 7, "receipt artifact sha256 must be non-empty text"),
    ],
)
def test_distribution_receipt_rejects_invalid_artifact_types(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    candidate, payload = _candidate(
        tmp_path, reference_type=ObservationReferenceType.PHASE_E_SQLITE_V1
    )
    _use_distribution_evidence_receipts(tmp_path, payload)
    event = cast(dict[str, object], payload["acquisition_events"][0])
    receipt = cast(dict[str, object], event["receipt"])
    relative = cast(str, receipt["relative_path"])
    document = cast(dict[str, object], json.loads((tmp_path / relative).read_text()))
    cast(dict[str, object], document["artifact"])[field] = value
    event["receipt"] = _write_json(tmp_path, relative, document)
    candidate = _write_json(tmp_path, f"bad-receipt-type-{field}.json", payload)

    assert _inspect(tmp_path, candidate).validation_errors == (expected,)


def test_distribution_receipt_rejects_duplicate_keys_and_tampering(
    tmp_path: Path,
) -> None:
    candidate, payload = _candidate(
        tmp_path, reference_type=ObservationReferenceType.PHASE_E_SQLITE_V1
    )
    _use_distribution_evidence_receipts(tmp_path, payload)
    event = cast(dict[str, object], payload["acquisition_events"][0])
    receipt = cast(dict[str, object], event["receipt"])
    relative = cast(str, receipt["relative_path"])
    original = (tmp_path / relative).read_bytes()
    duplicated = original.replace(
        b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1
    )
    event["receipt"] = _write(tmp_path, relative, duplicated)
    candidate = _write_json(tmp_path, "duplicate-receipt.json", payload)
    assert "malformed strict JSON" in _inspect(tmp_path, candidate).validation_errors[0]

    candidate, payload = _candidate(
        tmp_path / "tampered", reference_type=ObservationReferenceType.PHASE_E_SQLITE_V1
    )
    _use_distribution_evidence_receipts(tmp_path / "tampered", payload)
    event = cast(dict[str, object], payload["acquisition_events"][0])
    receipt = cast(dict[str, object], event["receipt"])
    path = tmp_path / "tampered" / cast(str, receipt["relative_path"])
    path.write_bytes(path.read_bytes() + b" ")
    candidate = _write_json(tmp_path / "tampered", "tampered-receipt.json", payload)
    assert _inspect(tmp_path / "tampered", candidate).validation_errors == (
        "acquisition receipt acq:correction byte count mismatch",
    )


def test_unknown_reference_type_is_explicitly_rejected(tmp_path: Path) -> None:
    candidate, payload = _candidate(tmp_path)
    payload["retained_observation"]["reference_type"] = "MAGICAL_DATABASE_V9"
    candidate = _write_json(tmp_path, "unsupported.json", payload)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is False
    assert result.unsupported_reference_types == ("MAGICAL_DATABASE_V9",)
    assert result.validation_errors == (
        "unsupported observation reference_type: MAGICAL_DATABASE_V9",
    )


def test_phase_e_profile_rejects_non_admitted_source_semantics(tmp_path: Path) -> None:
    candidate, payload = _candidate(
        tmp_path, reference_type=ObservationReferenceType.PHASE_E_SQLITE_V1
    )
    snapshot = payload["retained_observation"]["database_snapshot"]
    path = tmp_path / cast(str, snapshot["relative_path"])
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE nav_evidence_source SET source_governance='UNREVIEWED_SOURCE'"
        )
    raw = path.read_bytes()
    snapshot["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    snapshot["byte_count"] = len(raw)
    candidate = _write_json(tmp_path, "false-phase-e.json", payload)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is False
    assert result.validation_errors == (
        "Phase E observation binding differs from candidate",
    )


@pytest.mark.parametrize("defect", ["hash", "byte_count"])
def test_tampered_artifact_and_byte_count_fail(tmp_path: Path, defect: str) -> None:
    candidate, payload = _candidate(tmp_path)
    artifact = payload["issuer_correction"]["artifact"]
    if defect == "hash":
        artifact["raw_sha256"] = "f" * 64
    else:
        artifact["byte_count"] += 1
    candidate = _write_json(tmp_path, f"bad-{defect}.json", payload)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is False
    expected = "raw SHA-256" if defect == "hash" else "byte count"
    assert expected in result.validation_errors[0]


def test_dangling_locator_and_relationship_fail_precisely(tmp_path: Path) -> None:
    candidate, payload = _candidate(tmp_path)
    payload["retained_observation"]["value_locator"] = "/series/9/value"
    candidate = _write_json(tmp_path, "dangling-locator.json", payload)
    assert _inspect(tmp_path, candidate).validation_errors == (
        "raw observation value locator is dangling",
    )

    candidate, payload = _candidate(tmp_path / "relation")
    payload["correction_relationship"]["target_observation_id"] = "missing:1"
    candidate = _write_json(tmp_path / "relation", "dangling-relation.json", payload)
    assert _inspect(tmp_path / "relation", candidate).validation_errors == (
        "observation relationship target is dangling",
    )

    candidate, payload = _candidate(tmp_path / "publication")
    payload["publication_references"][0]["locator"] = "/missing"
    candidate = _write_json(
        tmp_path / "publication", "dangling-publication.json", payload
    )
    assert _inspect(tmp_path / "publication", candidate).validation_errors == (
        "publication locator locator is dangling",
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("isin", "HU0000000022", "observation binding differs from candidate"),
        ("valuation_date", "2026-01-03", "observation binding differs from candidate"),
        ("currency", "HUF", "observation binding differs from candidate"),
        ("value_type", "PRICE", "value_type must be NAV"),
    ],
)
def test_identity_scope_mismatches_fail(
    tmp_path: Path, field: str, value: str, expected: str
) -> None:
    candidate, payload = _candidate(tmp_path)
    payload["key"][field] = value
    candidate = _write_json(tmp_path, f"bad-{field}.json", payload)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is False
    assert result.validation_errors == (expected,)


def test_structured_correction_value_mismatch_fails(tmp_path: Path) -> None:
    candidate, payload = _candidate(tmp_path)
    payload["issuer_correction"]["raw_value"] = "1.0800"
    candidate = _write_json(tmp_path, "wrong-correction.json", payload)

    assert _inspect(tmp_path, candidate).validation_errors == (
        "structured correction claim differs from candidate",
    )


@pytest.mark.parametrize("bad_version", [True, "1"])
def test_exact_schema_types_are_required(tmp_path: Path, bad_version: object) -> None:
    candidate, payload = _candidate(tmp_path)
    payload["schema_version"] = bad_version
    candidate = _write_json(tmp_path, "bad-version.json", payload)

    assert _inspect(tmp_path, candidate).validation_errors == (
        "unsupported candidate schema_version",
    )


def test_duplicate_keys_and_nonfinite_values_are_rejected(tmp_path: Path) -> None:
    candidate, _ = _candidate(tmp_path)
    path = tmp_path / cast(str, candidate["relative_path"])
    raw = (
        path.read_text()
        .replace('"schema_version":1', '"schema_version":1,"schema_version":1', 1)
        .encode()
    )
    duplicate = _write(tmp_path, "duplicate.json", raw)
    assert "malformed strict JSON" in _inspect(tmp_path, duplicate).validation_errors[0]

    raw = (
        path.read_text().replace('"raw_value":"1.1000"', '"raw_value":NaN', 1).encode()
    )
    nonfinite = _write(tmp_path, "nonfinite.json", raw)
    assert "malformed strict JSON" in _inspect(tmp_path, nonfinite).validation_errors[0]


@pytest.mark.parametrize("value", ["0", "-1", "Infinity", "NaN"])
def test_nav_values_must_be_finite_positive_decimal_text(
    tmp_path: Path, value: str
) -> None:
    candidate, payload = _candidate(tmp_path)
    payload["retained_observation"]["raw_value"] = value
    candidate = _write_json(tmp_path, "invalid-value.json", payload)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is False
    assert "must be finite and positive" in result.validation_errors[0]


def test_unsafe_path_and_parent_symlink_are_rejected(tmp_path: Path) -> None:
    candidate, _ = _candidate(tmp_path)
    escaped = dict(candidate)
    escaped["relative_path"] = "../candidate.json"
    with pytest.raises(
        ValueError,
        match="artifact reference must be normalized and repository-relative",
    ):
        ArtifactReference(**escaped)  # type: ignore[arg-type]

    outside = tmp_path.parent / "outside-correction-evidence"
    outside.mkdir(exist_ok=True)
    (outside / "candidate.json").write_text("{}")
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    symlinked = {
        "relative_path": "linked/candidate.json",
        "raw_sha256": hashlib.sha256(b"{}").hexdigest(),
        "byte_count": 2,
    }
    assert _inspect(tmp_path, symlinked).validation_errors == (
        "artifact path contains a symlink component",
    )


@pytest.mark.parametrize("defect", ["artifact", "timestamp", "locator"])
def test_receipt_binding_and_timestamp_mismatches_fail(
    tmp_path: Path, defect: str
) -> None:
    candidate, payload = _candidate(tmp_path)
    event = payload["acquisition_events"][0]
    if defect == "artifact":
        event["artifact"] = payload["publication_references"][0]["artifact"]
    elif defect == "timestamp":
        event["retrieved_at_utc"] = "2026-01-03T12:00:01Z"
    else:
        event["timestamp_locator"] = "/timestamp"
    candidate = _write_json(tmp_path, f"bad-receipt-{defect}.json", payload)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is False
    assert (
        "receipt" in result.validation_errors[0]
        or "timestamp_locator" in result.validation_errors[0]
    )


def test_acquisition_role_cannot_masquerade_as_another_artifact(
    tmp_path: Path,
) -> None:
    candidate, payload = _candidate(tmp_path)
    payload["acquisition_events"][0]["artifact_role"] = "PUBLICATION_RECORD"
    candidate = _write_json(tmp_path, "wrong-role.json", payload)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is False
    assert result.validation_errors == (
        "acquisition events do not bind every ISSUER_CORRECTION artifact",
    )


def test_same_value_preserves_relationship_without_invented_lineage(
    tmp_path: Path,
) -> None:
    candidate, _ = _candidate(
        tmp_path, retained_value="1.1000", corrected_value="1.1000"
    )

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is True
    assert result.correction_precedence_established is False
    assert any(
        "do not independently prove correction lineage" in item
        for item in result.unverified_semantic_claims
    )


@pytest.mark.parametrize(
    "reference_type",
    [
        ObservationReferenceType.RETAINED_RAW_JSON_V1,
        ObservationReferenceType.LEGACY_SQLITE_V1,
    ],
)
def test_raw_and_legacy_do_not_gain_phase_e_or_availability_status(
    tmp_path: Path, reference_type: ObservationReferenceType
) -> None:
    candidate, _ = _candidate(tmp_path, reference_type=reference_type)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is True
    assert result.historical_availability_established is False
    assert result.source_approved is False
    assert result.candidate is not None
    assert result.candidate.retained_observation.reference_type is reference_type
    assert "Phase E" not in " ".join(result.verified_bindings)


def test_document_locator_retains_unverified_semantics(tmp_path: Path) -> None:
    candidate, _ = _candidate(tmp_path, document_locator=True)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is True
    assert result.documentary_review_performed is False
    assert any(
        "prose/value meaning" in item for item in result.unverified_semantic_claims
    )


def test_results_are_nested_immutable_and_deterministic(tmp_path: Path) -> None:
    candidate, _ = _candidate(tmp_path)
    first = _inspect(tmp_path, candidate)
    second = _inspect(tmp_path, candidate)

    assert first == second
    assert isinstance(first.verified_bindings, tuple)
    assert first.candidate is not None
    assert isinstance(first.candidate.acquisition_events, tuple)
    assert first.__dataclass_params__.frozen is True


def test_internal_collections_are_deterministic_across_input_order(
    tmp_path: Path,
) -> None:
    candidate, payload = _candidate(tmp_path)
    first = _inspect(tmp_path, candidate)
    payload["acquisition_events"].reverse()
    reordered = _write_json(tmp_path, "candidate-reordered.json", payload)

    second = _inspect(tmp_path, reordered)

    assert first.structurally_valid is True
    assert second.structurally_valid is True
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.acquisition_events == second.candidate.acquisition_events
    assert first.verified_bindings == second.verified_bindings
    assert first.candidate_fingerprint != second.candidate_fingerprint


def test_structural_success_has_no_admission_or_operational_claims(
    tmp_path: Path,
) -> None:
    candidate, _ = _candidate(tmp_path)

    result = _inspect(tmp_path, candidate)

    assert result.structurally_valid is True
    assert result.source_approved is False
    assert result.documentary_review_performed is False
    assert result.issuer_authenticated is False
    assert result.historical_availability_established is False
    assert result.correction_precedence_established is False
    assert result.admitted is False
    assert result.operational_eligibility_granted is False
    assert result.persistent_or_operational_state_changed is False
