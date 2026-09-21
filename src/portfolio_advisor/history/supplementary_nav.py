"""Scoped, evidence-only supplementary NAV plans and isolated append-only storage.

No default path, CLI, registry, Phase E projection or computation adapter exists.
The planner checks bytes and recorded relationships, not documentary truth.
ExecutionAuthorization is a TRUST INPUT from owner-controlled code/configuration:
constructing it or matching hashes does not authenticate authority. Never build
it from an evidence file, preview, directory discovery or self-declared owner.
The local filesystem is operator-controlled; symlinks and live WAL/journals are
rejected, not handled through adversarial filesystem-race infrastructure.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.history.nav_correction_evidence import (
    ERSTE_MARKET_TRANSPORT_FIELDS,
)
from portfolio_advisor.metrics.policy_contract import (
    load_phase_f1_portfolio_metrics_policy,
)

POLICY_ID = "HU0000722442_RESTRICTED_SUPPLEMENTARY_NAV"
VERSION = "1.0.0"
STATUS = "ADMITTED_EVIDENCE_ONLY_NO_COMPUTATION"
LEGACY_ADMISSION_SCHEMA_VERSION = 1
CURRENT_ADMISSION_SCHEMA_VERSION = 2
SCOPE = {
    "isin": "HU0000722442",
    "share_class": "Erste Euro Ingatlan Alap T180",
    "currency": "EUR",
    "chart_identity": "11002",
    "unit": "PER_UNIT",
    "value_type": "NAV",
    "precision_basis": "PUBLISHED_DECIMAL_TEXT",
    "start": "2025-05-23",
    "end": "2025-08-28",
    "count": 68,
    "first_locator": 1368,
    "last_locator": 1435,
    "discrepancy_count": 8,
    "cutoff": "2026-09-04T12:24:23Z",
}
RESTRICTIONS = (
    "NO_NUMERICAL_ENDORSEMENT",
    "NO_COMPUTATION",
    "NO_PHASE_E_PROJECTION",
    "NO_CORRECTION_ADMISSION",
    "NO_REVISION_LINEAGE_INFERENCE",
    "NO_DISTRIBUTION_OR_SETTLEMENT_CONCLUSION",
    "NO_PROMOTION_API",
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


class SupplementaryError(ValueError):
    """Precise fail-closed planning, persistence or restricted-use error."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise SupplementaryError(reason)


def _keys(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    _require(type(value) is dict and set(value) == keys, f"{label}: unsupported fields")
    return dict(value)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bad_number(value: str) -> NoReturn:
    raise SupplementaryError(f"unsupported JSON number: {value}")


class _Token(str):
    """A JSON numeric lexeme, never a binary float."""


def _json(raw: bytes, *, chart: bool = False) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_float=_Token if chart else _bad_number,
            parse_constant=_bad_number,
        )
    except (ValueError, UnicodeError) as exc:
        raise SupplementaryError(f"invalid JSON: {exc}") from exc


def _time(value: Any) -> datetime:
    _require(type(value) is str, "timestamp must be text")
    try:
        parsed = datetime.fromisoformat(value)
        _require(parsed.utcoffset() == timedelta(0), "timestamp must be UTC")
        return parsed
    except ValueError as exc:
        raise SupplementaryError("invalid UTC timestamp") from exc


def _decimal(token: Any) -> Decimal:
    _require(
        isinstance(token, str) and _NUMBER.fullmatch(token) is not None,
        "value must be exact finite JSON decimal text",
    )
    value = Decimal(token)
    _require(value.is_finite() and value > 0, "NAV must be positive and finite")
    return value


def _safe_absolute(path: Path) -> Path:
    _require(
        path.is_absolute() and ".." not in path.parts,
        "absolute normalized path required",
    )
    for part in (path, *path.parents):
        _require(not part.is_symlink(), "symlink path forbidden")
    return path


@dataclass(frozen=True)
class Reference:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        _require(type(self.path) is str, "reference path must be text")
        p = PurePosixPath(self.path)
        _require(
            bool(self.path)
            and not p.is_absolute()
            and str(p) == self.path
            and ".." not in p.parts
            and "\\" not in self.path,
            "reference must be normalized repository-relative path",
        )
        _require(
            type(self.sha256) is str and _HASH.fullmatch(self.sha256) is not None,
            "invalid raw SHA-256",
        )


