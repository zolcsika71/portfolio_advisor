from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from portfolio_advisor.canonical import canonical_json
from portfolio_advisor.database.schema.v3 import (
    SchemaVersionError,
    connect,
    initialize_schema,
    transaction,
    upgrade_schema_v3_nav_provenance_extension,
    validate_nav_provenance_schema,
)
from portfolio_advisor.history import nav_provenance as nav
from portfolio_advisor.history import nav_provenance_acquisition as acquisition


class _SyntheticResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
        url: str = "https://www.erstemarket.hu/funds/chart/11752",
        headers: dict[str, str] | None = None,
        history: list[_SyntheticResponse] | None = None,
    ) -> None:
        self._body = body
        self.status_code = status
        self.url = url
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.history = history or []
        self.closed = False

    def iter_content(self, chunk_size: int) -> list[bytes]:
        return [
            self._body[index : index + chunk_size]
            for index in range(0, len(self._body), chunk_size)
        ]

    def close(self) -> None:
        self.closed = True


def _timestamp(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)


def _target(path: Path) -> dict[str, nav.CohortMember]:
    members: dict[str, nav.CohortMember] = {}
    with connect(path) as connection:
        initialize_schema(connection)
        connection.execute(
            "INSERT INTO source_file(source_file_id,filename,sha256,source_type,source_date) "
            "VALUES (1,'phase-e.xls',?,'SHORTLIST_XLS','2026-08-26')",
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO source_sheet(source_sheet_id,source_file_id,sheet_name) "
            "VALUES (1,1,'shortlist')"
        )
        connection.execute(
            "INSERT INTO shortlist_snapshot(shortlist_snapshot_id,snapshot_date,source_sheet_id) "
            "VALUES (1,'2026-08-26',1)"
        )
        index = 0
        for currency in nav.PHASE_E_CURRENCIES:
            for isin in sorted(nav.PHASE_E_COHORT_ISINS[currency]):
                index += 1
                name = f"Exact Share Class {isin}"
                member = nav.CohortMember(
                    index, isin, name, currency, f"Asset {index}", f"Subasset {index}"
                )
                members[isin] = member
                connection.execute(
                    "INSERT INTO instrument(instrument_id,isin,canonical_name,base_currency_code) "
                    "VALUES (?,?,?,?)",
                    (index, isin, name, currency),
                )
                connection.execute(
                    "INSERT INTO shortlist_entry(shortlist_entry_id,shortlist_snapshot_id,"
                    "instrument_id,source_row_number,status) VALUES (?,1,?,?, 'SOURCE_REPORTED')",
                    (index, index, index + 1),
                )
                connection.execute(
                    """INSERT INTO shortlist_entry_source_occurrence(
                           shortlist_entry_source_occurrence_id,shortlist_snapshot_id,
                           instrument_id,source_sheet_id,source_row_number,
                           observed_product_name,observed_currency_code,observed_asset_class,
                           observed_sub_asset_class,source_payload_json,conflict_status
                       ) VALUES (?,1,?,1,?,?,?,?,?,?,'SOURCE_REPORTED')""",
                    (
                        index,
                        index,
                        index + 1,
                        name,
                        currency,
                        member.asset_class,
                        member.sub_asset_class,
                        canonical_json({"isin": isin}),
                    ),
                )
                connection.execute(
                    "INSERT INTO shortlist_entry_lineage(shortlist_entry_id,source_occurrence_id) "
                    "VALUES (?,?)",
                    (index, index),
                )
                connection.execute(
                    """INSERT INTO instrument_nav_observation(
                           instrument_id,observation_date,nav_value,currency_code,value_type,
                           source_provider,source_identifier,provenance_reference,quality_status,
                           source_fingerprint
                       ) VALUES (?,'2025-08-29',100,?,'NAV','legacy',?,'legacy','VALIDATED',?)""",
                    (index, currency, isin, "b" * 64),
                )
    return members


def _store_artifact(
    root: Path, *, isin: str, role: str, url: str, body: bytes
) -> dict[str, str]:
    raw_dir = root / "data/raw/nav/erste_market"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_sha = hashlib.sha256(body).hexdigest()
    raw_path = raw_dir / f"{raw_sha}.{'html' if role == 'identity' else 'json'}"
    raw_path.write_bytes(body)
    raw_reference = raw_path.relative_to(root).as_posix()
    receipt: dict[str, object] = {
        "byte_count": len(body),
        "content_type": "text/html; charset=utf-8" if role == "identity" else "application/json",
        "http_status": 200,
        "provider": nav.PHASE_E_SOURCE_CODE,
        "raw_artifact_reference": raw_reference,
        "raw_artifact_sha256": raw_sha,
        "request_role": role,
        "request_url": url,
        "requested_isin": isin,
        "response_headers": {},
        "retrieval_timestamp": "2026-09-03T12:00:00+00:00",
        "schema_version": 1,
    }
    receipt_bytes = (canonical_json(receipt) + "\n").encode()
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    receipt_path = raw_dir / f"{receipt_sha}.{role}.receipt.json"
    receipt_path.write_bytes(receipt_bytes)
    return {
        "raw_artifact_reference": raw_reference,
        "raw_artifact_sha256": raw_sha,
        "receipt_reference": receipt_path.relative_to(root).as_posix(),
        "receipt_sha256": receipt_sha,
    }


