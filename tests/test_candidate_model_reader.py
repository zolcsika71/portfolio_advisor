"""Synthetic reader construction only; no advisor execution or private inputs."""

from __future__ import annotations

import inspect
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.database.repository import (
    FileBackedModelPortfolioReader,
    ModelPortfolioReader,
)
from portfolio_advisor.workbook_source import (
    MODEL_CLASSIFICATION_LEGACY_LEXICAL_V1 as LEXICAL,
)
from portfolio_advisor.workbook_source import (
    MODEL_CLASSIFICATION_ORIGINAL_LABELS_V1 as LABELS,
)
from portfolio_advisor.workbook_source import (
    MODEL_METRIC_ORIGINAL_V1 as ORIGINAL,
)
from portfolio_advisor.workbook_source import (
    MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1 as COMPATIBILITY,
)
from portfolio_advisor.workbook_source import (
    MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1 as KEY_ORDER,
)
from portfolio_advisor.workbook_source import (
    ORIGINAL_MODEL_DESCRIPTORS_V1,
    CandidateModelReaderError,
    CandidateNativeModelPortfolioReader,
    normalize_biff_xls_envelope,
    parse_biff_xls,
)
from portfolio_advisor.workbook_source import (
    SOURCE_ORDER_V1 as SOURCE,
)
from portfolio_advisor.workbook_source import model_currency_risk_projection as risk
from tests.fixtures.biff_xls_fixture import write_biff_fixture
from tests.test_model_currency_risk_projection import _synthetic_anomaly_case
from tests.test_model_projection_ledger import _compose


@pytest.fixture
def envelope(tmp_path):
    return parse_biff_xls(write_biff_fixture(tmp_path, model_currency_risk="Fedezve"))


def _cell(envelope, header, *, occurrence=1, **changes):
    sheet = envelope.sheets[0]
    index = next(i for i, c in enumerate(sheet.headers) if c.raw_value == header)
    row = sheet.rows[occurrence - 1]
    cells = list(row.cells)
    if "cell_type" in changes:
        changes.setdefault(
            "error_text", "#VALUE!" if changes["cell_type"] == "error" else None
        )
    cells[index] = replace(cells[index], **changes)
    row = replace(row, cells=tuple(cells))
    payload = row.to_dict()
    payload.pop("row_fingerprint")
    row = replace(row, row_fingerprint=canonical_fingerprint(payload))
    rows = list(sheet.rows)
    rows[occurrence - 1] = row
    sheet = replace(sheet, rows=tuple(rows))
    payload = sheet.to_dict()
    payload.pop("sheet_fingerprint")
    sheet = replace(sheet, sheet_fingerprint=canonical_fingerprint(payload))
    return replace(envelope, sheets=(sheet, envelope.sheets[1]))


def _ledger(envelope, mode=COMPATIBILITY):
    return _compose(
        normalize_biff_xls_envelope(
            envelope, expected_workbook_sha256=envelope.workbook_sha256
        ),
        mode,
    )


def _reader(*ledgers, **changes):
    return CandidateNativeModelPortfolioReader(
        **{
            "ledgers": tuple(ledgers),
            "expected_candidate_fingerprints": tuple(
                item.original_candidate.candidate_fingerprint for item in ledgers
            ),
            "expected_ledger_fingerprints": tuple(
                item.ledger_fingerprint for item in ledgers
            ),
            "classification_profile": LEXICAL,
            "descriptor_profile": ORIGINAL_MODEL_DESCRIPTORS_V1,
            "holding_order": SOURCE,
            **changes,
        }
    )