def _ref(value: Any) -> Reference:
    d = _keys(value, {"path", "sha256"}, "reference")
    _require(type(d["path"]) is str, "reference path must be text")
    return Reference(**d)


def _read(root: Path, ref: Reference) -> bytes:
    path = _safe_absolute(root.absolute() / ref.path)
    raw = path.read_bytes()
    _require(
        hashlib.sha256(raw).hexdigest() == ref.sha256, f"hash mismatch: {ref.path}"
    )
    return raw


def _snapshot(root: Path, ref: Reference) -> tuple[bytes, sqlite3.Connection]:
    path = _safe_absolute(root.absolute() / ref.path)
    _require(
        not any(Path(str(path) + s).exists() for s in ("-wal", "-shm", "-journal")),
        "database sidecar present: consistent offline snapshot required",
    )
    raw = _read(root, ref)
    _require(
        raw[:16] == b"SQLite format 3\0" and raw[18:20] == b"\x01\x01",
        "only complete rollback-journal SQLite snapshots supported",
    )
    conn = sqlite3.connect(":memory:")
    conn.deserialize(raw)
    conn.execute("PRAGMA query_only=ON")
    return raw, conn


@dataclass(frozen=True)
class AdmissionPlan:
    """Immutable proposal only. No plan deserializer grants execution authority."""

    policy: Reference
    baseline: Reference
    payload_json: str

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(_json(self.payload_json.encode()))

    def preview(self) -> dict[str, Any]:
        return {
            "record_type": "SUPPLEMENTARY_NAV_NON_EXECUTED_PREVIEW",
            "schema_version": 1,
            "executed": False,
            "execution_authorized": False,
            "current_prefix_status": "APPROVED_NOT_ADMITTED",
            "plan_fingerprint": self.fingerprint,
            "plan": _json(self.payload_json.encode()),
        }

    def for_computation(self) -> NoReturn:
        raise SupplementaryError(
            "restricted evidence cannot be exported for computation"
        )