def _evidence(root: Path, target: Path) -> Path:
    members = nav.select_phase_e_cohorts(target)
    bundles: list[dict[str, object]] = []
    index = 0
    for currency in nav.PHASE_E_CURRENCIES:
        for member in members[currency]:
            index += 1
            instrument_id = str(1000 + index)
            identity_url = nav.PHASE_E_IDENTITY_URL.format(isin=member.isin)
            identity = (
                f'<html><h1>{member.share_class_name}</h1><div class="simpleChartContainer" '
                f'instrument-id="{instrument_id}"></div><p>ISIN: {member.isin}</p>'
                f'<p>Alap devizaneme {currency}</p></html>'
            ).encode()
            series_url = nav.PHASE_E_SERIES_URL.format(instrument_id=instrument_id)
            series = canonical_json(
                {
                    "instrument_id": instrument_id,
                    "isin": member.isin,
                    "series": [
                        [_timestamp(date(2025, 8, 29)), 100.00],
                        [_timestamp(date(2026, 2, 1)), 101.25],
                        [_timestamp(date(2026, 8, 31)), 102.50],
                    ],
                }
            ).encode()
            bundles.append(
                {
                    "currency": currency,
                    "identity": _store_artifact(
                        root, isin=member.isin, role="identity", url=identity_url, body=identity
                    ),
                    "isin": member.isin,
                    "series": _store_artifact(
                        root, isin=member.isin, role="series", url=series_url, body=series
                    ),
                }
            )
    value = {
        "bundles": bundles,
        "evidence_cutoff": nav.PHASE_E_CUTOFF.isoformat(),
        "provider": nav.PHASE_E_SOURCE_CODE,
        "schema_version": 1,
    }
    path = root / "data/raw/nav/erste_market/phase-e-index.json"
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    prepared = nav.prepare_bundles(repository_root=root, database_path=target, index_path=path)
    manifest_directory = path.parent / "manifests"
    manifest_directory.mkdir(parents=True, exist_ok=True)
    currency_manifest_references: dict[str, dict[str, str]] = {}
    for currency in nav.PHASE_E_CURRENCIES:
        entries = []
        for bundle in prepared:
            if bundle.member.currency != currency:
                continue
            entries.append(
                {
                    "dataset_fingerprint": bundle.dataset_fingerprint,
                    "identity_raw_reference": bundle.identity.raw_reference,
                    "identity_raw_sha256": bundle.identity.raw_sha256,
                    "identity_receipt_reference": bundle.identity.receipt_reference,
                    "identity_receipt_sha256": bundle.identity.receipt_sha256,
                    "isin": bundle.member.isin,
                    "semantic_receipt_reference": bundle.series.receipt_reference,
                    "semantic_receipt_sha256": bundle.series.receipt_sha256,
                    "series_raw_reference": bundle.series.raw_reference,
                    "series_raw_sha256": bundle.series.raw_sha256,
                }
            )
        payload = {
            "currency": currency,
            "instruments": entries,
            "manifest_type": "PHASE_E_CURRENCY_ACQUISITION_BUNDLE",
            "schema_version": 1,
            "source_governance": nav.ERSTE_MARKET_SOURCE_GOVERNANCE,
        }
        body = (canonical_json(payload) + "\n").encode()
        digest = hashlib.sha256(body).hexdigest()
        manifest = manifest_directory / f"{digest}.{currency.lower()}.bundle.manifest.json"
        manifest.write_bytes(body)
        currency_manifest_references[currency] = {
            "reference": manifest.relative_to(root).as_posix(),
            "sha256": digest,
        }
    combined = {
        "audit_contract": "MILESTONE_11C_PHASE_E_ACQUISITION_V1",
        "currency_manifests": currency_manifest_references,
        "schema_version": 1,
        "source_governance": nav.ERSTE_MARKET_SOURCE_GOVERNANCE,
    }
    combined_body = (canonical_json(combined) + "\n").encode()
    combined_digest = hashlib.sha256(combined_body).hexdigest()
    (manifest_directory / f"{combined_digest}.combined.acquisition.manifest.json").write_bytes(
        combined_body
    )
    return path


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    target = tmp_path / "target.sqlite"
    _target(target)
    return target, _evidence(tmp_path, target)


