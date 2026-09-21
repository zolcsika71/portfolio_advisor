"""Portable, synthetic-only restricted-prefix persistence and isolation tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from portfolio_advisor.canonical import canonical_json
from portfolio_advisor.history import supplementary_nav as sn
from portfolio_advisor.metrics.phase_e_adapter import (
    PhaseEReadError,
    load_admitted_phase_e_nav_series,
)
from portfolio_advisor.metrics.policy_contract import (
    PHASE_F1_POLICY_ARTIFACT,
    PHASE_F1_POLICY_FINGERPRINT,
)

ROOT = Path(__file__).resolve().parents[1]


def ref(root: Path, path: str) -> sn.Reference:
    return sn.Reference(path, hashlib.sha256((root / path).read_bytes()).hexdigest())


def put(root: Path, path: str, value: Any) -> sn.Reference:
    (root / path).write_text(json.dumps(value), encoding="utf-8")
    return ref(root, path)


@pytest.fixture
def packet(tmp_path: Path) -> tuple[Path, sn.Reference, sn.Reference]:
    # Deliberately synthetic NAVs; do not copy any local retained evidence.
    dates = [date(2025, 5, 23) + timedelta(days=i) for i in range(98)]
    dates = [d for d in dates if d.weekday() < 5]
    while len(dates) > 68:
        dates.pop(1)
    assert len(dates) == 68 and dates[-1] == date(2025, 8, 28)
    times = [
        int(datetime(d.year, d.month, d.day, 18, tzinfo=UTC).timestamp()) * 1000
        for d in dates
    ]
    pairs = ",".join(f"[{t},1.234500]" for t in times)
    (tmp_path / "chart.json").write_text(
        '{"isin":"HU0000722442","id":"11002",'
        '"instrument_id":"11002","series":['
        + ",".join(["[0,1]"] * 1368)
        + ","
        + pairs
        + "]}"
    )
    chart = ref(tmp_path, "chart.json")
    receipt: dict[str, Any] = {key: None for key in sn.ERSTE_MARKET_TRANSPORT_FIELDS}
    receipt.update(
        schema_version=1,
        byte_count=(tmp_path / chart.path).stat().st_size,
        raw_artifact_reference=chart.path,
        raw_artifact_sha256=chart.sha256,
        requested_isin="HU0000722442",
        provider="ERSTE_MARKET_APPROVED_NAV",
        body_complete=True,
        http_status=200,
        redirect_history=[],
        request_role="series",
        retrieval_timestamp="2026-09-03T17:00:00Z",
        content_type="text/html; charset=UTF-8",
        retention_status="QUARANTINED_RESPONSE",
        max_response_bytes=8388608,
        requested_url="https://www.erstemarket.hu/funds/chart/11002",
        final_url="https://www.erstemarket.hu/funds/chart/11002",
        response_headers={},
    )
    transport = put(tmp_path, "transport.json", receipt)
    (tmp_path / "notice.pdf").write_bytes(b"Synthetic unparsed PDF context only")
    notice = ref(tmp_path, "notice.pdf")
    sources = [
        {"source_id": sid, "path": r.path, "sha256": r.sha256}
        for sid, r in [
            ("retained-chart", chart),
            ("chart-transport-receipt", transport),
            ("july-2025-issuer-notice", notice),
        ]
    ]
    rows = []
    for i, (day, timestamp) in enumerate(zip(dates, times, strict=True)):
        row: dict[str, Any] = {
            "source_locator": f"/series/{1368 + i}",
            "raw_timestamp": timestamp,
            "valuation_date": day.isoformat(),
            "numeric_token": "1.234500",
            "decimal_value": "1.234500",
            "revision_status": "UNSPECIFIED",
            "source_id": "retained-chart",
            "identity_group": "synthetic-key",
        }
        if day.month == 7 and day.day in (1, 2, 3, 4, 7, 8, 9, 10):
            row["numerical_exception"] = {
                "comparison": "DIFFERS_FROM_ISSUER_REPORTED_CORRECTED_VALUE",
                "notice_source_id": "july-2025-issuer-notice",
                "currency": "EUR",
                "replacement_selected": False,
                "notice_locator": f"PDF p.1 synthetic HU0000722442 {day}",
                "issuer_reported_corrected_value": "1.2300",
            }
        rows.append(row)
    scope = {
        "isin": sn.SCOPE["isin"],
        "share_class": sn.SCOPE["share_class"],
        "currency": "EUR",
        "chart_identity": "11002",
        "start_inclusive": "2025-05-23",
        "end_inclusive": "2025-08-28",
        "verified_observation_count": 68,
        "source_sha256": chart.sha256,
    }
    a = {
        "scope": scope,
        "document_character": {"validator_executable": False, "activating": False},
        "overall_conclusion": {"required_semantic_attributes_unresolved": []},
        "created_at_utc": "2026-09-17T00:00:00Z",
        "reviewed_at_utc": "2026-09-17T01:00:00Z",
        "source_inventory": sources,
        "occurrence_inventory": rows,
        "identity_groups": [
            {
                **{
                    k: sn.SCOPE[k]
                    for k in (
                        "isin",
                        "share_class",
                        "chart_identity",
                        "currency",
                        "unit",
                        "value_type",
                        "precision_basis",
                    )
                },
                "identity_group": "synthetic-key",
            }
        ],
    }
    assessment = put(tmp_path, "assessment.json", a)
    (tmp_path / "f1.yaml").write_bytes((ROOT / PHASE_F1_POLICY_ARTIFACT).read_bytes())
    parent = ref(tmp_path, "f1.yaml")
    policy = put(
        tmp_path,
        "policy.json",
        {
            "schema_version": 1,
            "policy_id": sn.POLICY_ID,
            "version": sn.VERSION,
            "recorded_at_utc": "2026-09-17T01:00:00Z",
            "decision": "USER_APPROVED_RESTRICTED_EVIDENCE_ONLY_DIRECTION",
            "approval_timestamp": None,
            "authenticated_owner": None,
            "execution_authorized": False,
            "parent": {
                "artifact": vars(parent),
                "canonical_fingerprint": PHASE_F1_POLICY_FINGERPRINT,
            },
            "scope": sn.SCOPE,
            "chart": vars(chart),
            "assessment": vars(assessment),
            "restrictions": sn.RESTRICTIONS,
        },
    )
    with sqlite3.connect(tmp_path / "baseline.sqlite") as conn:
        conn.execute(
            "CREATE TABLE nav_import_manifest (exact_isin,manifest_fingerprint,dataset_fingerprint,"
            "series_raw_artifact_sha256,series_retrieval_timestamp,admitted_first_date,"
            "admitted_observation_count,nav_currency,provider_instrument_id)"
        )
        conn.execute(
            "INSERT INTO nav_import_manifest VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "HU0000722442",
                "a" * 64,
                "b" * 64,
                chart.sha256,
                receipt["retrieval_timestamp"],
                "2025-08-29",
                249,
                "EUR",
                "11002",
            ),
        )
        conn.execute(
            "CREATE TABLE nav_observation_version (exact_isin,observation_date)"
        )
        conn.executemany(
            "INSERT INTO nav_observation_version VALUES ('HU0000722442',?)",
            [
                ((date(2025, 8, 29) + timedelta(days=367 * i // 248)).isoformat(),)
                for i in range(249)
            ],
        )
    return tmp_path, policy, ref(tmp_path, "baseline.sqlite")


def plan(packet: tuple[Path, sn.Reference, sn.Reference]) -> sn.AdmissionPlan:
    root, policy, baseline = packet
    return sn.plan_supplementary_admission(
        repository_root=root, policy=policy, baseline=baseline
    )


def auth(p: sn.AdmissionPlan, target: Path) -> sn.ExecutionAuthorization:
    return sn.ExecutionAuthorization(
        p.fingerprint,
        p.policy.sha256,
        target,
        hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None,
        "SYNTHETIC_OWNER_CONTROLLED_TEST",
    )


def execute(
    packet: tuple[Path, sn.Reference, sn.Reference], p: sn.AdmissionPlan | None = None
) -> sn.RestrictedAdmission:
    p = p or plan(packet)
    target = packet[0] / "restricted.sqlite"
    return sn.execute_supplementary_admission(
        repository_root=packet[0], plan=p, target=target, authorization=auth(p, target)
    )


def rewrite_as_legacy_admission(target: Path) -> None:
    """Convert a synthetic current record to the exact retained v1 record shape."""
    with sqlite3.connect(target) as connection:
        for ddl in sn._DDL[3:5]:
            trigger_name = ddl.split()[2]
            connection.execute(f"DROP TRIGGER {trigger_name}")
        record = json.loads(
            connection.execute(
                "SELECT record_json FROM restricted_admission"
            ).fetchone()[0]
        )
        record["schema_version"] = sn.LEGACY_ADMISSION_SCHEMA_VERSION
        del record["initial_target_sha256"]
        connection.execute(
            "UPDATE restricted_admission SET record_json=?", (canonical_json(record),)
        )
        for ddl in sn._DDL[3:5]:
            connection.execute(ddl)


def rebind(
    packet: tuple[Path, sn.Reference, sn.Reference], name: str, value: Any
) -> tuple[Path, sn.Reference, sn.Reference]:
    root, _, baseline = packet
    updated = put(root, name, value)
    policy = json.loads((root / "policy.json").read_bytes())
    if name == "assessment.json":
        policy["assessment"] = vars(updated)
    elif name == "policy.json":
        policy = value
    return root, put(root, "policy.json", policy), baseline


def test_complete_plan_nonwriting_and_immutable(packet: Any) -> None:
    root = packet[0]
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    p = plan(packet)
    result = p.preview()
    assert result["executed"] is False and result["execution_authorized"] is False
    assert (
        len(result["plan"]["observations"]) == 68
        and len(result["plan"]["discrepancies"]) == 8
    )
    assert result["plan"]["observations"][0]["numeric_token"] == "1.234500"
    assert result["plan"]["numerical_correctness"] == "UNESTABLISHED_ALL_68"
    assert all(
        r["kind"] == "UNADMITTED_ISSUER_VALUE_COMPARISON"
        for r in result["plan"]["discrepancies"]
    )
    result["plan"]["observations"].clear()
    assert len(p.preview()["plan"]["observations"]) == 68
    with pytest.raises(FrozenInstanceError):
        p.payload_json = "{}"  # type: ignore[misc]
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing", "all 68"),
        ("extra", "all 68"),
        ("duplicate", "occurrence/token"),
        ("value", "occurrence/token"),
        ("date", "occurrence/token"),
        ("identity", "scope mismatch"),
        ("currency", "scope mismatch"),
        ("unresolved", "unresolved observation"),
        ("discrepancy", "all eight"),
        ("replacement", "replacement semantics"),
        ("notice", "discrepancy binding"),
    ],
)
def test_inventory_rejections(packet: Any, change: str, reason: str) -> None:
    a = json.loads((packet[0] / "assessment.json").read_bytes())
    rows = a["occurrence_inventory"]
    if change == "missing":
        rows.pop()
    elif change == "extra":
        rows.append(rows[-1])
    elif change == "duplicate":
        rows[1] = rows[0]
    elif change == "value":
        rows[0]["numeric_token"] = "1.2"
    elif change == "date":
        rows[0]["valuation_date"] = "2025-05-24"
    elif change == "identity":
        a["scope"]["isin"] = "XX0000000000"
    elif change == "currency":
        a["scope"]["currency"] = "HUF"
    elif change == "unresolved":
        a["overall_conclusion"]["required_semantic_attributes_unresolved"] = ["unit"]
    else:
        r = next(r for r in rows if "numerical_exception" in r)
        if change == "discrepancy":
            del r["numerical_exception"]
        elif change == "replacement":
            r["numerical_exception"]["replacement_selected"] = True
        else:
            r["numerical_exception"]["notice_locator"] = "unrelated"
    packet = rebind(packet, "assessment.json", a)
    with pytest.raises(sn.SupplementaryError, match=reason):
        plan(packet)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("version", "2.0.0", "version"),
        ("schema_version", True, "version"),
        ("execution_authorized", True, "grant execution"),
        ("restrictions", [], "restrictions"),
    ],
)
def test_policy_rejections(packet: Any, field: str, value: Any, reason: str) -> None:
    policy = json.loads((packet[0] / "policy.json").read_bytes())
    policy[field] = value
    with pytest.raises(sn.SupplementaryError, match=reason):
        plan(rebind(packet, "policy.json", policy))


@pytest.mark.parametrize(
    "name", ["chart.json", "transport.json", "assessment.json", "notice.pdf", "f1.yaml"]
)
def test_tampered_dependencies(packet: Any, name: str) -> None:
    p = plan(packet)
    (packet[0] / name).write_bytes((packet[0] / name).read_bytes() + b" ")
    with pytest.raises(sn.SupplementaryError, match="hash mismatch"):
        execute(packet, p)
    assert not (packet[0] / "restricted.sqlite").exists()


def test_stale_plan_missing_authorization_wrong_target(packet: Any) -> None:
    root = packet[0]
    p = plan(packet)
    target = root / "restricted.sqlite"
    with pytest.raises(sn.SupplementaryError, match="trusted execution"):
        sn.execute_supplementary_admission(
            repository_root=root, plan=p, target=target, authorization=None
        )
    payload = p.preview()["plan"]
    payload["observations"].pop()
    altered = replace(p, payload_json=canonical_json(payload))
    with pytest.raises(sn.SupplementaryError, match="stale/altered"):
        execute(packet, altered)
    with pytest.raises(sn.SupplementaryError, match="authorization binding"):
        sn.execute_supplementary_admission(
            repository_root=root,
            plan=p,
            target=target,
            authorization=auth(p, root / "wrong.sqlite"),
        )
    assert not target.exists()


def test_atomic_write_retry_exact_readback_and_no_projection(packet: Any) -> None:
    root = packet[0]
    before = (root / "baseline.sqlite").read_bytes()
    p = plan(packet)
    target = root / "restricted.sqlite"
    authorization = auth(p, target)
    result = sn.execute_supplementary_admission(
        repository_root=root,
        plan=p,
        target=target,
        authorization=authorization,
    )
    after = target.read_bytes()
    assert result.schema_version == sn.CURRENT_ADMISSION_SCHEMA_VERSION
    assert result.initial_target_sha256 is None
    assert result.initial_target_sha256_known is True
    assert (
        sn.execute_supplementary_admission(
            repository_root=root,
            plan=p,
            target=target,
            authorization=authorization,
        )
        == result
    )
    assert target.read_bytes() == after
    assert (root / "baseline.sqlite").read_bytes() == before
    actual = sn.inspect_restricted_admission(
        repository_root=root, plan=p, store=ref(root, target.name)
    )
    assert actual == result and actual.status == sn.STATUS
    with sqlite3.connect(target) as c:
        assert [
            c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in (
                "restricted_admission",
                "restricted_observation",
                "restricted_discrepancy",
            )
        ] == [1, 68, 8]
        assert (
            c.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('nav_observation_version','nav_import_manifest')"
            ).fetchall()
            == []
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("UPDATE restricted_observation SET status='ADMITTED_VALIDATED'")
    for record in (p, result):
        with pytest.raises(sn.SupplementaryError, match="cannot be exported"):
            record.for_computation()


def test_legacy_v1_admission_is_readable_but_not_retryable(packet: Any) -> None:
    root = packet[0]
    p = plan(packet)
    target = root / "restricted.sqlite"
    original_authorization = auth(p, target)
    sn.execute_supplementary_admission(
        repository_root=root,
        plan=p,
        target=target,
        authorization=original_authorization,
    )
    rewrite_as_legacy_admission(target)
    before = target.read_bytes()

    inspected = sn.inspect_restricted_admission(
        repository_root=root,
        plan=p,
        store=ref(root, target.name),
    )

    assert inspected.schema_version == sn.LEGACY_ADMISSION_SCHEMA_VERSION
    assert inspected.initial_target_sha256 is None
    assert inspected.initial_target_sha256_known is False
    assert target.read_bytes() == before
    for expected_hash in (None, "a" * 64):
        with pytest.raises(
            sn.SupplementaryError,
            match="initial target hash is unknown; retry is not authorized",
        ):
            sn.execute_supplementary_admission(
                repository_root=root,
                plan=p,
                target=target,
                authorization=replace(
                    original_authorization,
                    expected_initial_target_sha256=expected_hash,
                ),
            )
        assert target.read_bytes() == before


def test_exact_retry_rejects_wrong_or_post_write_hash_without_changes(
    packet: Any,
) -> None:
    root = packet[0]
    p = plan(packet)
    target = root / "restricted.sqlite"
    authorization = auth(p, target)
    sn.execute_supplementary_admission(
        repository_root=root,
        plan=p,
        target=target,
        authorization=authorization,
    )
    before = target.read_bytes()

    for substituted_hash in ("a" * 64, hashlib.sha256(before).hexdigest()):
        with pytest.raises(sn.SupplementaryError, match="target baseline mismatch"):
            sn.execute_supplementary_admission(
                repository_root=root,
                plan=p,
                target=target,
                authorization=replace(
                    authorization,
                    expected_initial_target_sha256=substituted_hash,
                ),
            )

        assert target.read_bytes() == before
    with pytest.raises(sn.SupplementaryError, match="authorization binding mismatch"):
        sn.execute_supplementary_admission(
            repository_root=root,
            plan=p,
            target=target,
            authorization=replace(
                authorization,
                authorization_reference="different-authorized-task",
            ),
        )
    assert target.read_bytes() == before
    with sqlite3.connect(target) as connection:
        assert [
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "restricted_admission",
                "restricted_observation",
                "restricted_discrepancy",
            )
        ] == [1, 68, 8]


def test_target_changed_after_authorization_is_rejected_without_changes(
    packet: Any,
) -> None:
    root = packet[0]
    p = plan(packet)
    target = root / "restricted.sqlite"
    with sqlite3.connect(target):
        pass
    authorization = auth(p, target)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA user_version=1")
    changed = target.read_bytes()

    with pytest.raises(sn.SupplementaryError, match="target baseline mismatch"):
        sn.execute_supplementary_admission(
            repository_root=root,
            plan=p,
            target=target,
            authorization=authorization,
        )

    assert target.read_bytes() == changed
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_rollback_mid_insert(packet: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    original = sn._stored
    calls = 0

    def fail_after_inserts(conn: sqlite3.Connection, p: sn.AdmissionPlan) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated failure before commit")
        return original(conn, p)

    monkeypatch.setattr(sn, "_stored", fail_after_inserts)
    with pytest.raises(RuntimeError, match="before commit"):
        execute(packet)
    with sqlite3.connect(packet[0] / "restricted.sqlite") as c:
        assert c.execute("SELECT name FROM sqlite_master").fetchall() == []
    monkeypatch.setattr(sn, "_stored", original)
    assert execute(packet).status == sn.STATUS


def test_existing_operational_database_rejected_without_changes(packet: Any) -> None:
    p = plan(packet)
    target = packet[0] / "foreign.sqlite"
    target.write_bytes((packet[0] / "baseline.sqlite").read_bytes())
    before = target.read_bytes()
    with pytest.raises(sn.SupplementaryError, match="isolated restricted"):
        sn.execute_supplementary_admission(
            repository_root=packet[0],
            plan=p,
            target=target,
            authorization=auth(p, target),
        )
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "promoted",
        "partial",
        "collision",
        "boolean_version",
        "missing_initial_hash",
        "invalid_initial_hash",
        "legacy_extra_hash",
        "unknown_schema_version",
    ],
)
def test_no_serialization_defaults_or_partial_retry(packet: Any, damage: str) -> None:
    p = plan(packet)
    execute(packet, p)
    target = packet[0] / "restricted.sqlite"
    with sqlite3.connect(target) as c:
        # Simulate hostile/corrupt persistence, restoring exact schema afterwards.
        for table in ("restricted_admission", "restricted_observation"):
            c.execute(f"DROP TRIGGER {table}_update")
            c.execute(f"DROP TRIGGER {table}_delete")
        record = json.loads(
            c.execute("SELECT record_json FROM restricted_admission").fetchone()[0]
        )
        if damage == "missing":
            del record["status"]
        elif damage == "promoted":
            record["status"] = "ADMITTED_VALIDATED"
        elif damage == "partial":
            c.execute(
                "DELETE FROM restricted_observation WHERE locator=(SELECT min(locator) FROM restricted_observation)"
            )
        elif damage == "boolean_version":
            record["plan"]["contract_version"] = True
        elif damage == "missing_initial_hash":
            del record["initial_target_sha256"]
        elif damage == "invalid_initial_hash":
            record["initial_target_sha256"] = "not-a-sha256"
        elif damage == "legacy_extra_hash":
            record["schema_version"] = sn.LEGACY_ADMISSION_SCHEMA_VERSION
        elif damage == "unknown_schema_version":
            record["schema_version"] = 3
        else:
            c.execute("UPDATE restricted_admission SET id=?", ("f" * 64,))
        c.execute(
            "UPDATE restricted_admission SET record_json=?", (canonical_json(record),)
        )
        for ddl in sn._DDL[3:7]:
            c.execute(ddl)
    with pytest.raises(sn.SupplementaryError):
        execute(packet, p)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_live_sidecar_rejected(packet: Any, suffix: str) -> None:
    (packet[0] / ("baseline.sqlite" + suffix)).write_bytes(b"not inspected")
    with pytest.raises(sn.SupplementaryError, match="sidecar"):
        plan(packet)


def test_paths_json_and_cutoff(packet: Any) -> None:
    with pytest.raises(sn.SupplementaryError, match="normalized"):
        sn.Reference("../chart.json", "a" * 64)
    path = packet[0] / "alias"
    path.symlink_to(packet[0], target_is_directory=True)
    with pytest.raises(sn.SupplementaryError, match="symlink"):
        sn._read(
            packet[0],
            sn.Reference("alias/chart.json", ref(packet[0], "chart.json").sha256),
        )
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1.2}'):
        with pytest.raises(sn.SupplementaryError):
            sn._json(raw)
    a = json.loads((packet[0] / "assessment.json").read_bytes())
    receipt = json.loads((packet[0] / "transport.json").read_bytes())
    receipt["retrieval_timestamp"] = "2026-09-05T00:00:00Z"
    r = put(packet[0], "transport.json", receipt)
    a["source_inventory"][1]["sha256"] = r.sha256
    with pytest.raises(sn.SupplementaryError, match="after historical cutoff"):
        plan(rebind(packet, "assessment.json", a))


def test_real_phase_e_adapter_rejects_restricted_store(packet: Any) -> None:
    execute(packet)
    # Actual adapter/validator invoked, no mocked success or installed files.
    with pytest.raises(PhaseEReadError, match="validation failed") as error:
        load_admitted_phase_e_nav_series(
            packet[0] / "restricted.sqlite",
            exact_isin="HU0000722442",
            repository_root=packet[0],
            phase_e_index_path=packet[0] / "no-operational-index.json",
        )
    assert isinstance(error.value.__cause__, sqlite3.OperationalError)
    assert "shortlist_snapshot" in str(error.value.__cause__)


def test_target_baseline_stale_baseline_and_valid_plan_collision(packet: Any) -> None:
    root = packet[0]
    p = plan(packet)
    target = root / "restricted.sqlite"
    with pytest.raises(sn.SupplementaryError, match="target baseline mismatch"):
        sn.execute_supplementary_admission(
            repository_root=root,
            plan=p,
            target=target,
            authorization=replace(
                auth(p, target), expected_initial_target_sha256="a" * 64
            ),
        )
    # A rejected baseline does not create a target or an admitted prefix.
    assert not target.exists()
    execute(packet, p)
    old = target.read_bytes()
    policy = json.loads((root / "policy.json").read_bytes())
    policy["recorded_at_utc"] = "2026-09-17T02:00:00Z"
    packet = rebind(packet, "policy.json", policy)
    with pytest.raises(sn.SupplementaryError, match="collision"):
        execute(packet)
    assert target.read_bytes() == old
    (root / "baseline.sqlite").write_bytes(
        (root / "baseline.sqlite").read_bytes() + b"x"
    )
    with pytest.raises(sn.SupplementaryError, match="hash mismatch"):
        plan(packet)


def test_no_f2_intervals_or_metrics_from_restricted_record(
    packet: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from portfolio_advisor.metrics import governed
    from portfolio_advisor.metrics.policy_contract import (
        load_phase_f1_portfolio_metrics_policy,
    )

    policy = load_phase_f1_portfolio_metrics_policy(ROOT / PHASE_F1_POLICY_ARTIFACT)
    result = execute(packet)
    invalid_series: Any = result

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("interval construction must not run")

    monkeypatch.setattr(governed, "_construct_intervals", forbidden)
    with pytest.raises(ValueError, match="governed metric series type required"):
        governed.build_observed_return_intervals(invalid_series, policy=policy)
    with pytest.raises(TypeError, match="governed metric series type required"):
        governed.compute_governed_metrics(
            series=invalid_series,
            policy=policy,
            requested_metrics=("TOTAL_RETURN",),
        )
    with pytest.raises(ValueError):
        governed.SourceApprovalState(sn.STATUS)


def test_f3a_rejects_restricted_constituents(packet: Any) -> None:
    from decimal import Decimal

    from portfolio_advisor.metrics.policy_contract import (
        load_phase_f1_portfolio_metrics_policy,
    )
    from portfolio_advisor.metrics.portfolio_wealth import (
        PhaseF3AValidationError,
        SyntheticPortfolioWealthRequest,
        build_synthetic_eur_portfolio_wealth,
    )
    from portfolio_advisor.objectives.construction_policy import (
        CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
        load_capital_defensive_construction_policy,
    )

    result = execute(packet)
    request = SyntheticPortfolioWealthRequest(
        portfolio_identity="SYNTHETIC_RESTRICTED_REJECTION",
        initial_capital=Decimal(1000),
        decision_as_of_utc="2026-09-04T12:24:23.000000Z",
        nav_evidence_cutoff="2026-08-31",
        constituents=(result,) * 8,  # type: ignore[arg-type]
    )
    with pytest.raises(PhaseF3AValidationError, match="synthetic series type"):
        build_synthetic_eur_portfolio_wealth(
            request=request,
            metrics_policy=load_phase_f1_portfolio_metrics_policy(
                ROOT / PHASE_F1_POLICY_ARTIFACT
            ),
            construction_policy=load_capital_defensive_construction_policy(
                ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
            ),
        )


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "wrong_identity"])
def test_raw_prefix_set_rejected_even_after_rebinding(packet: Any, change: str) -> None:
    root = packet[0]
    chart = json.loads((root / "chart.json").read_bytes(), parse_float=str)
    if change == "missing":
        chart["series"].pop()
    elif change == "extra":
        chart["series"].append(chart["series"][-1])
    elif change == "duplicate":
        chart["series"][1369] = chart["series"][1368]
    else:
        chart["isin"] = "XX0000000000"
    pairs = ",".join(f"[{row[0]},{row[1]}]" for row in chart["series"])
    (root / "chart.json").write_text(
        '{"isin":' + json.dumps(chart["isin"]) + ',"id":"11002",'
        '"instrument_id":"11002","series":[' + pairs + "]}"
    )
    changed = ref(root, "chart.json")
    receipt = json.loads((root / "transport.json").read_bytes())
    receipt["byte_count"] = (root / "chart.json").stat().st_size
    receipt["raw_artifact_sha256"] = changed.sha256
    transport = put(root, "transport.json", receipt)
    a = json.loads((root / "assessment.json").read_bytes())
    a["scope"]["source_sha256"] = changed.sha256
    a["source_inventory"][0]["sha256"] = changed.sha256
    a["source_inventory"][1]["sha256"] = transport.sha256
    packet = rebind(packet, "assessment.json", a)
    p = json.loads((root / "policy.json").read_bytes())
    p["chart"] = vars(changed)
    with pytest.raises(
        sn.SupplementaryError, match="identity mismatch|complete 68-occurrence"
    ):
        plan(rebind(packet, "policy.json", p))


def test_decimal_context_and_order_are_not_selection_or_rounding(packet: Any) -> None:
    from decimal import localcontext

    ordinary = plan(packet)
    with localcontext() as context:
        context.prec = 2
        assert plan(packet) == ordinary
    assert all(
        r["numeric_token"] == "1.234500"
        for r in ordinary.preview()["plan"]["observations"]
    )


@pytest.mark.parametrize(
    "field", ["plan_fingerprint", "policy_sha256", "authorization_reference"]
)
def test_wrong_authorization_bindings_preflight(packet: Any, field: str) -> None:
    p = plan(packet)
    target = packet[0] / "restricted.sqlite"
    value: Any = 123 if field == "authorization_reference" else "0" * 64
    with pytest.raises(sn.SupplementaryError, match="authorization binding"):
        sn.execute_supplementary_admission(
            repository_root=packet[0],
            plan=p,
            target=target,
            authorization=replace(auth(p, target), **{field: value}),
        )
    assert not target.exists()
