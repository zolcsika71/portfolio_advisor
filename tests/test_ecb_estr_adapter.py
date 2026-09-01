"""Strict synthetic tests for the official ECB €STR transport and parser."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from portfolio_advisor.reference_rates import (
    ECB_ESTR_DATAFLOW,
    ECB_ESTR_ISIN,
    ECB_ESTR_MACHINE_URL,
    ECB_ESTR_SERIES_IDENTIFIER,
    ECB_ESTR_SERIES_KEY,
    EcbEstrAcquisitionReceipt,
    EcbEstrError,
    ReferenceRateObservation,
    acquire_ecb_estr,
    load_ecb_estr_receipt,
    parse_ecb_estr_csv,
)
from portfolio_advisor.reference_rates.ecb_estr import (
    ecb_estr_definition,
    ecb_estr_source,
    validate_ecb_estr_policy,
)
from tests.ecb_estr_support import construction_policy, csv_bytes, row, write_evidence


class _Response:
    def __init__(
        self,
        content: bytes,
        *,
        status: int = 200,
        content_type: str = "text/csv",
        content_length: str | None = None,
    ) -> None:
        self.content = content
        self.status_code = status
        self.url = f"{ECB_ESTR_MACHINE_URL}?detail=full&format=csvdata&includeHistory=true"
        self.headers = {
            "Content-Type": content_type,
            "Content-Encoding": "identity",
            "Content-Disposition": "attachment;filename=data.csv",
            "Last-Modified": "Tue, 01 Sep 2026 06:05:24 GMT",
        }
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def iter_content(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self.content[:31]
        yield self.content[31:]


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


def test_reviewed_ecb_identity_and_policy_binding_are_exact() -> None:
    assert ECB_ESTR_DATAFLOW == "EST"
    assert ECB_ESTR_SERIES_KEY == "B.EU000A2X2A25.WT"
    assert ECB_ESTR_SERIES_IDENTIFIER == "EST.B.EU000A2X2A25.WT"
    assert ECB_ESTR_ISIN == "EU000A2X2A25"
    definition = ecb_estr_definition()
    source = ecb_estr_source()
    validate_ecb_estr_policy(construction_policy())
    assert definition.series_identifier == ECB_ESTR_SERIES_IDENTIFIER
    assert definition.rate_units == "PERCENT_PER_ANNUM"
    assert definition.day_count_convention == "ACT_360"
    assert definition.compounding_convention == "SIMPLE_ACT_360_OVERNIGHT"
    assert source.machine_readable_url == ECB_ESTR_MACHINE_URL
    assert source.automated_use_status == "PERMITTED"


def test_parser_uses_exact_decimal_and_stable_semantic_fingerprint() -> None:
    first = parse_ecb_estr_csv(csv_bytes())
    second = parse_ecb_estr_csv(csv_bytes())
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.observation_count == 2
    assert first.version_count == 2
    assert first.first_observation_date == date(2026, 8, 28)
    assert first.last_observation_date == date(2026, 8, 31)
    assert first.versions[0].rate == Decimal("2.186")
    assert first.versions[0].rate_decimal == "2.186"
    assert all(type(item.rate) is Decimal for item in first.versions)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("KEY", "EST.B.WRONG.WT", "KEY metadata"),
        ("FREQ", "D", "FREQ metadata"),
        ("BENCHMARK_ITEM", "EU0000000000", "BENCHMARK_ITEM metadata"),
        ("DATA_TYPE_EST", "RT", "DATA_TYPE_EST metadata"),
        ("UNIT_MEASURE", "EUR", "UNIT_MEASURE metadata"),
        ("UNIT_MULT", "2", "UNIT_MULT metadata"),
        ("DECIMALS", "4", "DECIMALS metadata"),
        ("OBS_STATUS", "P", "observation status"),
        ("ACTION", "D", "history action"),
        ("TIME_PERIOD", "20260831", "YYYY-MM-DD"),
        ("OBS_VALUE", "2.1851", "plain decimal"),
        ("VALID_FROM", "", "VALID_FROM"),
    ],
)
def test_parser_rejects_wrong_identity_missing_and_malformed_values(
    field: str, value: str, message: str
) -> None:
    with pytest.raises(EcbEstrError, match=message):
        parse_ecb_estr_csv(csv_bytes((row(overrides={field: value}),)))


def test_parser_rejects_empty_duplicate_and_conflicting_history() -> None:
    with pytest.raises(EcbEstrError, match="no observations"):
        parse_ecb_estr_csv(csv_bytes(()).split(b"\n", 1)[0] + b"\n")
    duplicate = row()
    with pytest.raises(EcbEstrError, match="duplicate provider observation version"):
        parse_ecb_estr_csv(csv_bytes((duplicate, duplicate)))
    with pytest.raises(EcbEstrError, match="unique current version"):
        parse_ecb_estr_csv(
            csv_bytes(
                (
                    row(valid_from="2026-09-01T06:00:00Z"),
                    row(valid_from="2026-09-01T06:05:00Z", value="2.186", status="R"),
                )
            )
        )


def test_parser_preserves_explicit_provider_revision_chain() -> None:
    dataset = parse_ecb_estr_csv(
        csv_bytes(
            (
                row(
                    value="2.185",
                    valid_from="2026-09-01T06:00:00Z",
                    valid_to="2026-09-01T07:00:00Z",
                ),
                row(
                    value="2.187",
                    valid_from="2026-09-01T07:00:00Z",
                    status="R",
                    action="Replace",
                ),
            )
        )
    )
    assert dataset.observation_count == 1
    assert dataset.version_count == 2
    assert dataset.versions[0].valid_to == dataset.versions[1].valid_from


def test_parser_rejects_naive_or_noncontiguous_provider_revision_timestamps() -> None:
    with pytest.raises(EcbEstrError, match="timezone"):
        parse_ecb_estr_csv(
            csv_bytes((row(valid_from="2026-09-01T06:05:24"),))
        )
    with pytest.raises(EcbEstrError, match="not contiguous"):
        parse_ecb_estr_csv(
            csv_bytes(
                (
                    row(
                        valid_from="2026-09-01T06:00:00Z",
                        valid_to="2026-09-01T06:30:00Z",
                    ),
                    row(valid_from="2026-09-01T07:00:00Z", status="R"),
                )
            )
        )


def test_observation_fingerprint_is_stable_when_current_projection_changes() -> None:
    observation = ReferenceRateObservation(
        benchmark_id="ESTR",
        source_contract_fingerprint="a" * 64,
        import_manifest_fingerprint="b" * 64,
        observation_date=date(2026, 8, 31),
        publication_date=date(2026, 9, 1),
        rate=Decimal("2.1850"),
        provider_revision_id="2026-09-01T06:05:24Z",
        revision_sequence=1,
        supersedes_observation_fingerprint=None,
        is_current=True,
        quality_status="ADMITTED_VALIDATED",
    )
    assert observation.rate_decimal == "2.185"
    assert observation.fingerprint == replace(observation, is_current=False).fingerprint
    with pytest.raises(ValueError, match="positive integer"):
        replace(observation, revision_sequence=1.5)  # type: ignore[arg-type]


def test_acquisition_uses_one_exact_bounded_request_and_retains_pair(tmp_path: Path) -> None:
    content = csv_bytes()
    client = _Client(_Response(content, content_length=str(len(content))))
    result = acquire_ecb_estr(
        repository_root=tmp_path,
        raw_directory=tmp_path / "data/raw/reference_rates/ecb/estr",
        client=client,
        clock=lambda: datetime(2026, 9, 1, 8, 30, tzinfo=UTC),
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == ECB_ESTR_MACHINE_URL
    assert call["params"] == {
        "detail": "full",
        "format": "csvdata",
        "includeHistory": "true",
    }
    assert call["allow_redirects"] is False
    assert call["stream"] is True
    assert call["headers"]["Accept-Encoding"] == "identity"
    assert result.raw_artifact.read_bytes() == content
    assert result.receipt_path.is_file()
    assert result.receipt.raw_artifact_sha256 in result.raw_artifact.name


@pytest.mark.parametrize(
    "response",
    [
        _Response(csv_bytes(), status=206),
        _Response(csv_bytes(), content_type="application/json"),
        _Response(csv_bytes(), content_length="not-a-number"),
        _Response(csv_bytes(), content_length="1"),
        _Response(b""),
    ],
)
def test_acquisition_rejects_invalid_transport_without_retained_files(
    tmp_path: Path, response: _Response
) -> None:
    raw_directory = tmp_path / "data/raw/reference_rates/ecb/estr"
    with pytest.raises(EcbEstrError):
        acquire_ecb_estr(
            repository_root=tmp_path,
            raw_directory=raw_directory,
            client=_Client(response),
            clock=lambda: datetime(2026, 9, 1, 8, 30, tzinfo=UTC),
        )
    assert not raw_directory.exists()


def test_acquisition_rejects_invalid_effective_url_and_http_date(tmp_path: Path) -> None:
    raw_directory = tmp_path / "data/raw/reference_rates/ecb/estr"
    wrong_port = _Response(csv_bytes())
    wrong_port.url = wrong_port.url.replace(".europa.eu/", ".europa.eu:444/")
    with pytest.raises(EcbEstrError, match="effective URL"):
        acquire_ecb_estr(
            repository_root=tmp_path,
            raw_directory=raw_directory,
            client=_Client(wrong_port),
            clock=lambda: datetime(2026, 9, 1, 8, 30, tzinfo=UTC),
        )
    malformed_date = _Response(csv_bytes())
    malformed_date.headers["Last-Modified"] = "not-a-date"
    with pytest.raises(EcbEstrError, match="HTTP date"):
        acquire_ecb_estr(
            repository_root=tmp_path,
            raw_directory=raw_directory,
            client=_Client(malformed_date),
            clock=lambda: datetime(2026, 9, 1, 8, 30, tzinfo=UTC),
        )
    assert not raw_directory.exists()


def test_receipt_rejects_unknown_fields_and_noncanonical_endpoint(tmp_path: Path) -> None:
    content = csv_bytes()
    client = _Client(_Response(content))
    with pytest.raises(EcbEstrError, match="under data/raw"):
        acquire_ecb_estr(
            repository_root=Path("/tmp").resolve(),
            raw_directory=Path("/tmp/not-approved-ecb-evidence"),
            client=client,
        )
    payload = {
        "receipt_schema_version": 1,
        "request_url": ECB_ESTR_MACHINE_URL,
        "unknown": True,
    }
    with pytest.raises(EcbEstrError, match="fields differ"):
        EcbEstrAcquisitionReceipt.from_mapping(payload)
    _, _, receipt = write_evidence(tmp_path)
    malformed_content_type = receipt.canonical_payload()
    malformed_content_type["response_content_type"] = 123
    with pytest.raises(EcbEstrError, match="response_content_type must be an exact non-empty string"):
        EcbEstrAcquisitionReceipt.from_mapping(malformed_content_type)
    malformed_effective_url = receipt.canonical_payload()
    malformed_effective_url["effective_url"] = 123
    with pytest.raises(EcbEstrError, match="effective_url must be an exact non-empty string"):
        EcbEstrAcquisitionReceipt.from_mapping(malformed_effective_url)
    malformed_encoding = receipt.canonical_payload()
    malformed_encoding["content_encoding"] = []
    with pytest.raises(EcbEstrError, match="content encoding"):
        EcbEstrAcquisitionReceipt.from_mapping(malformed_encoding)


def test_receipt_loader_requires_canonical_bytes(tmp_path: Path) -> None:
    _, receipt_path, _ = write_evidence(tmp_path)
    receipt_path.write_text(
        "\n" + receipt_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(EcbEstrError, match="canonical JSON"):
        load_ecb_estr_receipt(receipt_path)