@pytest.mark.parametrize("classification", [LEXICAL, LABELS])
@pytest.mark.parametrize("order", [SOURCE, KEY_ORDER])
@pytest.mark.parametrize("mode", [ORIGINAL, COMPATIBILITY])
def test_public_reader_protocol_and_complete_evidence(
    envelope, classification, order, mode
):
    # The five DTO metrics include RETURN_1Y, not YTD. Exercise its zero policy.
    envelope = _cell(envelope, "1yr", raw_value=0.0)
    ledger = _ledger(envelope, mode)
    before = ledger.to_json()
    reader = _reader(ledger, classification_profile=classification, holding_order=order)
    assert isinstance(reader, ModelPortfolioReader)
    assert not isinstance(reader, FileBackedModelPortfolioReader)
    assert reader.observation_dates() == (date(2026, 1, 15),)
    assert reader.latest_observation_date() == date(2026, 1, 15)
    holdings = reader.load_holdings(date(2026, 1, 15))
    assert len(holdings) == 2
    first = holdings[0]
    assert first.return_1y == (0.0 if mode == ORIGINAL else None)
    assert first.allocation == 50.0
    assert first.currency_risk == "Hedged"
    assert first.asset_class == ("Bond" if classification == LEXICAL else "Kötvény")
    snapshot = reader.snapshot_provenance(date(2026, 1, 15))
    assert len(snapshot.provenance) == 2
    assert len(snapshot.provenance[0].fields) == 13  # 12 DTO fields + sub-asset
    bindings = snapshot.to_dict()["evaluated_reader_policies"]
    assert bindings == reader.to_dict()["policies"]
    assert bindings["classification"]["profile"] == classification
    assert bindings["descriptors"]["profile"] == ORIGINAL_MODEL_DESCRIPTORS_V1
    assert bindings["ordering"]["profile"] == order
    assert bindings["ordering"]["collation"] == (
        "UTF8_BINARY" if order == KEY_ORDER else None
    )
    for binding in (
        bindings["classification"],
        bindings["descriptors"],
        bindings["ordering"],
    ):
        assert binding["content_fingerprint"] == canonical_fingerprint(
            {k: v for k, v in binding.items() if k != "content_fingerprint"}
        )
    assert sorted(snapshot.dto_to_source) == [0, 1]
    for item, source_index in zip(
        snapshot.provenance, snapshot.dto_to_source, strict=True
    ):
        assert item.source_occurrence == ledger.occurrences[source_index]
        assert item.observation == snapshot.holdings[item.dto_position]
    assert snapshot.ledger.to_dict() == ledger.to_dict()
    assert (
        snapshot.ledger.original_candidate.diagnostics
        == ledger.original_candidate.diagnostics
    )
    assert len(snapshot.ledger.original_candidate.sheets[1].rows) == 2
    assert (
        reader.admission_approval
        == reader.to_dict()["admission_approval"]
        == "NOT_GRANTED"
    )
    assert ledger.to_json() == before
    assert (
        reader.to_json()
        == _reader(
            ledger, classification_profile=classification, holding_order=order
        ).to_json()
    )