def _plan_supplementary_admission(
    *,
    repository_root: Path,
    policy: Reference,
    baseline: Reference,
) -> AdmissionPlan:
    """Non-writing exact-prefix preview; never opens a disk database with SQLite.

    The policy binds a prior documentary review. No PDF meaning is machine
    adjudicated here. Current policy/assessment/source bytes are all re-read.
    """
    p = _keys(
        _json(_read(repository_root, policy)),
        {
            "schema_version",
            "policy_id",
            "version",
            "recorded_at_utc",
            "decision",
            "approval_timestamp",
            "authenticated_owner",
            "execution_authorized",
            "parent",
            "scope",
            "chart",
            "assessment",
            "restrictions",
        },
        "policy",
    )
    _require(
        type(p["schema_version"]) is int
        and p["schema_version"] == 1
        and p["policy_id"] == POLICY_ID
        and p["version"] == VERSION,
        "unsupported policy identity/version",
    )
    _time(p["recorded_at_utc"])
    _require(
        p["approval_timestamp"] is None
        and p["authenticated_owner"] is None
        and p["execution_authorized"] is False,
        "policy text cannot grant execution or authenticate owner",
    )
    _require(
        p["decision"] == "USER_APPROVED_RESTRICTED_EVIDENCE_ONLY_DIRECTION",
        "unsupported policy decision",
    )
    _require(
        canonical_json(p["scope"]) == canonical_json(SCOPE), "policy scope mismatch"
    )
    _require(p["restrictions"] == list(RESTRICTIONS), "required restrictions mismatch")
    parent = _keys(p["parent"], {"artifact", "canonical_fingerprint"}, "parent")
    parent_ref = _ref(parent["artifact"])
    _read(repository_root, parent_ref)
    f1 = load_phase_f1_portfolio_metrics_policy(repository_root / parent_ref.path)
    _require(
        f1.fingerprint == parent["canonical_fingerprint"], "F1 fingerprint mismatch"
    )
    assessment_ref = _ref(p["assessment"])
    a = _json(_read(repository_root, assessment_ref))
    chart_ref = _ref(p["chart"])
    raw = _read(repository_root, chart_ref)
    chart = _json(raw, chart=True)
    scope = a["scope"]
    for field, expected in {
        "isin": SCOPE["isin"],
        "share_class": SCOPE["share_class"],
        "currency": "EUR",
        "chart_identity": "11002",
        "start_inclusive": SCOPE["start"],
        "end_inclusive": SCOPE["end"],
        "verified_observation_count": 68,
        "source_sha256": chart_ref.sha256,
    }.items():
        _require(
            type(scope[field]) is type(expected) and scope[field] == expected,
            f"assessment scope mismatch: {field}",
        )
    _require(
        a["document_character"]["validator_executable"] is False
        and a["document_character"]["activating"] is False,
        "assessment must remain non-executable/non-activating",
    )
    _require(
        a["overall_conclusion"]["required_semantic_attributes_unresolved"] == [],
        "unresolved observation semantics",
    )
    _time(a["created_at_utc"])
    _time(a["reviewed_at_utc"])
    sources: dict[str, dict[str, Any]] = {}
    dependencies: list[dict[str, Any]] = []
    for entry in a["source_inventory"]:
        source_id = entry["source_id"]
        _require(source_id not in sources, "duplicate assessment source ID")
        ref = Reference(entry["path"], entry["sha256"])
        content = _read(repository_root, ref)
        sources[source_id] = entry
        dependencies.append(
            {"path": ref.path, "sha256": ref.sha256, "byte_count": len(content)}
        )
    _require(
        sources["retained-chart"]["path"] == chart_ref.path
        and sources["retained-chart"]["sha256"] == chart_ref.sha256,
        "assessment/chart binding mismatch",
    )
    group = a["identity_groups"]
    _require(len(group) == 1, "ambiguous full-key semantic attribution")
    for field in (
        "isin",
        "share_class",
        "chart_identity",
        "currency",
        "unit",
        "value_type",
        "precision_basis",
    ):
        _require(group[0][field] == SCOPE[field], f"full-key mismatch: {field}")
    _require(
        chart["isin"] == SCOPE["isin"]
        and chart["instrument_id"] == "11002"
        and chart["id"] == "11002",
        "chart identity mismatch",
    )
    receipt_entry = sources["chart-transport-receipt"]
    receipt = _keys(
        _json(
            _read(
                repository_root,
                Reference(receipt_entry["path"], receipt_entry["sha256"]),
            )
        ),
        set(ERSTE_MARKET_TRANSPORT_FIELDS),
        "chart transport receipt",
    )
    _require(
        type(receipt["schema_version"]) is int
        and receipt["schema_version"] == 1
        and type(receipt["byte_count"]) is int
        and receipt["byte_count"] == len(raw)
        and receipt["raw_artifact_reference"] == chart_ref.path
        and receipt["raw_artifact_sha256"] == chart_ref.sha256
        and receipt["requested_isin"] == SCOPE["isin"]
        and receipt["provider"] == "ERSTE_MARKET_APPROVED_NAV"
        and receipt["body_complete"] is True
        and receipt["transport_error"] is None
        and type(receipt["http_status"]) is int
        and receipt["http_status"] == 200
        and receipt["requested_url"]
        == receipt["final_url"]
        == "https://www.erstemarket.hu/funds/chart/11002"
        and receipt["redirect_history"] == []
        and receipt["request_role"] == "series",
        "transport receipt binding mismatch",
    )
    _require(
        _time(receipt["retrieval_timestamp"]) <= _time(SCOPE["cutoff"]),
        "acquisition bound after historical cutoff",
    )
    selected = []
    for index, pair in enumerate(chart["series"]):
        _require(
            type(pair) is list and len(pair) == 2 and type(pair[0]) is int,
            "malformed chart occurrence",
        )
        when = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=pair[0])
        if str(SCOPE["start"]) <= when.date().isoformat() <= str(SCOPE["end"]):
            _require(
                type(pair[1]) in (int, _Token), "chart value must be a JSON number"
            )
            token = str(pair[1])
            _decimal(token)
            selected.append(
                {
                    "source_locator": f"/series/{index}",
                    "raw_timestamp": pair[0],
                    "valuation_date": when.date().isoformat(),
                    "numeric_token": token,
                    "decimal_value": str(Decimal(token)),
                    "revision_status": "UNSPECIFIED",
                }
            )
    _require(
        len(selected) == 68
        and len({r["valuation_date"] for r in selected}) == 68
        and [r["source_locator"] for r in selected]
        == [f"/series/{i}" for i in range(1368, 1436)]
        and selected[0]["valuation_date"] == SCOPE["start"]
        and selected[-1]["valuation_date"] == SCOPE["end"],
        "complete 68-occurrence prefix required",
    )
    _require(
        [r["valuation_date"] for r in selected]
        == sorted(r["valuation_date"] for r in selected),
        "prefix dates must increase",
    )
    inventory = a["occurrence_inventory"]
    _require(
        len(inventory) == 68, "assessment inventory must contain all 68 occurrences"
    )
    discrepancies = []
    for row_index, (actual, reviewed) in enumerate(
        zip(selected, inventory, strict=True)
    ):
        _require(
            all(
                type(reviewed[k]) is type(v) and reviewed[k] == v
                for k, v in actual.items()
            ),
            "assessment occurrence/token mismatch",
        )
        _require(
            reviewed["source_id"] == "retained-chart"
            and reviewed["identity_group"] == group[0]["identity_group"],
            "occurrence attribution mismatch",
        )
        if "numerical_exception" in reviewed:
            e = reviewed["numerical_exception"]
            _require(
                e["comparison"] == "DIFFERS_FROM_ISSUER_REPORTED_CORRECTED_VALUE"
                and e["currency"] == "EUR"
                and e["replacement_selected"] is False,
                "unsupported discrepancy/replacement semantics",
            )
            notice = sources[e["notice_source_id"]]
            _require(
                e["notice_source_id"] == "july-2025-issuer-notice"
                and actual["valuation_date"] in e["notice_locator"]
                and "HU0000722442" in e["notice_locator"]
                and _decimal(e["issuer_reported_corrected_value"])
                != _decimal(actual["numeric_token"]),
                "discrepancy binding mismatch",
            )
            discrepancies.append(
                {
                    "source_locator": actual["source_locator"],
                    "valuation_date": actual["valuation_date"],
                    "kind": "UNADMITTED_ISSUER_VALUE_COMPARISON",
                    "notice": {"path": notice["path"], "sha256": notice["sha256"]},
                    "document_locator": e["notice_locator"],
                    "issuer_value_text": e["issuer_reported_corrected_value"],
                    "assessment_pointer": f"/occurrence_inventory/{row_index}/numerical_exception",
                }
            )
    _require(
        len(discrepancies) == 8
        and [r["valuation_date"] for r in discrepancies]
        == [f"2025-07-{day:02d}" for day in (1, 2, 3, 4, 7, 8, 9, 10)],
        "all eight July discrepancy links required",
    )
    # Same-byte in-memory snapshot: no installed SQLite connection or sidecars.
    _, conn = _snapshot(repository_root, baseline)
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        _require(
            {"nav_import_manifest", "nav_observation_version"} <= tables,
            "Phase E baseline tables missing",
        )
        manifests = conn.execute(
            "SELECT manifest_fingerprint,dataset_fingerprint,series_raw_artifact_sha256,"
            "series_retrieval_timestamp,admitted_first_date,admitted_observation_count,nav_currency,"
            "provider_instrument_id FROM nav_import_manifest WHERE exact_isin=?",
            (SCOPE["isin"],),
        ).fetchall()
        _require(len(manifests) == 1, "ambiguous Phase E baseline")
        m = manifests[0]
        _require(
            m[2] == chart_ref.sha256
            and m[3] == receipt["retrieval_timestamp"]
            and m[4:] == ("2025-08-29", 249, "EUR", "11002"),
            "Phase E baseline binding mismatch",
        )
        count = conn.execute(
            "SELECT count(*) FROM nav_observation_version WHERE exact_isin=? "
            "AND observation_date BETWEEN ? AND ?",
            (SCOPE["isin"], SCOPE["start"], SCOPE["end"]),
        ).fetchone()[0]
        _require(count == 0, "prefix already present in ordinary Phase E projection")
        retained = conn.execute(
            "SELECT count(*),min(observation_date),max(observation_date) "
            "FROM nav_observation_version WHERE exact_isin=?",
            (SCOPE["isin"],),
        ).fetchone()
        _require(
            retained == (249, "2025-08-29", "2026-08-31"),
            "Phase E observation baseline count/bounds mismatch",
        )
    finally:
        conn.close()
    payload = {
        "contract_version": 1,
        "record_type": "RESTRICTED_SUPPLEMENTARY_PLAN",
        "scope": SCOPE,
        "restrictions": RESTRICTIONS,
        "future_status": STATUS,
        "policy": p,
        "policy_reference": {"path": policy.path, "sha256": policy.sha256},
        "policy_fingerprint": canonical_fingerprint(p),
        "baseline": {
            "path": baseline.path,
            "sha256": baseline.sha256,
            "manifest_fingerprint": m[0],
            "dataset_fingerprint": m[1],
        },
        "assessment": p["assessment"],
        "assessment_reviewed_at_utc": a["reviewed_at_utc"],
        "retrieved_at_utc": receipt["retrieval_timestamp"],
        "availability_basis": "WHOLE_ARTIFACT_ACQUISITION_BOUND_NOT_ORIGINAL_PUBLICATION",
        "observations": selected,
        "occurrence_set_fingerprint": canonical_fingerprint(selected),
        "discrepancies": discrepancies,
        "dependencies": sorted(dependencies, key=lambda d: d["path"]),
        "semantic_attribution": "INHERITED_FROM_BOUND_NARRATIVE_NOT_NEW_DOCUMENTARY_REVIEW",
        "numerical_correctness": "UNESTABLISHED_ALL_68",
        "operational_eligibility": False,
    }
    return AdmissionPlan(policy, baseline, canonical_json(payload))


