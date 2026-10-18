"""Synthetic metadata reuse, differential validation and public replay checks."""

from __future__ import annotations

import gc
import types
import weakref
from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeAliasType, get_args, get_origin
from unittest.mock import patch

import pytest

from portfolio_advisor.workbook_source import model_metric_projection as metrics
from tests.fixtures.biff_xls_fixture import write_biff_fixture
from tests.test_candidate_model_reader import _cell, _ledger, _reader

type Choice = Literal["one", "two"]


@dataclass(frozen=True)
class Leaf:
    value: float
    name: str


@dataclass(frozen=True)
class Branch:
    leaves: tuple[Leaf, ...]
    choice: Choice
    optional: Leaf | None


@dataclass(frozen=True)
class Cycle:
    child: Cycle | None


def _legacy_typed(value: object, annotation: object) -> bool:
    """Frozen pre-change algorithm from 10c269f; no metadata-result cache."""
    if isinstance(annotation, TypeAliasType):
        return _legacy_typed(value, annotation.__value__)
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is types.UnionType:
        return any(_legacy_typed(value, arg) for arg in args)
    if origin is Literal:
        return any(type(value) is type(arg) and value == arg for arg in args)
    if origin is tuple:
        return type(value) is tuple and all(
            _legacy_typed(item, args[0]) for item in value
        )
    if isinstance(annotation, type) and is_dataclass(annotation):
        return type(value) is annotation and all(
            _legacy_typed(
                getattr(value, field.name), metrics._annotations(annotation)[field.name]
            )
            for field in fields(annotation)
        )
    return type(value) is annotation


@pytest.fixture(autouse=True)
def clear_metadata_cache():
    metrics._class_type_metadata.cache_clear()
    yield
    metrics._class_type_metadata.cache_clear()


@pytest.mark.parametrize(
    "annotation",
    [
        int,
        float,
        bool,
        str,
        type(None),
        object,
        date,
        float | None,
        str | float | None,
        Literal[False, 0, "0", "one"],
        Choice,
        tuple[float, ...],
        tuple[Leaf, ...],
        Leaf,
        Branch,
        [],  # Unhashable/unsupported annotations retain their old behavior.
        {},
    ],
)
@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        True,
        0,
        1,
        0.0,
        -0.0,
        float("inf"),
        float("nan"),
        "",
        "0",
        "one",
        date(2026, 1, 15),
        (),
        (1.0, -0.0),
        (False,),
        [],
        {},
        Leaf(1.0, "one"),
        Leaf(False, "one"),  # type: ignore[arg-type]
        Branch((Leaf(1.0, "one"),), "one", None),
        Branch([Leaf(1.0, "one")], "one", None),  # type: ignore[arg-type]
    ],
)
def test_acceptance_matches_pre_change_algorithm(value, annotation):
    assert metrics._typed(value, annotation) is _legacy_typed(value, annotation)
    # Repeat on the warm metadata path without ever caching a value verdict.
    assert metrics._typed(value, annotation) is _legacy_typed(value, annotation)


def test_metadata_calls_reused_across_distinct_live_inputs():
    with (
        patch.object(metrics, "get_origin", wraps=get_origin) as origin,
        patch.object(metrics, "get_args", wraps=get_args) as args,
    ):
        first, second = Leaf(1.0, "one"), Leaf(2.0, "two")
        assert first is not second
        assert metrics._typed(first, Leaf)
        assert origin.call_count == args.call_count == 3  # Leaf, float, str
        assert metrics._typed(second, Leaf)
        assert origin.call_count == args.call_count == 3
        object.__setattr__(second, "value", False)
        assert not metrics._typed(second, Leaf)
        assert origin.call_count == args.call_count == 3
        assert not metrics._typed(Leaf(3.0, []), Leaf)  # type: ignore[arg-type]
        assert origin.call_count == args.call_count == 3


def test_cache_bound_and_eviction_do_not_change_validation():
    schemas = [type(f"SyntheticSchema{index}", (), {}) for index in range(160)]
    for schema in schemas:
        assert metrics._typed(schema(), schema)
        assert metrics._class_type_metadata.cache_info().currsize <= 128
    info = metrics._class_type_metadata.cache_info()
    assert info.maxsize == info.currsize == 128
    assert info.misses == 160
    assert metrics._typed(schemas[0](), schemas[0])
    assert not metrics._typed(object(), schemas[0])
    assert metrics._class_type_metadata.cache_info().currsize == 128