@pytest.mark.parametrize(
    "header,key,expected",
    [
        ("Eszközosztály", "alternatív", "Alternative"),
        ("Eszközosztály", "kötvény", "Bond"),
        ("Eszközosztály", "kötvény - befektetési kategória", "Investment Grade Bond"),
        ("Eszközosztály", "kötvény-befektetési kategória", "Investment Grade Bond"),
        ("Eszközosztály", "kötvény - magas hozamú", "High Yield Bond"),
        ("Eszközosztály", "kötvény-magas hozamú", "High Yield Bond"),
        ("Eszközosztály", "kötvény-rugalmas", "Flexible Bond"),
        ("Eszközosztály", "pénzpiac", "Money Market"),
        ("Eszközosztály", "pénzpiaci", "Money Market"),
        ("Eszközosztály", "részvény", "Equity"),
        ("Aleszközosztály", "abszolút hozamú", "Absolute Return"),
        ("Aleszközosztály", "amerikai dollár", "USD"),
        ("Aleszközosztály", "eur", "EUR"),
        ("Aleszközosztály", "euro", "EUR"),
        ("Aleszközosztály", "európa", "Europe"),
        ("Aleszközosztály", "európa-vállalatok", "Europe-Corporates"),
        ("Aleszközosztály", "európai vállalatok", "Europe-Corporates"),
        ("Aleszközosztály", "fejl?d? piacok", "Emerging Markets"),
        ("Aleszközosztály", "globál", "Global"),
        ("Aleszközosztály", "globál állampapír", "Global-Government Bond"),
        ("Aleszközosztály", "globál-állampapír", "Global-Government Bond"),
        ("Aleszközosztály", "hu-állampapír", "Hungary-Government Bond"),
        ("Aleszközosztály", "huf", "HUF"),
        ("Aleszközosztály", "ingatlan", "Real Estate"),
        (
            "Aleszközosztály",
            "kötvény - magyar állampapírok",
            "Bond - Hungarian Government Bonds",
        ),
        (
            "Aleszközosztály",
            "közép-kelet európai állampapír",
            "Central and Eastern European Government Bond",
        ),
        ("Aleszközosztály", "magyar forint", "HUF"),
        ("Aleszközosztály", "magyar állampapírok", "Hungarian Government Bonds"),
        ("Aleszközosztály", "nyersanyag", "Commodities"),
        ("Aleszközosztály", "részvény - fejl?d? piacok", "Equity - Emerging Markets"),
        ("Aleszközosztály", "usd", "USD"),
        ("Aleszközosztály", "észak-amerika", "North America"),
        (
            "Aleszközosztály",
            "észak-amerika-állampapír",
            "North America-Government Bond",
        ),
        (
            "Aleszközosztály",
            "észak-amerikai állampapír",
            "North America-Government Bond",
        ),
    ],
)
def test_exact_approved_tables_and_same_column_english(envelope, header, key, expected):
    for text in (f" {key.upper()} ", expected):
        reader = _reader(_ledger(_cell(envelope, header, raw_value=text)))
        sidecar = reader.snapshots[0].provenance[0]
        field = next(item for item in sidecar.fields if item.original.header == header)
        assert field.value == expected
        assert field.original.source_cell.raw_value == text
        assert field.lookup_key is not None


@pytest.mark.parametrize(
    "header,value",
    [
        ("Eszközosztály", "USD"),
        ("Aleszközosztály", "Bond"),
        ("Aleszközosztály", "fejlődő piacok"),
        ("Eszközosztály", "Unapproved"),
    ],
)
def test_unknown_and_cross_column_keys_fail_lexical_only(envelope, header, value):
    ledger = _ledger(_cell(envelope, header, raw_value=value))
    with pytest.raises(CandidateModelReaderError, match="UNKNOWN_CLASSIFICATION"):
        _reader(ledger)
    original = _reader(ledger, classification_profile=LABELS)
    assert any(
        field.value == value for field in original.snapshots[0].provenance[0].fields
    )


def test_nfc_lookup_does_not_rewrite_originals(envelope):
    raw = "  KO\u0308TVE\u0301NY "
    reader = _reader(_ledger(_cell(envelope, "Eszközosztály", raw_value=raw)))
    field = next(
        f
        for f in reader.snapshots[0].provenance[0].fields
        if f.field_name == "asset_class"
    )
    assert field.value == "Bond"
    assert field.lookup_key == "kötvény"
    assert field.original.source_cell.raw_value == raw


@pytest.mark.parametrize(
    "header", ["Portfólió neve", "Termék", "ISIN", "Eszközosztály", "Aleszközosztály"]
)
@pytest.mark.parametrize("value", [None, "", " "])
def test_invalid_required_fields_fail_complete_snapshot(envelope, header, value):
    changes = {
        "raw_value": value,
        "cell_type": "blank" if value is None else "text",
        "biff_type_code": 6 if value is None else 1,
    }
    with pytest.raises(CandidateModelReaderError, match="INVALID_REQUIRED_FIELD"):
        _reader(_ledger(_cell(envelope, header, **changes)))


@pytest.mark.parametrize("value", ["lowercaseisin", "123", "ie00B7KFL990"])
def test_invalid_isin_never_repaired(envelope, value):
    with pytest.raises(CandidateModelReaderError, match="INVALID_REQUIRED_FIELD"):
        _reader(_ledger(_cell(envelope, "ISIN", raw_value=value)))