def test_valid_eur_huf_offline_import_is_exact_and_idempotent(tmp_path: Path) -> None:
    target, index = _fixture(tmp_path)
    logical_before = nav.logical_table_fingerprints(target, omit_phase_e=True)
    first = nav.import_phase_e_nav(repository_root=tmp_path, target=target, index_path=index)
    assert first.manifest_insert_count == 16
    assert first.observation_insert_count == 48
    assert dict(first.currency_dataset_fingerprints).keys() == {"EUR", "HUF"}
    first_bytes = target.read_bytes()
    replay = nav.import_phase_e_nav(repository_root=tmp_path, target=target, index_path=index)
    assert replay.manifest_insert_count == replay.observation_insert_count == 0
    assert target.read_bytes() == first_bytes
    assert nav.logical_table_fingerprints(target, omit_phase_e=True) == logical_before
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM nav_import_manifest").fetchone()[0] == 16
        assert connection.execute("SELECT count(*) FROM nav_observation_version").fetchone()[0] == 48
        assert connection.execute("SELECT count(*) FROM nav_observation_version WHERE typeof(nav_decimal)='text'").fetchone()[0] == 48
        assert connection.execute(
            "SELECT source_governance FROM nav_evidence_source"
        ).fetchone()[0] == nav.ERSTE_MARKET_SOURCE_GOVERNANCE


def test_exact_isin_wrong_currency_and_malformed_series_reject(tmp_path: Path) -> None:
    member = nav.CohortMember(1, "AT0000673322", "Fund", "EUR", "A", "B")
    good = canonical_json(
        {"isin": member.isin, "instrument_id": "42", "series": [
            [_timestamp(date(2025, 8, 29)), 1], [_timestamp(date(2026, 8, 31)), 2]
        ]}
    ).encode()
    assert len(nav._parse_series(good, member, "42")) == 2
    wrong = good.replace(member.isin.encode(), b"AT0000673314")
    with pytest.raises(nav.NavProvenanceError, match="exact ISIN"):
        nav._parse_series(wrong, member, "42")
    html = (
        f'<h1>Fund</h1><div class="simpleChartContainer" instrument-id="42"></div>'
        f'<p>ISIN: {member.isin}</p><p>Alap devizaneme HUF</p>'
    ).encode()
    with pytest.raises(nav.NavProvenanceError, match="currency"):
        nav._parse_identity(html, member)
    with pytest.raises(nav.NavProvenanceError, match="valid UTF-8 JSON"):
        nav._parse_series(b'{"isin":', member, "42")


@pytest.mark.parametrize(
    ("dates", "message"),
    [
        ((date(2026, 1, 1), date(2026, 8, 31)), "less than 365"),
        ((date(2024, 1, 1), date(2026, 7, 1)), "more than 30"),
    ],
)
def test_insufficient_or_stale_series_reject(
    dates: tuple[date, date], message: str
) -> None:
    member = nav.CohortMember(1, "AT0000673322", "Fund", "EUR", "A", "B")
    raw = canonical_json(
        {"isin": member.isin, "instrument_id": "42", "series": [
            [_timestamp(dates[0]), 1], [_timestamp(dates[1]), 2]
        ]}
    ).encode()
    with pytest.raises(nav.NavProvenanceError, match=message):
        nav._parse_series(raw, member, "42")


def test_duplicate_identical_and_conflicting_dates_reject() -> None:
    member = nav.CohortMember(1, "AT0000673322", "Fund", "EUR", "A", "B")
    for second_value in (1, 2):
        raw = canonical_json(
            {"isin": member.isin, "series": [
                [_timestamp(date(2025, 8, 29)), 1],
                [_timestamp(date(2025, 8, 29)), second_value],
                [_timestamp(date(2026, 8, 31)), 2],
            ]}
        ).encode()
        with pytest.raises(nav.NavProvenanceError, match="duplicate"):
            nav._parse_series(raw, member, "42")