def plan_supplementary_admission(
    *,
    repository_root: Path,
    policy: Reference,
    baseline: Reference,
) -> AdmissionPlan:
    """Validate and preview only; malformed/missing bindings fail closed."""
    try:
        return _plan_supplementary_admission(
            repository_root=repository_root, policy=policy, baseline=baseline
        )
    except (
        KeyError,
        TypeError,
        IndexError,
        OverflowError,
        OSError,
        sqlite3.Error,
    ) as exc:
        raise SupplementaryError(f"missing/malformed provenance: {exc}") from exc


@dataclass(frozen=True)
class ExecutionAuthorization:
    """Externally trusted, explicit per-execution selection; NOT an authenticator.

    Only owner-controlled code may supply this after separately authorized
    execution. There is deliberately no file parser, default, or real selection.
    """

    plan_fingerprint: str
    policy_sha256: str
    target: Path
    expected_initial_target_sha256: str | None
    authorization_reference: str


@dataclass(frozen=True)
class RestrictedAdmission:
    record_json: str
    schema_version: int
    initial_target_sha256: str | None
    initial_target_sha256_known: bool

    @property
    def status(self) -> str:
        return STATUS

    def for_computation(self) -> NoReturn:
        raise SupplementaryError(
            "restricted evidence cannot be exported for computation"
        )