@pytest.mark.parametrize(
    "kind,code,value,expected",
    [
        ("blank", 6, None, None),
        ("empty", 0, None, None),
        ("text", 1, "", None),
        ("text", 1, "  ", None),
        ("text", 1, " USD ", "USD"),
        ("text", 1, "0", "0"),
    ],
)
def test_optional_currency_preserves_typed_missing_and_text(
    envelope, kind, code, value, expected
):
    ledger = _ledger(
        _cell(envelope, "Deviza", cell_type=kind, biff_type_code=code, raw_value=value)
    )
    reader = _reader(ledger)
    assert reader.snapshots[0].holdings[0].currency == expected
    source = next(
        f.original.source_cell
        for f in reader.snapshots[0].provenance[0].fields
        if f.field_name == "currency"
    )
    assert (source.cell_type, source.raw_value) == (kind, value)


@pytest.mark.parametrize(
    "kind,code,value",
    [
        ("number", 2, 0.0),
        ("boolean", 4, False),
        ("date_serial", 3, 45000.0),
        ("error", 5, 15),
    ],
)
def test_invalid_optional_currency_is_not_none(envelope, kind, code, value):
    with pytest.raises(CandidateModelReaderError):
        _reader(
            _ledger(
                _cell(
                    envelope,
                    "Deviza",
                    cell_type=kind,
                    biff_type_code=code,
                    raw_value=value,
                )
            )
        )


@pytest.mark.parametrize(
    "kind,code,value",
    [
        ("number", 2, -1.0),
        ("text", 1, "0"),
        ("blank", 6, None),
        ("boolean", 4, False),
        ("date_serial", 3, 0.0),
        ("error", 5, 15),
    ],
)
def test_invalid_allocation_never_cleaned_or_rescaled(envelope, kind, code, value):
    with pytest.raises(CandidateModelReaderError, match="INVALID_ALLOCATION"):
        _reader(
            _ledger(
                _cell(
                    envelope,
                    "Hányad (%)",
                    cell_type=kind,
                    biff_type_code=code,
                    raw_value=value,
                )
            )
        )


def test_zero_allocation_duplicates_and_source_ties(envelope):
    envelope = _cell(envelope, "Hányad (%)", raw_value=0.0)
    # Exact duplicate values, but distinct source coordinates and references.
    for header, first in zip(
        envelope.sheets[0].headers, envelope.sheets[0].rows[0].cells, strict=True
    ):
        envelope = _cell(
            envelope,
            header.raw_value,
            occurrence=2,
            raw_value=first.raw_value,
            cell_type=first.cell_type,
            biff_type_code=first.biff_type_code,
        )
    ledger = _ledger(envelope)
    reader = _reader(ledger, holding_order=KEY_ORDER)
    assert reader.snapshots[0].dto_to_source == (0, 1)
    assert reader.snapshots[0].holdings[0] == reader.snapshots[0].holdings[1]
    assert all(h.allocation == 0.0 for h in reader.snapshots[0].holdings)
    assert (
        len(
            {
                p.source_occurrence.original.occurrence_id
                for p in reader.snapshots[0].provenance
            }
        )
        == 2
    )


def test_trim_collision_rejects_but_repeated_identical_raw_name_does_not(envelope):
    with pytest.raises(CandidateModelReaderError, match="PORTFOLIO_TRIM_COLLISION"):
        _reader(
            _ledger(_cell(envelope, "Portfólió neve", raw_value=" PB Szintetikus "))
        )
    envelope = _cell(
        envelope, "Portfólió neve", raw_value=" PB Szintetikus ", occurrence=2
    )
    envelope = _cell(envelope, "Portfólió neve", raw_value=" PB Szintetikus ")
    assert (
        _reader(_ledger(envelope)).snapshots[0].holdings[0].portfolio_name
        == "PB Szintetikus"
    )


def test_binary_reader_order_not_evidence_order(envelope):
    envelope = _cell(envelope, "Termék", raw_value="é")
    envelope = _cell(envelope, "Termék", occurrence=2, raw_value="Z")
    ledger = _ledger(envelope)
    assert _reader(ledger).snapshots[0].dto_to_source == (0, 1)
    reader = _reader(ledger, holding_order=KEY_ORDER)
    assert reader.snapshots[0].dto_to_source == (1, 0)
    assert [h.product for h in reader.snapshots[0].holdings] == ["Z", "é"]
    assert ledger.occurrences[0].original.fields[1].normalized_value == "é"