def test_cache_does_not_retain_input_instances():
    value = Leaf(1.0, "one")
    reference = weakref.ref(value)
    assert metrics._typed(value, Leaf)
    del value
    gc.collect()
    assert reference() is None


def test_recursive_values_still_raise_without_a_validity_cache():
    value = Cycle(None)
    object.__setattr__(value, "child", value)
    for validator in (_legacy_typed, metrics._typed, metrics._typed):
        with pytest.raises(RecursionError):
            validator(value, Cycle)


def test_custom_metaclass_hash_and_equality_are_never_cache_keys():
    class Meta(type):
        def __hash__(cls):
            raise AssertionError("do not hash caller metaclasses")

        def __eq__(cls, other):
            raise AssertionError("do not compare caller metaclasses")

    class Schema(metaclass=Meta):
        pass

    with patch.object(metrics, "get_args", wraps=get_args) as args:
        for _ in range(2):
            value = Schema()
            assert metrics._typed(value, Schema) == _legacy_typed(value, Schema)
        assert args.call_count == 2
    assert metrics._class_type_metadata.cache_info().currsize == 0


def test_mutable_typing_metadata_is_not_cached():
    annotation: Any = Literal["synthetic-metadata-before"]
    before = annotation.__args__
    try:
        assert metrics._typed("synthetic-metadata-before", annotation)
        annotation.__args__ = ("synthetic-metadata-after",)
        for value in ("synthetic-metadata-before", "synthetic-metadata-after"):
            assert metrics._typed(value, annotation) == _legacy_typed(value, annotation)
        assert metrics._typed("synthetic-metadata-after", annotation)
        assert not metrics._typed("synthetic-metadata-before", annotation)
        assert metrics._class_type_metadata.cache_info().currsize == 0
    finally:
        annotation.__args__ = before


def test_live_field_access_order_and_exception_are_unchanged():
    accessed = []

    @dataclass(frozen=True)
    class Watched:
        first: float
        second: float

        def __getattribute__(self, name):
            if name in ("first", "second"):
                accessed.append(name)
            if name == "second" and object.__getattribute__(self, name) == 99.0:
                raise ValueError("synthetic live field error")
            return object.__getattribute__(self, name)

    for value in (Watched(1.0, 2.0), Watched(False, 2.0), Watched(1.0, 99.0)):
        outcomes = []
        for validator in (_legacy_typed, metrics._typed, metrics._typed):
            accessed.clear()
            outcome: tuple[object, ...]
            try:
                outcome = ("returned", validator(value, Watched))
            except ValueError as error:
                outcome = ("raised", type(error), str(error))
            outcomes.append((outcome, tuple(accessed)))
        assert outcomes[0] == outcomes[1] == outcomes[2]


@pytest.mark.parametrize(
    "mode",
    [
        metrics.MODEL_METRIC_ORIGINAL_V1,
        metrics.MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1,
    ],
)
@pytest.mark.parametrize(
    "attack", [None, "metric_text", "allocation", "portfolio", "classification"]
)
def test_public_reader_replay_matches_legacy_validator(
    tmp_path: Path, monkeypatch, mode, attack
):
    from portfolio_advisor.workbook_source import parse_biff_xls

    envelope = parse_biff_xls(
        write_biff_fixture(tmp_path, model_currency_risk="Fedezve")
    )
    if attack == "metric_text":
        envelope = _cell(
            envelope, "1yr", raw_value="0", cell_type="text", biff_type_code=1
        )
    elif attack == "allocation":
        envelope = _cell(envelope, "Hányad (%)", raw_value=-1.0)
    elif attack == "portfolio":
        envelope = _cell(envelope, "Portfólió neve", raw_value="")
    elif attack == "classification":
        envelope = _cell(envelope, "Eszközosztály", raw_value="unknown synthetic label")
    before = envelope.to_json()

    def outcome():
        try:
            reader = _reader(_ledger(envelope, mode))
            return ("accepted", reader.to_json())
        except ValueError as error:
            return ("rejected", type(error), str(error))

    with monkeypatch.context() as reference:
        reference.setattr(metrics, "_typed", _legacy_typed)
        expected = outcome()
    assert outcome() == expected
    assert outcome() == expected
    assert envelope.to_json() == before