_DDL = (
    (
        "CREATE TABLE restricted_admission (id TEXT PRIMARY KEY, scope_key TEXT NOT NULL UNIQUE, "
        "record_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status='ADMITTED_EVIDENCE_ONLY_NO_COMPUTATION')) STRICT"
    ),
    (
        "CREATE TABLE restricted_observation (admission_id TEXT NOT NULL REFERENCES restricted_admission(id), "
        "locator TEXT NOT NULL, row_json TEXT NOT NULL, status TEXT NOT NULL "
        "CHECK(status='ADMITTED_EVIDENCE_ONLY_NO_COMPUTATION'), PRIMARY KEY(admission_id,locator)) STRICT"
    ),
    (
        "CREATE TABLE restricted_discrepancy (admission_id TEXT NOT NULL, locator TEXT NOT NULL, "
        "row_json TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind='UNADMITTED_ISSUER_VALUE_COMPARISON'), "
        "PRIMARY KEY(admission_id,locator), FOREIGN KEY(admission_id,locator) "
        "REFERENCES restricted_observation(admission_id,locator)) STRICT"
    ),
    *(
        f"CREATE TRIGGER {table}_{op.lower()} BEFORE {op} ON {table} BEGIN "
        "SELECT RAISE(ABORT,'restricted evidence is immutable'); END"
        for table in (
            "restricted_admission",
            "restricted_observation",
            "restricted_discrepancy",
        )
        for op in ("UPDATE", "DELETE")
    ),
)


