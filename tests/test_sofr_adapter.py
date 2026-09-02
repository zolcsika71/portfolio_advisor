"""Strict synthetic tests for New York Fed SOFR transport and parsing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import requests

from portfolio_advisor.reference_rates import observations_available_as_of
from portfolio_advisor.reference_rates.sofr import (
    SOFR_MACHINE_URL,
    SofrAcquisitionReceipt,
    SofrError,
    load_sofr_receipt,
    parse_sofr_json,
    prepare_sofr_bundle,
    sofr_definition,
    sofr_source,
    validate_sofr_policy,
)
from portfolio_advisor.reference_rates.sofr_acquisition import acquire_sofr
from tests.sofr_support import (
    construction_policy,
    json_bytes,
    valid_rows,
    write_evidence,
)


class _Response:
    def __init__(
        self,
        content: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json;charset=utf-8",
        content_length: str | None = None,
    ) -> None:
        self.content = content
        self.status_code = status
        self.url = (
            f"{SOFR_MACHINE_URL}?startDate=2018-04-02&endDate=2026-08-31&type=rate"
        )
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def iter_content(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self.content[:31]
        yield self.content[31:]


class _Client:
    def __init__(self, response: _Response | BaseException) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def test_reviewed_sofr_identity_units_and_policy_binding_are_exact() -> None:
    definition = sofr_definition()
    source = sofr_source()
    validate_sofr_policy(construction_policy())
    assert definition.benchmark_id == "SOFR"
    assert definition.currency_code == "USD"
    assert definition.rate_units == "PERCENT_PER_ANNUM"
    assert definition.day_count_convention == "ACT_360"
    assert definition.compounding_convention == "SIMPLE_ACT_360_OVERNIGHT"
    assert source.source_organization == "Federal Reserve Bank of New York"
    assert source.machine_readable_url == SOFR_MACHINE_URL
    assert source.automated_use_status == "PERMITTED"


def test_parser_preserves_decimal_wire_contract_and_deterministic_ordering() -> None:
    rows = valid_rows()
    first = parse_sofr_json(json_bytes(rows))
    second = parse_sofr_json(json_bytes(list(reversed(rows))))
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.observation_count == 2102
    assert first.observations[0].rate == Decimal("2.18")
    assert type(first.observations[0].rate) is Decimal
    assert first.observations[-1].footnote_id == 2
    assert first.observations[-1].percentiles == ("NA", "NA", "NA", "NA")


@pytest.mark.parametrize(
    "product",
    ("SOFR30DAYAVG", "SOFR90DAYAVG", "SOFR180DAYAVG", "SOFRINDEX", "EFFR", "OBFR", "BGCR", "TGCR"),
)
def test_parser_rejects_wrong_products(product: str) -> None:
    rows = valid_rows()
    rows[20]["type"] = product
    with pytest.raises(SofrError, match="wrong reference-rate product"):
        parse_sofr_json(json_bytes(rows))


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda item: item.pop("percentRate"), "fields differ"),
        (lambda item: item.update(percentRate=None), "exact finite JSON decimal"),
        (lambda item: item.update(percentRate="2.18"), "exact finite JSON decimal"),
        (lambda item: item.update(percentRate=2.1), "two-decimal precision"),
        (lambda item: item.update(effectiveDate="20260831"), "YYYY-MM-DD"),
        (lambda item: item.update(currency="EUR"), "fields differ"),
        (lambda item: item.pop("revisionIndicator"), "fields differ"),
        (lambda item: item.update(revisionIndicator=None), "revision indicator"),
        (lambda item: item.update(revisionIndicator="N"), "revision indicator"),
        (lambda item: item.update(footnoteId=None), "null footnote"),
        (lambda item: item.update(footnoteId=99), "footnote identifier"),
    ),
)
def test_parser_rejects_malformed_dates_values_metadata_and_revision(
    mutation: Any, message: str
) -> None:
    rows = valid_rows()
    mutation(rows[10])
    with pytest.raises(SofrError, match=message):
        parse_sofr_json(json_bytes(rows))


def test_parser_rejects_missing_contingency_evidence_duplicates_and_truncation() -> None:
    rows = valid_rows()
    rows[10]["percentPercentile1"] = "NA"
    with pytest.raises(SofrError, match="lacks contingency"):
        parse_sofr_json(json_bytes(rows))
    rows = valid_rows()
    rows[10]["footnoteId"] = 2
    with pytest.raises(SofrError, match="requires unavailable"):
        parse_sofr_json(json_bytes(rows))
    rows = valid_rows()
    rows[10]["effectiveDate"] = str(rows[11]["effectiveDate"])
    with pytest.raises(SofrError, match="duplicate or conflicting"):
        parse_sofr_json(json_bytes(rows))
    with pytest.raises(SofrError):
        parse_sofr_json(b"")
    with pytest.raises(SofrError, match="malformed or truncated"):
        parse_sofr_json(json_bytes()[:-1])
    with pytest.raises(SofrError, match="duplicate object key"):
        parse_sofr_json(b'{"refRates":[],"refRates":[]}')


def test_revision_and_retrieval_bound_semantics_are_truthful(tmp_path: Path) -> None:
    raw, receipt_path, _ = write_evidence(tmp_path)
    bundle = prepare_sofr_bundle(
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt_path,
    )
    observation = bundle.observations[0]
    assert observation.provider_revision_id is None
    assert observation.provider_revision_indicator == ""
    assert observation.provider_revision_indicator_source_field == "revisionIndicator"
    assert observation.provider_revision_status == "PROVIDER_EMPTY_REVISION_INDICATOR"
    assert bundle.manifest.provider_dataset_version is None
    assert observation.provider_publication_date is None
    assert observation.provider_publication_value is None
    assert observation.availability_basis == "RETRIEVAL_BOUND"
    assert observations_available_as_of(
        bundle.observations, datetime(2026, 9, 2, 17, 0, 38, 999999, tzinfo=UTC)
    ) == ()
    assert len(
        observations_available_as_of(
            bundle.observations, datetime(2026, 9, 2, 17, 0, 39, tzinfo=UTC)
        )
    ) == 2102


def test_acquisition_uses_one_exact_bounded_request_and_retains_pair(tmp_path: Path) -> None:
    content = json_bytes()
    client = _Client(_Response(content, content_length=str(len(content))))
    result = acquire_sofr(
        repository_root=tmp_path,
        raw_directory=tmp_path / "data/raw/reference_rates/new_york_fed/sofr",
        client=client,
        clock=lambda: datetime(2026, 9, 2, 17, 0, 39, tzinfo=UTC),
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == SOFR_MACHINE_URL
    assert call["params"] == {
        "startDate": "2018-04-02",
        "endDate": "2026-08-31",
        "type": "rate",
    }
    assert call["allow_redirects"] is False
    assert call["stream"] is True
    assert call["headers"]["Accept-Encoding"] == "identity"
    assert result.raw_artifact.read_bytes() == content
    assert load_sofr_receipt(result.receipt_path) == result.receipt


@pytest.mark.parametrize(
    "response",
    (
        _Response(json_bytes(), status=302),
        _Response(json_bytes(), status=500),
        _Response(json_bytes(), content_type="text/html"),
        _Response(json_bytes(), content_type="application/json;charset=latin-1"),
        _Response(json_bytes(), content_length="bad"),
        _Response(json_bytes(), content_length="1"),
        _Response(json_bytes(), content_length=str(8 * 1024 * 1024 + 1)),
        _Response(b""),
        requests.Timeout("timeout"),
    ),
)
def test_acquisition_rejects_transport_failures_without_retention(
    tmp_path: Path, response: _Response | BaseException
) -> None:
    directory = tmp_path / "data/raw/reference_rates/new_york_fed/sofr"
    with pytest.raises(SofrError):
        acquire_sofr(
            repository_root=tmp_path,
            raw_directory=directory,
            client=_Client(response),
            clock=lambda: datetime(2026, 9, 2, 17, 0, 39, tzinfo=UTC),
        )
    assert not directory.exists()


def test_acquisition_and_receipt_reject_effective_url_and_tampering(tmp_path: Path) -> None:
    response = _Response(json_bytes())
    response.url = response.url.replace("markets.newyorkfed.org", "example.com")
    with pytest.raises(SofrError, match="effective URL"):
        acquire_sofr(
            repository_root=tmp_path,
            raw_directory=tmp_path / "data/raw/reference_rates/new_york_fed/sofr",
            client=_Client(response),
        )
    raw, receipt_path, receipt = write_evidence(tmp_path)
    payload = receipt.canonical_payload()
    payload["raw_artifact_sha256"] = "0" * 64
    changed = SofrAcquisitionReceipt.from_mapping(payload)
    receipt_path.write_text(
        json.dumps(changed.canonical_payload(), sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(SofrError):
        prepare_sofr_bundle(
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt_path,
        )
    assert raw.is_file()


def test_acquisition_and_offline_import_reject_symlinked_parent(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "data" / "raw" / "reference_rates"
    parent.mkdir(parents=True)
    (parent / "new_york_fed").symlink_to(outside, target_is_directory=True)
    directory = parent / "new_york_fed" / "sofr"
    with pytest.raises(SofrError, match="approved directory"):
        acquire_sofr(
            repository_root=tmp_path,
            raw_directory=directory,
            client=_Client(_Response(json_bytes())),
        )
    assert not (outside / "sofr").exists()

    clean_root = tmp_path / "clean"
    raw, receipt_path, _ = write_evidence(clean_root)
    retained = clean_root / "data" / "raw" / "reference_rates"
    actual = clean_root / "actual-new-york-fed"
    (retained / "new_york_fed").rename(actual)
    (retained / "new_york_fed").symlink_to(actual, target_is_directory=True)
    with pytest.raises(SofrError, match="symlink component"):
        prepare_sofr_bundle(
            repository_root=clean_root,
            raw_artifact=raw,
            receipt_path=receipt_path,
        )


def test_receipt_rejects_noncanonical_and_unknown_fields(
    tmp_path: Path,
) -> None:
    _, receipt_path, receipt = write_evidence(tmp_path)
    assert load_sofr_receipt(receipt_path) == receipt
    receipt_path.write_bytes(b"\n" + receipt_path.read_bytes())
    with pytest.raises(SofrError, match="canonical JSON"):
        load_sofr_receipt(receipt_path)
    with pytest.raises(SofrError, match="fields differ"):
        SofrAcquisitionReceipt.from_mapping({"unknown": True})
