from __future__ import annotations

import socket
from pathlib import Path

import pytest

from portfolio_advisor.history.mnb_otc_inventory import (
    build_manual_acquisition_manifest,
    inventory_local_reports,
    sha256_file,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mnb_otc_20241129_extract.txt"


def test_inventory_finds_exact_isin_report_and_hash_is_deterministic(
    tmp_path: Path,
) -> None:
    report = tmp_path / "OTC_HETI_20241129.txt"
    report.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    records = inventory_local_reports(tmp_path)

    assert len(records) == 1
    assert records[0].contains_exact_isin is True
    assert records[0].report_status == "REPORT_ACQUIRED_ISIN_PRESENT"
    assert records[0].reporting_period == ("2024-11-25", "2024-12-01")
    assert records[0].artifact_type == "TEXT_ARTIFACT_NOT_SOURCE_EVIDENCE"
    assert sha256_file(report) == sha256_file(report)


def test_inventory_ignores_unrelated_content_and_classifies_target_absence(
    tmp_path: Path,
) -> None:
    (tmp_path / "unrelated.txt").write_text("not a KELER report", encoding="utf-8")
    absent = tmp_path / "absent.txt"
    absent.write_text(
        FIXTURE.read_text(encoding="utf-8").replace("HU0000554795", "HU0000554796"),
        encoding="utf-8",
    )

    records = inventory_local_reports(tmp_path)

    assert len(records) == 1
    assert records[0].report_status == "REPORT_ACQUIRED_ISIN_ABSENT"
    assert records[0].observation is None


def test_inventory_marks_duplicate_content_and_manual_workflow_is_network_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = FIXTURE.read_text(encoding="utf-8")
    (tmp_path / "one.txt").write_text(content, encoding="utf-8")
    (tmp_path / "two.txt").write_text(content, encoding="utf-8")

    def unexpected_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("Local inventory attempted network I/O")

    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    records = inventory_local_reports(tmp_path)
    manifest = build_manual_acquisition_manifest(records)

    assert records[1].duplicate_of == records[0].relative_path
    assert (
        manifest["remote_discovery_status"]
        == "AUTOMATED_DISCOVERY_REJECTED_NO_DOCUMENTED_MACHINE_ARCHIVE"
    )
    summary = manifest["summary"]
    assert isinstance(summary, dict)
    assert summary["duplicate_report_count"] == 1
    assert summary["exact_isin_absent_reports"] == 0