def _schema(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
        "ORDER BY type,name"
    ).fetchall()


def _expected_schema() -> list[tuple[str, str, str]]:
    with sqlite3.connect(":memory:") as conn:
        for ddl in _DDL:
            conn.execute(ddl)
        return _schema(conn)


def _stored(
    conn: sqlite3.Connection, plan: AdmissionPlan
) -> RestrictedAdmission | None:
    p = _json(plan.payload_json.encode())
    row = conn.execute(
        "SELECT record_json,status,scope_key FROM restricted_admission WHERE id=?",
        (plan.fingerprint,),
    ).fetchone()
    if row is None:
        _require(
            conn.execute("SELECT count(*) FROM restricted_admission").fetchone()[0]
            == 0,
            "scope/plan collision: restricted store already contains another admission",
        )
        _require(
            all(
                conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0
                for t in ("restricted_observation", "restricted_discrepancy")
            ),
            "orphan/partial admission",
        )
        return None
    raw_record = _json(row[0].encode())
    _require(type(raw_record) is dict, "admission record: unsupported fields")
    schema_version = raw_record.get("schema_version")
    common_fields = {
        "record_type",
        "schema_version",
        "plan_fingerprint",
        "plan",
        "recorded_at_utc",
        "authorization_reference",
        "status",
        "restrictions",
    }
    if (
        type(schema_version) is int
        and schema_version == LEGACY_ADMISSION_SCHEMA_VERSION
    ):
        record = _keys(raw_record, common_fields, "admission record")
        initial_target_sha256 = None
        initial_target_sha256_known = False
    elif (
        type(schema_version) is int
        and schema_version == CURRENT_ADMISSION_SCHEMA_VERSION
    ):
        record = _keys(
            raw_record,
            common_fields | {"initial_target_sha256"},
            "admission record",
        )
        initial_target_sha256 = record["initial_target_sha256"]
        _require(
            initial_target_sha256 is None
            or (
                type(initial_target_sha256) is str
                and _HASH.fullmatch(initial_target_sha256) is not None
            ),
            "stored initial target hash invalid",
        )
        initial_target_sha256_known = True
    else:
        raise SupplementaryError("unsupported admission record schema version")
    _require(
        type(record["schema_version"]) is int
        and record["schema_version"] == schema_version
        and record["record_type"] == "RESTRICTED_SUPPLEMENTARY_ADMISSION"
        and canonical_json(record["plan"]) == plan.payload_json
        and record["plan_fingerprint"] == plan.fingerprint
        and row[1] == record["status"] == STATUS
        and row[2] == canonical_fingerprint(SCOPE)
        and record["restrictions"] == list(RESTRICTIONS),
        "stored admission mismatch",
    )
    _time(record["recorded_at_utc"])
    _require(
        type(record["authorization_reference"]) is str
        and bool(record["authorization_reference"]),
        "stored authorization attribution missing",
    )
    _require(
        conn.execute("SELECT count(*) FROM restricted_admission").fetchone()[0] == 1,
        "ambiguous store scope",
    )
    for table, key, fixed in (
        ("restricted_observation", "observations", STATUS),
        (
            "restricted_discrepancy",
            "discrepancies",
            "UNADMITTED_ISSUER_VALUE_COMPARISON",
        ),
    ):
        column = "status" if key == "observations" else "kind"
        actual = conn.execute(
            f"SELECT admission_id,locator,row_json,{column} FROM {table} ORDER BY locator"
        ).fetchall()
        expected = sorted(
            (plan.fingerprint, r["source_locator"], canonical_json(r), fixed)
            for r in p[key]
        )
        _require(actual == expected, f"stored {key} partial/altered")
    _require(
        not conn.execute("PRAGMA foreign_key_check").fetchall(),
        "stored foreign-key mismatch",
    )
    return RestrictedAdmission(
        row[0],
        schema_version,
        initial_target_sha256,
        initial_target_sha256_known,
    )