def test_missing_corrupt_artifact_and_manifest_hash_mismatch_reject(tmp_path: Path) -> None:
    target, index = _fixture(tmp_path)
    payload = json.loads(index.read_text())
    raw = tmp_path / payload["bundles"][0]["series"]["raw_artifact_reference"]
    original = raw.read_bytes()
    raw.unlink()
    with pytest.raises(nav.NavProvenanceError, match="missing"):
        nav.prepare_bundles(repository_root=tmp_path, database_path=target, index_path=index)
    raw.write_bytes(original + b"corrupt")
    with pytest.raises(nav.NavProvenanceError, match="mismatch"):
        nav.prepare_bundles(repository_root=tmp_path, database_path=target, index_path=index)
    raw.write_bytes(original)
    payload["bundles"][0]["series"]["raw_artifact_sha256"] = "0" * 64
    index.write_text(canonical_json(payload) + "\n")
    with pytest.raises(nav.NavProvenanceError, match="reconcile"):
        nav.prepare_bundles(repository_root=tmp_path, database_path=target, index_path=index)


def test_external_bundle_manifest_mismatch_rejects_import(tmp_path: Path) -> None:
    target, index = _fixture(tmp_path)
    manifest = next((index.parent / "manifests").glob("*.eur.bundle.manifest.json"))
    payload = json.loads(manifest.read_text())
    payload["source_governance"] = "UNAPPROVED"
    manifest.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    with pytest.raises(nav.NavProvenanceError, match="content-addressed"):
        nav.import_phase_e_nav(repository_root=tmp_path, target=target, index_path=index)


def test_partial_or_damaged_schema_rejects(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite"
    _target(target)
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE nav_evidence_source(nav_evidence_source_id INTEGER)")
    with connect(target) as connection, pytest.raises(SchemaVersionError, match="partial"):
        upgrade_schema_v3_nav_provenance_extension(connection)
    target.unlink()
    _target(target)
    with connect(target) as connection:
        upgrade_schema_v3_nav_provenance_extension(connection)
        connection.execute("DROP TRIGGER nav_import_manifest_immutable_delete")
        with pytest.raises(SchemaVersionError, match="damaged"):
            validate_nav_provenance_schema(connection)


def test_validator_is_read_only_and_preserves_legacy_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, index = _fixture(tmp_path)
    legacy = tmp_path / "legacy.sqlite"
    shutil.copy2(target, legacy)
    count, isins, fingerprint = nav.legacy_nav_fingerprint(legacy)
    monkeypatch.setattr(nav, "LEGACY_NAV_OBSERVATION_COUNT", count)
    monkeypatch.setattr(nav, "LEGACY_NAV_ISIN_COUNT", isins)
    monkeypatch.setattr(nav, "LEGACY_NAV_DATASET_FINGERPRINT", fingerprint)
    nav.import_phase_e_nav(repository_root=tmp_path, target=target, index_path=index)
    before = target.read_bytes()
    first = nav.validate_phase_e_nav(
        repository_root=tmp_path, target=target, index_path=index, legacy_source=legacy
    )
    second = nav.validate_phase_e_nav(
        repository_root=tmp_path, target=target, index_path=index, legacy_source=legacy
    )
    assert first == second
    assert target.read_bytes() == before
    legacy_nav = first["legacy_nav"]
    assert isinstance(legacy_nav, dict)
    assert legacy_nav["dataset_fingerprint"] == fingerprint
    assert first["constructed_portfolio_row_counts"] == {
        "constructed_portfolio_holding_lineage": 0,
        "constructed_portfolio_metadata": 0,
    }


def test_validator_rejects_foreign_key_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, index = _fixture(tmp_path)
    legacy = tmp_path / "legacy.sqlite"
    shutil.copy2(target, legacy)
    count, isins, fingerprint = nav.legacy_nav_fingerprint(legacy)
    monkeypatch.setattr(nav, "LEGACY_NAV_OBSERVATION_COUNT", count)
    monkeypatch.setattr(nav, "LEGACY_NAV_ISIN_COUNT", isins)
    monkeypatch.setattr(nav, "LEGACY_NAV_DATASET_FINGERPRINT", fingerprint)
    nav.import_phase_e_nav(repository_root=tmp_path, target=target, index_path=index)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """INSERT INTO nav_observation_version(
                   nav_import_manifest_id,instrument_id,exact_isin,observation_date,
                   nav_decimal,currency_code,provider_observation_identity,
                   provider_revision_id,revision_sequence,supersedes_observation_id,
                   raw_artifact_sha256,quality_status,observation_fingerprint
               ) VALUES (999,1,'AT0000673322','2020-01-01','1','EUR','orphan',
                         NULL,1,NULL,?,'ADMITTED_VALIDATED',?)""",
            ("a" * 64, "b" * 64),
        )
    with pytest.raises(nav.NavProvenanceError, match="foreign_key_check"):
        nav.validate_phase_e_nav(
            repository_root=tmp_path, target=target, index_path=index, legacy_source=legacy
        )


