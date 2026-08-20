from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_oekb.py"
SPEC = importlib.util.spec_from_file_location("validate_oekb", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
validate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validate
SPEC.loader.exec_module(validate)


def test_main_returns_non_zero_when_any_validation_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def fake_validate(isin: str, **_kwargs: object) -> dict[str, object]:
        calls.append(isin)
        return {
            "requested_isin": isin,
            "success": isin != "AT0000605324",
            "error": "mock failure" if isin == "AT0000605324" else None,
            "merged_result": {},
            "chunks": [],
        }

    monkeypatch.setattr(validate, "validate_isin", fake_validate)
    output = tmp_path / "oekb_validation.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_oekb.py",
            "--isin",
            "AT0000605324",
            "--audit-output",
            str(output),
        ],
    )

    assert validate.main() == 1
    assert calls == ["AT0000605324"]
    assert output.exists()