@pytest.mark.parametrize(
    "header", ["1yr", "1Y Sharpe", "1Y Vol.", "Down. risk", "Max. drawd."]
)
def test_rejected_dto_metric_blocks_snapshot(envelope, header):
    with pytest.raises(CandidateModelReaderError, match="REJECTED_READER_METRIC"):
        _reader(
            _ledger(
                _cell(
                    envelope, header, cell_type="text", biff_type_code=1, raw_value="0"
                )
            )
        )


def test_unused_rejections_and_diagnostics_are_not_waived(envelope):
    ledger = _ledger(
        _cell(envelope, "YTD", cell_type="text", biff_type_code=1, raw_value="0")
    )
    reader = _reader(ledger)
    assert (
        reader.snapshots[0].ledger.original_candidate.diagnostics
        == ledger.original_candidate.diagnostics
    )
    assert any(
        d.code == "INVALID_METRIC_CELL_TYPE"
        for d in ledger.original_candidate.diagnostics
    )
    assert reader.admission_approval == "NOT_GRANTED"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Fedezve", "Hedged"),
        ("Nincs fedezve", "Unhedged"),
        ("Részben fedezve", "Partially Hedged"),
        ("", None),
        (None, None),
    ],
)
def test_approved_currency_outcomes(envelope, value, expected):
    kind, code = ("blank", 6) if value is None else ("text", 1)
    reader = _reader(
        _ledger(
            _cell(
                envelope,
                "Devizakockázat",
                cell_type=kind,
                biff_type_code=code,
                raw_value=value,
            )
        )
    )
    assert reader.snapshots[0].holdings[0].currency_risk == expected


def test_public_anomaly_dispositions_under_test_local_registry_only(
    envelope, monkeypatch
):
    # Synthetic proof shape, never acceptance of the historical retained hashes.
    candidate, synthetic = _synthetic_anomaly_case(envelope)
    with monkeypatch.context() as local:
        local.setattr(risk, "_APPROVED_WORKBOOKS", (synthetic,))
        ledger = _compose(candidate)
        reader = _reader(ledger)
        before = reader.to_json()
        assert len(reader.snapshots[0].holdings) == 24
        assert all(h.currency_risk is None for h in reader.snapshots[0].holdings)
        assert all(
            next(f for f in p.fields if f.field_name == "currency_risk").reason
            == "LEGACY_ANOMALY_AS_NONE_WITHOUT_INTERPRETATION"
            for p in reader.snapshots[0].provenance
        )
    assert reader.to_json() == before  # captured bindings survive restored test state
    with pytest.raises(CandidateModelReaderError):
        _reader(ledger)  # restored production registry cannot approve this fixture


def test_dates_are_unique_sorted_and_exact(envelope, tmp_path):
    one = _ledger(envelope)
    other = _ledger(
        parse_biff_xls(
            write_biff_fixture(
                tmp_path,
                filename="Synthetic_Portfolios_20260216.xls",
                model_currency_risk="Fedezve",
            )
        )
    )
    reader = _reader(other, one)
    assert reader.observation_dates() == (date(2026, 1, 15), date(2026, 2, 16))
    assert reader.latest_observation_date() == date(2026, 2, 16)
    with pytest.raises(CandidateModelReaderError, match="DUPLICATE_SNAPSHOT_DATE"):
        _reader(one, one)
    for invalid in ("2026-01-15", datetime(2026, 1, 15, tzinfo=UTC), date(2026, 1, 16)):
        with pytest.raises(CandidateModelReaderError):
            reader.load_holdings(invalid)