def test_conflict_rejects_and_explicit_revision_appends(tmp_path: Path) -> None:
    target, index = _fixture(tmp_path)
    bundles = nav.prepare_bundles(
        repository_root=tmp_path, database_path=target, index_path=index
    )
    lineage = nav._external_bundle_lineage(
        repository_root=tmp_path, index_path=index, bundles=bundles
    )
    nav.import_phase_e_nav(repository_root=tmp_path, target=target, index_path=index)
    original = bundles[0]
    changed = replace(
        original,
        acquisition_identity="c" * 64,
        dataset_fingerprint="d" * 64,
        manifest_fingerprint="e" * 64,
        observations=(replace(original.observations[0], decimal_text="999"),),
    )
    with (
        connect(target) as connection,
        pytest.raises(nav.NavProvenanceError, match="replacement"),
        transaction(connection),
    ):
        nav._insert_bundle(connection, 1, changed, lineage[changed.member.currency])
    with sqlite3.connect(target) as connection:
        old_fingerprint = str(
            connection.execute(
                "SELECT observation_fingerprint FROM nav_observation_version "
                "WHERE instrument_id=? AND observation_date=?",
                (
                    original.member.instrument_id,
                    original.observations[0].observation_date.isoformat(),
                ),
            ).fetchone()[0]
        )
    revision = replace(
        changed,
        revision_semantics="EXPLICIT_REPLACEMENT",
        replaces_manifest_fingerprint=original.manifest_fingerprint,
        replacement_reason="provider-published correction fixture",
        observations=(
            replace(
                changed.observations[0],
                provider_identity="revision-2",
                provider_revision_id="R2",
                supersedes_fingerprint=old_fingerprint,
            ),
        ),
    )
    with connect(target) as connection, transaction(connection):
        assert nav._insert_bundle(connection, 1, revision, lineage[revision.member.currency]) == (1, 1)
    with sqlite3.connect(target) as connection:
        assert connection.execute(
            "SELECT count(*) FROM nav_observation_version WHERE instrument_id=? AND observation_date=?",
            (
                original.member.instrument_id,
                original.observations[0].observation_date.isoformat(),
            ),
        ).fetchone()[0] == 2


def test_candidate_build_preserves_every_preexisting_logical_table(tmp_path: Path) -> None:
    source, index = _fixture(tmp_path)
    before = nav.logical_table_fingerprints(source, omit_phase_e=True)
    candidate = tmp_path / "candidate.sqlite"
    result = nav.build_phase_e_candidate(
        repository_root=tmp_path, source=source, candidate=candidate, index_path=index
    )
    assert result["status"] == "PHASE_E_CANDIDATE_VALIDATED"
    assert nav.logical_table_fingerprints(candidate, omit_phase_e=True) == before


def _recovery_member() -> nav.CohortMember:
    return nav.CohortMember(1, "AT0000673322", "Fund", "EUR", "A", "B")


def _valid_recovery_body(
    *,
    instrument_id: str = "11752",
    isin: str = "AT0000673322",
    title: str = "Fund",
    series: list[list[object]] | None = None,
) -> bytes:
    return canonical_json(
        {
            "decimals": 6,
            "id": instrument_id,
            "instrument_id": instrument_id,
            "isin": isin,
            "last_close": "101.250",
            "series": series or [
                [_timestamp(date(2025, 8, 29)), 100.125],
                [_timestamp(date(2026, 8, 31)), 101.250],
            ],
            "ticker": title,
            "title": title,
        }
    ).encode()


def _capture_synthetic(
    tmp_path: Path,
    response: _SyntheticResponse,
    *,
    max_response_bytes: int = acquisition.MAX_RESPONSE_BYTES,
) -> acquisition.QuarantinedResponse:
    return acquisition._capture_response_to_quarantine(
        response,  # type: ignore[arg-type]
        repository_root=tmp_path,
        raw_directory=tmp_path / "data/raw/nav/erste_market",
        requested_url="https://www.erstemarket.hu/funds/chart/11752",
        requested_isin="AT0000673322",
        role="series",
        max_response_bytes=max_response_bytes,
        retrieval_timestamp="2026-09-03T15:00:00+00:00",
    )


def _classify_synthetic(
    captured: acquisition.QuarantinedResponse,
) -> tuple[str, str | None]:
    return acquisition._validate_quarantined_response(
        captured,
        member=_recovery_member(),
        role="series",
        requested_url="https://www.erstemarket.hu/funds/chart/11752",
        provider_instrument_id="11752",
    )


