"""Strict synthetic tests for official MNB HUFONIA transport and parsing."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from struct import pack
from typing import Any

import pytest
import requests

from portfolio_advisor.reference_rates import hufonia as hufonia_module
from portfolio_advisor.reference_rates import observations_available_as_of
from portfolio_advisor.reference_rates.hufonia import (
    _BIFF_FORMULA,
    HUFONIA_MACHINE_URL,
    HufoniaAcquisitionReceipt,
    HufoniaError,
    _BiffNumericCell,
    hufonia_definition,
    hufonia_source,
    load_hufonia_receipt,
    parse_hufonia_xls,
    prepare_hufonia_bundle,
    validate_hufonia_policy,
)
from portfolio_advisor.reference_rates.hufonia_acquisition import acquire_hufonia
from tests.hufonia_support import (
    SYNTHETIC_RAW,
    SyntheticBook,
    construction_policy,
    install_synthetic_workbook,
    write_evidence,
)


class _Response:
    def __init__(
        self,
        content: bytes = SYNTHETIC_RAW,
        *,
        status: int = 200,
        content_type: str = "application/vnd.ms-excel",
        content_encoding: str = "",
        content_length: str | None = None,
        url: str = HUFONIA_MACHINE_URL,
    ) -> None:
        self.content = content
        self.status_code = status
        self.url = url
        self.headers = {"Content-Type": content_type}
        if content_encoding:
            self.headers["Content-Encoding"] = content_encoding
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def iter_content(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self.content[:17]
        yield self.content[17:]


class _Client:
    def __init__(self, response: _Response | BaseException) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class _DirectoryEntry:
    def __init__(self, name: str, entry_type: int = 2) -> None:
        self.name = name
        self.etype = entry_type


class _CompoundDocument:
    def __init__(self, stream: bytes, names: tuple[str, ...] = ("Workbook",)) -> None:
        self.stream = stream
        self.dirlist = [_DirectoryEntry(name) for name in names]

    def get_named_stream(self, name: str) -> bytes:
        assert name == "Workbook"
        return self.stream


def _record(code: int, data: bytes = b"") -> bytes:
    return pack("<HH", code, len(data)) + data


def _boundsheet(name: str, offset: int) -> bytes:
    encoded = name.encode("latin-1")
    return pack("<I", offset) + bytes((0, 0, len(encoded), 0)) + encoded


def _single_sheet_stream(sheet_records: bytes, name: str = "2026") -> bytes:
    placeholder = _record(hufonia_module._BIFF_BOUNDSHEET, _boundsheet(name, 0))
    offset = len(placeholder) + len(_record(hufonia_module._BIFF_EOF))
    return (
        _record(hufonia_module._BIFF_BOUNDSHEET, _boundsheet(name, offset))
        + _record(hufonia_module._BIFF_EOF)
        + sheet_records
        + _record(hufonia_module._BIFF_EOF)
    )


def _install_compound_document(
    monkeypatch: pytest.MonkeyPatch,
    stream: bytes,
    names: tuple[str, ...] = ("Workbook",),
) -> None:
    monkeypatch.setattr(
        hufonia_module,
        "CompDoc",
        lambda _: _CompoundDocument(stream, names),
    )


def test_reviewed_hufonia_identity_units_and_policy_binding_are_exact() -> None:
    definition = hufonia_definition()
    source = hufonia_source()
    validate_hufonia_policy(construction_policy())
    assert definition.benchmark_id == "HUFONIA"
    assert definition.currency_code == "HUF"
    assert definition.administrator == "Magyar Nemzeti Bank"
    assert definition.rate_units == "PERCENT_PER_ANNUM"
    assert definition.compounding_convention == "NONE_DAILY_OVERNIGHT_RATE"
    assert definition.day_count_convention == "NOT_SUPPLIED_BY_MNB"
    assert source.source_organization == "Magyar Nemzeti Bank"
    assert source.machine_readable_url == HUFONIA_MACHINE_URL
    assert source.source_role == "OFFICIAL_ADMINISTRATOR"


def test_parser_preserves_decimal_precision_annotations_and_ordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_workbook(monkeypatch)
    first = parse_hufonia_xls(SYNTHETIC_RAW)
    second = parse_hufonia_xls(SYNTHETIC_RAW)
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.observation_count == 6231
    assert (first.first_observation_date, first.last_observation_date) == (
        date(2002, 1, 2),
        date(2026, 8, 31),
    )
    text_rate = next(
        item for item in first.observations if item.observation_date == date(2023, 4, 26)
    )
    assert text_rate.rate == Decimal("17.880")
    assert type(text_rate.rate) is Decimal
    correction = next(
        item for item in first.observations if item.observation_date == date(2015, 11, 19)
    )
    assert correction.revision_indicator == "módosítva 14:53-kor"
    assert correction.observation_date_basis == "VALUE_DATE"
    switched = next(
        item for item in first.observations if item.observation_date == date(2016, 10, 4)
    )
    assert switched.observation_date_basis == "TRADE_DATE"
    assert switched.revision_indicator is None


def test_parser_rejects_wrong_identity_hidden_sheets_and_unknown_annotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def wrong_identity(book: SyntheticBook, _: object) -> None:
        book.sheets["HUFONIA_SWAP"] = book.sheets.pop("2023")

    install_synthetic_workbook(monkeypatch, wrong_identity)
    with pytest.raises(HufoniaError, match="identity"):
        parse_hufonia_xls(SYNTHETIC_RAW)

    monkeypatch.undo()

    def hidden(book: SyntheticBook, _: object) -> None:
        book.sheet_by_name("2024").visibility = 1

    install_synthetic_workbook(monkeypatch, hidden)
    with pytest.raises(HufoniaError, match="hidden|non-visible"):
        parse_hufonia_xls(SYNTHETIC_RAW)

    monkeypatch.undo()

    def annotation(book: SyntheticBook, _: object) -> None:
        book.sheet_by_name("2015").rows[226][3] = "provisional"

    install_synthetic_workbook(monkeypatch, annotation)
    with pytest.raises(HufoniaError, match="unknown provider annotation"):
        parse_hufonia_xls(SYNTHETIC_RAW)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda _book, numeric: numeric["2024"].pop((2, 1)),
            "missing or malformed observation",
        ),
        (
            lambda _book, numeric: numeric["2024"].__setitem__(
                (2, 1), _BiffNumericCell(None, _BIFF_FORMULA, 2)
            ),
            "unsupported cell record or formula",
        ),
        (
            lambda _book, numeric: numeric["2002"].__setitem__(
                (1, 0), _BiffNumericCell(Decimal(0), 0x027E, 0)
            ),
            "outside the safe range",
        ),
        (
            lambda _book, numeric: numeric["2024"].__setitem__(
                (3, 0), numeric["2024"][(2, 0)]
            ),
            "strictly increasing",
        ),
        (
            lambda _book, numeric: numeric["2024"].__setitem__(
                (2, 2), _BiffNumericCell(Decimal(-1), 0x027E, 0)
            ),
            "must not be negative",
        ),
    ),
)
def test_parser_rejects_missing_formula_out_of_range_duplicate_and_negative_values(
    monkeypatch: pytest.MonkeyPatch,
    mutation: Any,
    message: str,
) -> None:
    install_synthetic_workbook(monkeypatch, mutation)
    with pytest.raises(HufoniaError, match=message):
        parse_hufonia_xls(SYNTHETIC_RAW)


def test_parser_rejects_empty_truncated_and_unreviewed_workbooks() -> None:
    with pytest.raises(HufoniaError, match="empty"):
        parse_hufonia_xls(b"")
    with pytest.raises(HufoniaError, match="malformed|compound"):
        parse_hufonia_xls(b"truncated")


def test_low_level_scanner_rejects_formula_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    formula = _record(hufonia_module._BIFF_FORMULA, pack("<HHH", 99, 12, 0))
    _install_compound_document(monkeypatch, _single_sheet_stream(formula))
    with pytest.raises(HufoniaError, match="prohibited formula"):
        hufonia_module._biff_numeric_cells(b"synthetic-ole")


@pytest.mark.parametrize(
    "names",
    (
        ("\x05SummaryInformation",),
        ("Workbook", "VBA"),
    ),
)
def test_low_level_scanner_requires_only_reviewed_ole_streams(
    monkeypatch: pytest.MonkeyPatch,
    names: tuple[str, ...],
) -> None:
    _install_compound_document(
        monkeypatch,
        _record(hufonia_module._BIFF_EOF),
        names,
    )
    with pytest.raises(HufoniaError, match="unsupported embedded stream"):
        hufonia_module._biff_numeric_cells(b"synthetic-ole")


def test_low_level_scanner_rejects_truncated_and_malformed_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_compound_document(monkeypatch, b"\x85\x00")
    with pytest.raises(HufoniaError, match="truncated"):
        hufonia_module._biff_numeric_cells(b"synthetic-ole")

    malformed_boundsheet = _record(hufonia_module._BIFF_BOUNDSHEET, b"x")
    _install_compound_document(
        monkeypatch,
        malformed_boundsheet + _record(hufonia_module._BIFF_EOF),
    )
    with pytest.raises(HufoniaError, match="BOUNDSHEET"):
        hufonia_module._biff_numeric_cells(b"synthetic-ole")

    malformed_number = _record(hufonia_module._BIFF_NUMBER, b"\0" * 6)
    _install_compound_document(
        monkeypatch,
        _single_sheet_stream(malformed_number),
    )
    with pytest.raises(HufoniaError, match="NUMBER record"):
        hufonia_module._biff_numeric_cells(b"synthetic-ole")


def test_low_level_scanner_rejects_duplicate_sheets_cells_and_nonfinite_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate_global = (
        _record(hufonia_module._BIFF_BOUNDSHEET, _boundsheet("2026", 0))
        + _record(hufonia_module._BIFF_BOUNDSHEET, _boundsheet("2026", 0))
        + _record(hufonia_module._BIFF_EOF)
    )
    _install_compound_document(monkeypatch, duplicate_global)
    with pytest.raises(HufoniaError, match="duplicate sheet"):
        hufonia_module._biff_numeric_cells(b"synthetic-ole")

    number_data = pack("<HHH", 1, 2, 0) + (0x3FF8000000000000).to_bytes(8, "little")
    duplicate_cells = _record(hufonia_module._BIFF_NUMBER, number_data) * 2
    _install_compound_document(
        monkeypatch,
        _single_sheet_stream(duplicate_cells),
    )
    with pytest.raises(HufoniaError, match="duplicate numeric cell"):
        hufonia_module._biff_numeric_cells(b"synthetic-ole")

    infinite_data = pack("<HHH", 1, 2, 0) + (0x7FF0000000000000).to_bytes(8, "little")
    _install_compound_document(
        monkeypatch,
        _single_sheet_stream(_record(hufonia_module._BIFF_NUMBER, infinite_data)),
    )
    with pytest.raises(HufoniaError, match="non-finite"):
        hufonia_module._biff_numeric_cells(b"synthetic-ole")


def test_low_level_rk_and_ieee_decoding_preserve_exact_decimal_values() -> None:
    assert hufonia_module._ieee_decimal(0x3FF8000000000000) == Decimal("1.5")
    assert hufonia_module._rk_decimal((123 << 2) | 2) == Decimal(123)
    assert hufonia_module._rk_decimal((123 << 2) | 3) == Decimal("1.23")


def test_info_sheet_fingerprint_tampering_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book, _ = install_synthetic_workbook(monkeypatch)
    book.sheet_by_name("info").rows[0][0] = "HUFONIA Swap Index"
    with pytest.raises(HufoniaError, match="definition sheet"):
        parse_hufonia_xls(SYNTHETIC_RAW)


def test_revision_and_retrieval_bound_semantics_are_truthful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_workbook(monkeypatch)
    raw, receipt_path, _ = write_evidence(tmp_path)
    bundle = prepare_hufonia_bundle(
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt_path,
    )
    ordinary = bundle.observations[0]
    assert ordinary.provider_revision_id is None
    assert ordinary.provider_revision_indicator is None
    assert ordinary.provider_revision_indicator_source_field is None
    assert ordinary.provider_revision_status == "PROVIDER_REVISION_FIELD_NOT_SUPPLIED"
    assert bundle.manifest.provider_dataset_version is None
    assert ordinary.provider_publication_date is None
    assert ordinary.provider_publication_value is None
    assert ordinary.availability_basis == "RETRIEVAL_BOUND"
    revised = next(
        item
        for item in bundle.observations
        if item.observation_date == date(2015, 11, 19)
    )
    assert revised.provider_revision_status == "PROVIDER_EXPLICIT_REVISION"
    assert revised.provider_revision_id is None
    assert revised.provider_revision_indicator_source_field == "2015!D227"
    assert observations_available_as_of(
        bundle.observations,
        datetime(2026, 9, 3, 12, 27, 2, 999999, tzinfo=UTC),
    ) == ()
    assert len(
        observations_available_as_of(
            bundle.observations,
            datetime(2026, 9, 3, 12, 27, 3, tzinfo=UTC),
        )
    ) == 6231


def test_acquisition_uses_one_exact_bounded_request_and_retains_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_workbook(monkeypatch)
    client = _Client(_Response(content_length=str(len(SYNTHETIC_RAW))))
    result = acquire_hufonia(
        repository_root=tmp_path,
        raw_directory=tmp_path / "data/raw/reference_rates/mnb/hufonia",
        client=client,
        clock=lambda: datetime(2026, 9, 3, 12, 27, 3, tzinfo=UTC),
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == HUFONIA_MACHINE_URL
    assert call["params"] == {}
    assert call["allow_redirects"] is False
    assert call["stream"] is True
    assert call["timeout"] == (10.0, 60.0)
    assert call["headers"]["Accept"] == "application/vnd.ms-excel"
    assert call["headers"]["Accept-Encoding"] == "identity"
    assert result.raw_artifact.read_bytes() == SYNTHETIC_RAW
    assert load_hufonia_receipt(result.receipt_path) == result.receipt


@pytest.mark.parametrize(
    "response",
    (
        _Response(status=302),
        _Response(status=500),
        _Response(content_type="text/html"),
        _Response(content_encoding="gzip"),
        _Response(content_length="bad"),
        _Response(content_length="1"),
        _Response(content_length=str(8 * 1024 * 1024 + 1)),
        _Response(content=b""),
        _Response(url="https://example.com/hufonia.xls"),
        requests.Timeout("timeout"),
    ),
)
def test_acquisition_rejects_transport_failures_without_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: _Response | BaseException,
) -> None:
    install_synthetic_workbook(monkeypatch)
    directory = tmp_path / "data/raw/reference_rates/mnb/hufonia"
    with pytest.raises(HufoniaError):
        acquire_hufonia(
            repository_root=tmp_path,
            raw_directory=directory,
            client=_Client(response),
        )
    assert not directory.exists()


def test_receipt_and_artifact_tampering_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_workbook(monkeypatch)
    raw, receipt_path, receipt = write_evidence(tmp_path)
    assert load_hufonia_receipt(receipt_path) == receipt
    original = raw.read_bytes()
    raw.write_bytes(original + b"x")
    with pytest.raises(HufoniaError, match="byte count|SHA-256"):
        prepare_hufonia_bundle(
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt_path,
        )
    raw.write_bytes(original)
    receipt_path.write_bytes(b"\n" + receipt_path.read_bytes())
    with pytest.raises(HufoniaError, match="canonical JSON"):
        load_hufonia_receipt(receipt_path)
    with pytest.raises(HufoniaError, match="fields differ"):
        HufoniaAcquisitionReceipt.from_mapping({"unknown": True})


def test_acquisition_and_offline_import_reject_symlinked_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_workbook(monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "data/raw/reference_rates"
    parent.mkdir(parents=True)
    (parent / "mnb").symlink_to(outside, target_is_directory=True)
    directory = parent / "mnb/hufonia"
    with pytest.raises(HufoniaError, match="approved directory"):
        acquire_hufonia(
            repository_root=tmp_path,
            raw_directory=directory,
            client=_Client(_Response()),
        )
    assert not (outside / "hufonia").exists()

    clean_root = tmp_path / "clean"
    raw, receipt_path, _ = write_evidence(clean_root)
    retained = clean_root / "data/raw/reference_rates"
    actual = clean_root / "actual-mnb"
    (retained / "mnb").rename(actual)
    (retained / "mnb").symlink_to(actual, target_is_directory=True)
    with pytest.raises(HufoniaError, match="symlink component"):
        prepare_hufonia_bundle(
            repository_root=clean_root,
            raw_artifact=raw,
            receipt_path=receipt_path,
        )


def test_receipt_rejects_wrong_currency_or_product_paths() -> None:
    payload = {
        "benchmark": "HUFONIA_SWAP",
        "currency": "EUR",
    }
    with pytest.raises(HufoniaError, match="fields differ"):
        HufoniaAcquisitionReceipt.from_mapping(json.loads(json.dumps(payload)))