@pytest.mark.parametrize(
    "argument", ["classification_profile", "descriptor_profile", "holding_order"]
)
def test_explicit_profiles_are_mandatory(envelope, argument):
    ledger = _ledger(envelope)
    with pytest.raises(CandidateModelReaderError, match="UNSUPPORTED_PROFILE"):
        _reader(ledger, **{argument: "UNSUPPORTED"})
    signature = inspect.signature(CandidateNativeModelPortfolioReader)
    assert signature.parameters[argument].default is inspect.Parameter.empty
    assert (
        "policy" not in signature.parameters and "registry" not in signature.parameters
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"ledgers": []},
        {"expected_candidate_fingerprints": ()},
        {"expected_ledger_fingerprints": ()},
        {"expected_candidate_fingerprints": ("0" * 64,)},
        {"expected_ledger_fingerprints": ("0" * 64,)},
    ],
)
def test_wrong_or_incomplete_bindings(envelope, changes):
    with pytest.raises(CandidateModelReaderError):
        _reader(_ledger(envelope), **changes)


@pytest.mark.parametrize(
    "attack",
    [
        "missing",
        "extra",
        "duplicate",
        "reverse",
        "value",
        "boolean_value",
        "coordinate",
        "reference",
        "classification",
        "captured_version",
        "captured_value",
    ],
)
def test_recomputed_fingerprints_cannot_hide_live_or_captured_tampering(
    envelope, attack
):
    original = _ledger(envelope)
    rows = original.occurrences
    if attack in ("missing", "extra", "duplicate", "reverse"):
        rows = {
            "missing": rows[:1],
            "extra": (*rows, rows[0]),
            "duplicate": (rows[0], rows[0]),
            "reverse": rows[::-1],
        }[attack]
        forged = replace(original, occurrences=rows)
    elif attack in ("value", "boolean_value"):
        row = rows[0]
        metric = replace(
            row.metrics.metrics[0], value=True if attack == "boolean_value" else 12.0
        )
        projected = replace(row.metrics, metrics=(metric, *row.metrics.metrics[1:]))
        forged = replace(
            original, occurrences=(replace(row, metrics=projected), rows[1])
        )
    elif attack in ("coordinate", "reference", "classification"):
        candidate = original.original_candidate
        sheet = candidate.sheets[0]
        row = sheet.rows[0]
        if attack == "classification":
            row = replace(
                row,
                classification=replace(
                    row.classification, english_asset_class_candidate="Bond"
                ),
            )
        elif attack == "reference":
            row = replace(row, occurrence_id="forged")
        else:
            field = row.fields[0]
            field = replace(
                field, source_cell=replace(field.source_cell, coordinate="Z99")
            )
            row = replace(row, fields=(field, *row.fields[1:]))
        candidate = replace(
            candidate,
            sheets=(replace(sheet, rows=(row, sheet.rows[1])), candidate.sheets[1]),
        )
        forged = replace(original, original_candidate=candidate)
    else:
        payload = json.loads(original._evaluated_payload_json)
        if attack == "captured_version":
            payload["contract"]["version"] = 2
        else:
            payload["occurrences"][0]["metrics"]["metrics"][0]["value"] = 12.0
        forged = replace(original, _evaluated_payload_json=canonical_json(payload))
    with pytest.raises(CandidateModelReaderError):
        _reader(forged)  # helper supplies recomputed candidate and ledger identities


def test_inputs_and_reader_are_deeply_immutable(envelope):
    ledger = _ledger(envelope)
    original = ledger.to_json()
    reader = _reader(ledger)
    encoded = reader.to_json()
    for obj, name, value in (
        (reader, "snapshots", ()),
        (reader.snapshots[0], "dto_to_source", (1, 0)),
        (reader.snapshots[0].provenance[0], "dto_position", 4),
        (reader.snapshots[0].provenance[0].fields[0], "value", "forged"),
        (reader.snapshots[0].holdings[0], "allocation", 0.0),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, value)
    reader.load_holdings(reader.latest_observation_date()).clear()
    reader.to_dict()["snapshots"].clear()
    reader.snapshots[0].to_dict()["provenance"].clear()
    assert reader.to_json() == encoded and ledger.to_json() == original