def test_recovery_boundary_preserves_valid_json_before_strict_validation(tmp_path: Path) -> None:
    response = _SyntheticResponse(_valid_recovery_body())
    captured = _capture_synthetic(tmp_path, response)
    assert response.closed
    assert _classify_synthetic(captured) == (acquisition.VALID_NAV_RESPONSE, None)
    assert (tmp_path / captured.raw_reference).read_bytes() == _valid_recovery_body()
    receipt = json.loads((tmp_path / captured.receipt_reference).read_text())
    assert receipt["retention_status"] == "QUARANTINED_RESPONSE"
    assert receipt["body_complete"] is True
    assert receipt["raw_artifact_sha256"] == captured.raw_sha256


@pytest.mark.parametrize(
    ("response", "expected_format", "expected_reason"),
    [
        (
            _SyntheticResponse(b"<html><body>temporary page</body></html>", content_type="text/html"),
            "HTML",
            "wrong media type",
        ),
        (
            _SyntheticResponse(
                b"<html><body>Access denied</body></html>",
                status=403,
                content_type="text/html",
            ),
            "ACCESS_DENIAL_PAGE",
            "unexpected HTTP status",
        ),
        (
            _SyntheticResponse(b'{"isin":', content_type="application/json"),
            "OTHER",
            "series artifact is not valid UTF-8 JSON",
        ),
        (
            _SyntheticResponse(_valid_recovery_body(), content_type="text/plain"),
            "JSON",
            "wrong media type",
        ),
    ],
)
def test_recovery_boundary_quarantines_html_denial_malformed_and_wrong_media(
    tmp_path: Path,
    response: _SyntheticResponse,
    expected_format: str,
    expected_reason: str,
) -> None:
    captured = _capture_synthetic(tmp_path, response)
    classification, reason = _classify_synthetic(captured)
    assert classification == acquisition.QUARANTINED_REJECTED_RESPONSE
    assert captured.response_format == expected_format
    assert reason == expected_reason
    assert (tmp_path / captured.raw_reference).read_bytes() == response._body
    assert (tmp_path / captured.receipt_reference).is_file()


def test_recovery_boundary_records_and_rejects_redirect(tmp_path: Path) -> None:
    redirect = _SyntheticResponse(
        b"",
        status=302,
        content_type="text/html",
        headers={"Location": "https://www.erstemarket.hu/funds/chart/11752/"},
    )
    response = _SyntheticResponse(
        _valid_recovery_body(),
        url="https://www.erstemarket.hu/funds/chart/11752/",
        history=[redirect],
    )
    captured = _capture_synthetic(tmp_path, response)
    assert _classify_synthetic(captured) == (
        acquisition.QUARANTINED_REJECTED_RESPONSE,
        "unexpected effective URL or redirect",
    )
    receipt = json.loads((tmp_path / captured.receipt_reference).read_text())
    assert receipt["redirect_history"] == [
        {
            "content_type": "text/html",
            "location": "https://www.erstemarket.hu/funds/chart/11752/",
            "status": 302,
            "url": "https://www.erstemarket.hu/funds/chart/11752",
        }
    ]


def test_recovery_boundary_limits_oversized_body_and_preserves_quarantine(tmp_path: Path) -> None:
    response = _SyntheticResponse(
        b"0123456789",
        headers={"Content-Length": "10", "Set-Cookie": "secret=never-record"},
    )
    captured = _capture_synthetic(tmp_path, response, max_response_bytes=8)
    assert captured.body == b"012345678"
    assert captured.body_complete is False
    assert _classify_synthetic(captured) == (
        acquisition.NETWORK_FAILURE,
        "response body was not obtained completely",
    )
    receipt = json.loads((tmp_path / captured.receipt_reference).read_text())
    assert receipt["byte_count"] == 9
    assert receipt["max_response_bytes"] == 8
    assert "set-cookie" not in receipt["response_headers"]
    assert (tmp_path / captured.raw_reference).read_bytes() == captured.body