def execute_supplementary_admission(
    *,
    repository_root: Path,
    plan: AdmissionPlan,
    target: Path,
    authorization: ExecutionAuthorization | None,
) -> RestrictedAdmission:
    """Explicit opt-in writer for a SEPARATE restricted store, never Phase E.

    Replays plan/dependencies before opening the target. Transactional DDL + all
    rows; retry returns the original record/time after exact content verification.
    No clock in the plan; actual execution time is recorded only here.
    """
    _require(type(plan) is AdmissionPlan, "validated plan required")
    _require(
        type(authorization) is ExecutionAuthorization,
        "trusted execution authorization required",
    )
    assert authorization is not None
    _safe_absolute(target)
    _require(
        authorization.target == target
        and authorization.plan_fingerprint == plan.fingerprint
        and authorization.policy_sha256 == plan.policy.sha256
        and type(authorization.authorization_reference) is str
        and bool(authorization.authorization_reference),
        "execution authorization binding mismatch",
    )
    fresh = plan_supplementary_admission(
        repository_root=repository_root, policy=plan.policy, baseline=plan.baseline
    )
    _require(fresh == plan, "stale/altered plan")
    _require(
        target != (repository_root.absolute() / plan.baseline.path),
        "baseline cannot be a write target",
    )
    _require(
        not any(Path(str(target) + s).exists() for s in ("-wal", "-shm", "-journal")),
        "target has live sidecar",
    )
    target_existed = target.exists()
    before = hashlib.sha256(target.read_bytes()).hexdigest() if target_existed else None
    _require(
        target_existed or authorization.expected_initial_target_sha256 is None,
        "target baseline mismatch",
    )
    # Reject operational schemas before any DDL; no generic application connect/migrate.
    conn = sqlite3.connect(
        target.as_uri() + "?mode=rwc", uri=True, isolation_level=None
    )
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        schema = _schema(conn)
        if schema:
            _require(
                schema == _expected_schema(),
                "target is not an isolated restricted store",
            )
            existing = _stored(conn, fresh)
            if existing is not None:
                _require(
                    existing.initial_target_sha256_known,
                    "legacy admission initial target hash is unknown; retry is not authorized",
                )
                existing_record = _json(existing.record_json.encode())
                _require(
                    authorization.authorization_reference
                    == existing_record["authorization_reference"],
                    "execution authorization binding mismatch",
                )
                _require(
                    authorization.expected_initial_target_sha256
                    == existing.initial_target_sha256,
                    "target baseline mismatch",
                )
                conn.rollback()
                return existing
        _require(
            before == authorization.expected_initial_target_sha256,
            "target baseline mismatch",
        )
        if not schema:
            for ddl in _DDL:
                conn.execute(ddl)
        p = _json(fresh.payload_json.encode())
        record = {
            "schema_version": CURRENT_ADMISSION_SCHEMA_VERSION,
            "record_type": "RESTRICTED_SUPPLEMENTARY_ADMISSION",
            "plan_fingerprint": fresh.fingerprint,
            "plan": p,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "authorization_reference": authorization.authorization_reference,
            "initial_target_sha256": before,
            "status": STATUS,
            "restrictions": RESTRICTIONS,
        }
        conn.execute(
            "INSERT INTO restricted_admission VALUES (?,?,?,?)",
            (
                fresh.fingerprint,
                canonical_fingerprint(SCOPE),
                canonical_json(record),
                STATUS,
            ),
        )
        for row in p["observations"]:
            conn.execute(
                "INSERT INTO restricted_observation VALUES (?,?,?,?)",
                (fresh.fingerprint, row["source_locator"], canonical_json(row), STATUS),
            )
        for row in p["discrepancies"]:
            conn.execute(
                "INSERT INTO restricted_discrepancy VALUES (?,?,?,?)",
                (
                    fresh.fingerprint,
                    row["source_locator"],
                    canonical_json(row),
                    row["kind"],
                ),
            )
        result = _stored(conn, fresh)
        assert result is not None
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def inspect_restricted_admission(
    *, repository_root: Path, plan: AdmissionPlan, store: Reference
) -> RestrictedAdmission:
    """Read-only same-byte inspection; no permissive/default status or promotion."""
    _require(
        plan
        == plan_supplementary_admission(
            repository_root=repository_root, policy=plan.policy, baseline=plan.baseline
        ),
        "stale/altered plan",
    )
    _, conn = _snapshot(repository_root, store)
    try:
        _require(_schema(conn) == _expected_schema(), "unsupported restricted schema")
        result = _stored(conn, plan)
        _require(result is not None, "admission absent")
        assert result is not None
        return result
    finally:
        conn.close()