def test_boolean_can_never_impersonate_a_live_projected_numeric_zero(envelope):
    ledger = _ledger(envelope, ORIGINAL)
    row = ledger.occurrences[0]
    metric = replace(row.metrics.metrics[0], value=False)
    forged = replace(
        ledger,
        occurrences=(
            replace(
                row,
                metrics=replace(
                    row.metrics, metrics=(metric, *row.metrics.metrics[1:])
                ),
            ),
            ledger.occurrences[1],
        ),
    )
    # Python's dataclass equality alone considers False == 0.0. Deep typed
    # validation rejects it despite unchanged captured bytes and equality.
    assert forged == ledger
    with pytest.raises(CandidateModelReaderError, match="MALFORMED_LEDGER"):
        _reader(forged)


@pytest.mark.parametrize("value", [True, float("inf"), float("nan")])
def test_malformed_typed_live_originals_reject_even_with_valid_captured_identity(
    envelope, value
):
    ledger = _ledger(envelope)
    row = ledger.occurrences[0]
    field = row.original.fields[3]
    field = replace(field, normalized_value=value)
    candidate = ledger.original_candidate
    sheet = candidate.sheets[0]
    original = replace(
        row.original, fields=(*row.original.fields[:3], field, *row.original.fields[4:])
    )
    candidate = replace(
        candidate,
        sheets=(replace(sheet, rows=(original, sheet.rows[1])), candidate.sheets[1]),
    )
    forged = replace(ledger, original_candidate=candidate)
    with pytest.raises(CandidateModelReaderError):
        _reader(
            forged,
            expected_candidate_fingerprints=(
                ledger.original_candidate.candidate_fingerprint,
            ),
        )


def test_mixed_candidates_and_live_mutable_occurrences_fail(envelope):
    ledger = _ledger(envelope)
    other = _ledger(_cell(envelope, "Termék", raw_value="Different"))
    for forged in (
        replace(ledger, occurrences=(other.occurrences[0], ledger.occurrences[1])),
        replace(ledger, occurrences=list(ledger.occurrences)),
    ):
        with pytest.raises(CandidateModelReaderError):
            _reader(forged)


def test_no_database_side_effects(envelope, monkeypatch):
    import sqlite3

    def forbidden(*args: Any, **kwargs: Any):
        raise AssertionError("reader must never open a database")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    assert len(_reader(_ledger(envelope)).load_holdings(date(2026, 1, 15))) == 2


@pytest.mark.parametrize("attribute", ["original", "metrics", "currency_risk"])
def test_mutable_impostors_cannot_spoof_nested_typed_ledger_contents(
    envelope, attribute
):
    ledger = _ledger(envelope)
    occurrence = ledger.occurrences[0]
    expected = getattr(occurrence, attribute)

    class MutableImpostor:
        # Malformed caller-owned data must be rejected even when its custom
        # equality and serialization pretend to match a validated typed result.
        def __init__(self):
            self.mutable_values = []

        def __eq__(self, other):
            return True

        def to_dict(self):
            return expected.to_dict()

    forged = replace(
        ledger,
        occurrences=(
            replace(occurrence, **{attribute: MutableImpostor()}),
            ledger.occurrences[1],
        ),
    )
    assert forged == ledger and forged.to_json() == ledger.to_json()
    with pytest.raises(CandidateModelReaderError, match="MALFORMED_LEDGER"):
        _reader(forged)


def test_live_serialized_content_still_binds_equal_but_distinct_numeric_values(
    envelope,
):
    ledger = _ledger(envelope, ORIGINAL)
    occurrence = ledger.occurrences[0]
    metric = replace(occurrence.metrics.metrics[0], value=-0.0)
    forged = replace(
        ledger,
        occurrences=(
            replace(
                occurrence,
                metrics=replace(
                    occurrence.metrics,
                    metrics=(metric, *occurrence.metrics.metrics[1:]),
                ),
            ),
            ledger.occurrences[1],
        ),
    )
    # Both values are valid floats and equal under Python comparison. Complete
    # live serialization, not the captured/recomputed fingerprint, binds the sign.
    assert forged == ledger and forged.to_json() == ledger.to_json()
    with pytest.raises(CandidateModelReaderError, match="LEDGER_CONTENT_MISMATCH"):
        _reader(forged)