def _semantic_fixture(
    tmp_path: Path,
    *,
    body: bytes | None = None,
    identity_currency: str = "EUR",
    requested_url: str = "https://www.erstemarket.hu/funds/chart/11752",
    final_url: str | None = None,
    redirect_history: list[dict[str, object]] | None = None,
) -> tuple[Path, Path, dict[str, str]]:
    target = tmp_path / "target.sqlite"
    members = _target(target)
    member = members["AT0000673322"]
    identity_url = nav.PHASE_E_IDENTITY_URL.format(isin=member.isin)
    identity_body = (
        f'<html><h1>Befektetési alapok {member.share_class_name}</h1>'
        '<div class="simpleChartContainer" instrument-id="11752"></div>'
        f'<p>ISIN: {member.isin}</p><p>Alap devizaneme {identity_currency}</p></html>'
    ).encode()
    identity = _store_artifact(
        tmp_path,
        isin=member.isin,
        role="identity",
        url=identity_url,
        body=identity_body,
    )
    raw = body or _valid_recovery_body(title=member.share_class_name)
    raw_sha = hashlib.sha256(raw).hexdigest()
    raw_path = tmp_path / "data/raw/nav/erste_market/quarantine" / f"{raw_sha}.response.bin"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw)
    raw_reference = raw_path.relative_to(tmp_path).as_posix()
    receipt = {
        "body_complete": True,
        "byte_count": len(raw),
        "content_encoding": "gzip",
        "content_type": "text/html; charset=UTF-8",
        "final_url": final_url or requested_url,
        "http_status": 200,
        "max_response_bytes": nav.PHASE_E_MAX_RESPONSE_BYTES,
        "provider": nav.PHASE_E_SOURCE_CODE,
        "raw_artifact_reference": raw_reference,
        "raw_artifact_sha256": raw_sha,
        "redirect_history": redirect_history or [],
        "request_role": "series",
        "requested_isin": member.isin,
        "requested_url": requested_url,
        "response_headers": {"content-type": "text/html; charset=UTF-8"},
        "retention_status": "QUARANTINED_RESPONSE",
        "retrieval_timestamp": "2026-09-03T16:33:18.654146+00:00",
        "schema_version": 1,
        "transport_error": None,
    }
    receipt_bytes = (canonical_json(receipt) + "\n").encode()
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    receipt_path = raw_path.parent / f"{receipt_sha}.quarantine.receipt.json"
    receipt_path.write_bytes(receipt_bytes)
    index = tmp_path / "data/raw/nav/erste_market/phase-e-index.json"
    index.write_text(
        canonical_json(
            {
                "bundles": [{"currency": "EUR", "identity": identity, "isin": member.isin}],
                "evidence_cutoff": nav.PHASE_E_CUTOFF.isoformat(),
                "provider": nav.PHASE_E_SOURCE_CODE,
                "schema_version": nav.PHASE_E_INDEX_SCHEMA_VERSION,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return target, index, {
        "raw_reference": raw_reference,
        "raw_sha256": raw_sha,
        "receipt_reference": receipt_path.relative_to(tmp_path).as_posix(),
        "receipt_sha256": receipt_sha,
    }


def _assess_semantic_fixture(
    tmp_path: Path, target: Path, index: Path, evidence: dict[str, str]
) -> dict[str, object]:
    return nav.assess_erste_market_quarantined_chart(
        repository_root=tmp_path,
        database_path=target,
        index_path=index,
        isin="AT0000673322",
        **evidence,
    )


def test_erste_media_quirk_is_explicit_semantic_only_and_immutable(tmp_path: Path) -> None:
    target, index, evidence = _semantic_fixture(tmp_path)
    raw = tmp_path / evidence["raw_reference"]
    receipt = tmp_path / evidence["receipt_reference"]
    before = (raw.read_bytes(), receipt.read_bytes())
    first = _assess_semantic_fixture(tmp_path, target, index, evidence)
    second = _assess_semantic_fixture(tmp_path, target, index, evidence)
    assert first == second
    assert first["semantic_status"] == "SEMANTIC_ADMISSIBLE_IN_MEMORY_ONLY"
    assert first["transport_classification"] == "QUARANTINED_REJECTED_RESPONSE"
    assert first["source_governance"] == "APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE"
    assert first["normalized_media_type"] == "text/html; charset=utf-8"
    assert first["observation_count"] == 2
    assert first["first_observation_date"] == "2025-08-29"
    assert first["last_observation_date"] == "2026-08-31"
    assert (raw.read_bytes(), receipt.read_bytes()) == before
    captured = _capture_synthetic(
        tmp_path / "global-strict",
        _SyntheticResponse(raw.read_bytes(), content_type="text/html; charset=UTF-8"),
    )
    assert _classify_synthetic(captured)[0] == acquisition.QUARANTINED_REJECTED_RESPONSE


def test_semantic_receipt_links_quarantine_without_mutating_transport(tmp_path: Path) -> None:
    target, index, evidence = _semantic_fixture(tmp_path)
    raw_path = tmp_path / evidence["raw_reference"]
    transport_path = tmp_path / evidence["receipt_reference"]
    before = (raw_path.read_bytes(), transport_path.read_bytes())
    captured = acquisition._retained_quarantined_response(
        repository_root=tmp_path,
        receipt_path=transport_path,
    )
    member = next(
        item
        for item in nav.select_phase_e_cohorts(target)["EUR"]
        if item.isin == "AT0000673322"
    )
    series, assessment = acquisition._semantic_series_entry(
        repository_root=tmp_path,
        database_path=target,
        raw_directory=tmp_path / "data/raw/nav/erste_market",
        index_path=index,
        member=member,
        captured=captured,
    )
    state = json.loads(index.read_text())
    state["bundles"][0]["series"] = series
    index.write_text(canonical_json(state) + "\n", encoding="utf-8")

    first = nav._evidence_from_entry(
        tmp_path, series, isin=member.isin, role="series"
    )
    second = nav._evidence_from_entry(
        tmp_path, series, isin=member.isin, role="series"
    )
    semantic = json.loads((tmp_path / series["receipt_reference"]).read_text())
    assert first == second
    assert first.raw_reference == evidence["raw_reference"]
    assert semantic["assessment"] == assessment
    assert semantic["transport_receipt_sha256"] == evidence["receipt_sha256"]
    assert semantic["receipt_type"] == "ERSTE_MARKET_CHART_SEMANTIC_ADMISSION"
    assert (raw_path.read_bytes(), transport_path.read_bytes()) == before


@pytest.mark.parametrize(
    ("requested_url", "final_url", "redirect_history", "body", "identity_currency", "message"),
    [
        (
            "https://other.example/funds/chart/11752",
            None,
            None,
            None,
            "EUR",
            "quarantined transport receipt",
        ),
        (
            "https://www.erstemarket.hu/not-chart/11752",
            None,
            None,
            None,
            "EUR",
            "quarantined transport receipt",
        ),
        (
            "https://www.erstemarket.hu/funds/chart/11752",
            "https://www.erstemarket.hu/funds/chart/11752/",
            [{"status": 302}],
            None,
            "EUR",
            "quarantined transport receipt",
        ),
        (
            "https://www.erstemarket.hu/funds/chart/11752",
            None,
            None,
            b"<html>{\"isin\":\"AT0000673322\"}</html>",
            "EUR",
            "whole UTF-8 JSON",
        ),
        (
            "https://www.erstemarket.hu/funds/chart/11752",
            None,
            None,
            b"<html><script>{\"isin\":\"AT0000673322\"}</script></html>",
            "EUR",
            "whole UTF-8 JSON",
        ),
        (
            "https://www.erstemarket.hu/funds/chart/11752",
            None,
            None,
            None,
            "HUF",
            "exact NAV currency",
        ),
    ],
)
def test_erste_media_contract_rejects_wrong_scope_html_and_identity(
    tmp_path: Path,
    requested_url: str,
    final_url: str | None,
    redirect_history: list[dict[str, object]] | None,
    body: bytes | None,
    identity_currency: str,
    message: str,
) -> None:
    target, index, evidence = _semantic_fixture(
        tmp_path,
        requested_url=requested_url,
        final_url=final_url,
        redirect_history=redirect_history,
        body=body,
        identity_currency=identity_currency,
    )
    with pytest.raises(nav.NavProvenanceError, match=message):
        _assess_semantic_fixture(tmp_path, target, index, evidence)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (_valid_recovery_body(instrument_id="11753", title="Exact Share Class AT0000673322"), "instrument ID"),
        (_valid_recovery_body(isin="AT0000673314", title="Exact Share Class AT0000673322"), "ISIN"),
        (
            _valid_recovery_body(
                title="Exact Share Class AT0000673322",
                series=[
                    [_timestamp(date(2025, 8, 29)), 100],
                    [_timestamp(date(2025, 8, 29)), 100],
                    [_timestamp(date(2026, 8, 31)), 101],
                ],
            ),
            "duplicate",
        ),
        (
            _valid_recovery_body(
                title="Exact Share Class AT0000673322",
                series=[
                    [_timestamp(date(2025, 8, 29)), "NaN"],
                    [_timestamp(date(2026, 8, 31)), 101],
                ],
            ),
            "finite and positive",
        ),
        (
            _valid_recovery_body(
                title="Exact Share Class AT0000673322",
                series=[["2025-08-29", 100], [_timestamp(date(2026, 8, 31)), 101]],
            ),
            "timestamp",
        ),
    ],
)
def test_erste_media_contract_rejects_schema_identity_duplicates_and_invalid_values(
    tmp_path: Path, body: bytes, message: str
) -> None:
    target, index, evidence = _semantic_fixture(tmp_path, body=body)
    with pytest.raises(nav.NavProvenanceError, match=message):
        _assess_semantic_fixture(tmp_path, target, index, evidence)


def test_erste_media_contract_rejects_supplied_raw_or_receipt_hash_mismatch(tmp_path: Path) -> None:
    target, index, evidence = _semantic_fixture(tmp_path)
    for key in ("raw_sha256", "receipt_sha256"):
        mismatched = {**evidence, key: "0" * 64}
        with pytest.raises(nav.NavProvenanceError, match="hash does not match"):
            _assess_semantic_fixture(tmp_path, target, index, mismatched)
