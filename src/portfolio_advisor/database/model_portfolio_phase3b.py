"""Synthetic-only Phase 3B.1 dual-sheet workbook writer.

The operational model-portfolio database and watcher remain outside this
module.  The public write entry points accept only ordinary files below the
system temporary directory and operate on the synthetic JSON workbook format
defined here.  A later, separately authorized phase must supply the retained
Excel parser, evidence retention, writer coordination, and outbox worker.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from portfolio_advisor.audit.milestone_4 import is_valid_isin
from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.database.model_portfolio_phase1 import (
    RAW_ONLY_FIELDS,
    normalize_parsed_fields,
    source_dataset_fingerprint,
)
from portfolio_advisor.database.schema.v3 import (
    connect,
    initialize_schema,
    insert_instrument,
    transaction,
    validate_schema,
)

FEATURE_ID = "MODEL_PORTFOLIO_CONSOLIDATION_PHASE3B1"
FEATURE_REVISION = 1
CONTRACT_VERSION = 1
WORKBOOK_FORMAT = "SYNTHETIC_DUAL_SHEET_WORKBOOK_V1"
NON_OPERATIONAL_STATUS = "PHASE3B1_SYNTHETIC_TEMPORARY"

MODEL_ROLE = "MODEL"
SHORTLIST_ROLE = "SHORTLIST"
MODEL_SHEET_NAME = "modell portfóliók"
SHORTLIST_SHEET_NAME = "terméklista"

MODEL_HEADERS = (
    "Portfolio Name",
    "Product",
    "ISIN",
    "Allocation (%)",
    "Asset Class",
    "Sub-Asset Class",
    "Currency",
    "Currency Risk",
    "Sustainability",
    "YTD",
    "1 Year",
    "3 Years",
    "5 Years",
    "1Y Sharpe Ratio",
    "3Y Sharpe Ratio",
    "5Y Sharpe Ratio",
    "1Y Volatility",
    "3Y Volatility",
    "Downside Risk",
    "Information Ratio",
    "Maximum Drawdown",
)
SHORTLIST_HEADERS = (
    "Product",
    "ISIN",
    "Asset Class",
    "Sub-Asset Class",
    "Product Type",
    "Currency",
    "Currency Risk",
    "Sustainability",
    "YTD",
    "1yr",
    "3yr",
    "5yr",
    "1Y Sharpe",
    "3Y Sharpe",
    "5Y Sharpe",
    "1Y Vol.",
    "3Y Vol.",
    "Down. risk",
    "Info. ratio",
    "Max. drawd.",
)

_MODEL_METRICS = (
    ("RETURN_1Y", "Return 1 year", "1 Year"),
    ("SHARPE_RATIO_1Y", "Sharpe ratio 1 year", "1Y Sharpe Ratio"),
    ("VOLATILITY_1Y", "Volatility 1 year", "1Y Volatility"),
    ("DOWNSIDE_RISK", "Downside risk", "Downside Risk"),
    ("MAXIMUM_DRAWDOWN", "Maximum drawdown", "Maximum Drawdown"),
    ("YTD", "YTD", "YTD"),
    ("RETURN_3Y", "Return 3 years", "3 Years"),
    ("RETURN_5Y", "Return 5 years", "5 Years"),
    ("SHARPE_RATIO_3Y", "Sharpe ratio 3 years", "3Y Sharpe Ratio"),
    ("SHARPE_RATIO_5Y", "Sharpe ratio 5 years", "5Y Sharpe Ratio"),
    ("VOLATILITY_3Y", "Volatility 3 years", "3Y Volatility"),
    ("INFORMATION_RATIO", "Information ratio", "Information Ratio"),
)
_SHORTLIST_METRICS = (
    ("YTD", "YTD", "YTD"),
    ("RETURN_1Y", "Return 1 year", "1yr"),
    ("RETURN_3Y", "Return 3 years", "3yr"),
    ("RETURN_5Y", "Return 5 years", "5yr"),
    ("SHARPE_RATIO_1Y", "Sharpe ratio 1 year", "1Y Sharpe"),
    ("SHARPE_RATIO_3Y", "Sharpe ratio 3 years", "3Y Sharpe"),
    ("SHARPE_RATIO_5Y", "Sharpe ratio 5 years", "5Y Sharpe"),
    ("VOLATILITY_1Y", "Volatility 1 year", "1Y Vol."),
    ("VOLATILITY_3Y", "Volatility 3 years", "3Y Vol."),
    ("DOWNSIDE_RISK", "Downside risk", "Down. risk"),
    ("INFORMATION_RATIO", "Information ratio", "Info. ratio"),
    ("MAXIMUM_DRAWDOWN", "Maximum drawdown", "Max. drawd."),
)
_OUTBOX_WORK = ("FILE_FINALIZATION", "ARTIFACT_REFRESH")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ModelPortfolioPhase3BError(RuntimeError):
    """A temporary writer contract, admission, or replay failed closed."""


@dataclass(frozen=True, slots=True)
class SyntheticWorkbookAdmissionRequest:
    """Authorization and expected state for one synthetic workbook envelope."""

    admission_id: str
    authority_id: str
    authorization_reference: str
    workbook_path: Path
    retention_root: Path
    expected_workbook_sha256: str
    expected_predecessor_admission_id: str | None
    expected_model_fingerprint_before: str
    expected_shortlist_fingerprint_before: str


@dataclass(frozen=True, slots=True)
class SyntheticWorkbookAdmissionResult:
    admission_id: str
    snapshot_date: str
    model_item_count: int
    shortlist_item_count: int
    outbox_item_count: int
    model_fingerprint_after: str
    shortlist_fingerprint_after: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class _Sheet:
    role: str
    name: str
    headers: tuple[str, ...]
    rows: tuple[dict[str, object], ...]

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(
            {
                "headers": self.headers,
                "name": self.name,
                "role": self.role,
                "rows": self.rows,
            }
        )


@dataclass(frozen=True, slots=True)
class _Workbook:
    snapshot_date: str
    sheets: tuple[_Sheet, ...]

    def sheet(self, role: str) -> _Sheet:
        return next(sheet for sheet in self.sheets if sheet.role == role)


_SCHEMA_SQL = """
CREATE TABLE model_workbook_writer_authority (
    authority_id TEXT PRIMARY KEY CHECK(length(trim(authority_id)) > 0),
    contract_version INTEGER NOT NULL CHECK(contract_version = 1),
    operational_status TEXT NOT NULL CHECK(operational_status = 'PHASE3B1_SYNTHETIC_TEMPORARY'),
    authorization_reference TEXT NOT NULL CHECK(length(trim(authorization_reference)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE model_workbook_writer_admission (
    admission_id TEXT PRIMARY KEY CHECK(length(trim(admission_id)) > 0),
    authority_id TEXT NOT NULL REFERENCES model_workbook_writer_authority(authority_id),
    contract_version INTEGER NOT NULL CHECK(contract_version = 1),
    admission_sequence INTEGER NOT NULL UNIQUE CHECK(admission_sequence > 0),
    predecessor_admission_id TEXT NULL REFERENCES model_workbook_writer_admission(admission_id),
    workbook_sha256 TEXT NOT NULL CHECK(length(workbook_sha256) = 64),
    filename TEXT NOT NULL CHECK(length(trim(filename)) > 0),
    retained_relative_path TEXT NOT NULL CHECK(length(trim(retained_relative_path)) > 0),
    snapshot_date TEXT NOT NULL UNIQUE CHECK(length(trim(snapshot_date)) > 0),
    expected_model_fingerprint_before TEXT NOT NULL CHECK(length(expected_model_fingerprint_before) = 64),
    expected_shortlist_fingerprint_before TEXT NOT NULL CHECK(length(expected_shortlist_fingerprint_before) = 64),
    model_fingerprint_after TEXT NOT NULL CHECK(length(model_fingerprint_after) = 64),
    shortlist_fingerprint_after TEXT NOT NULL CHECK(length(shortlist_fingerprint_after) = 64),
    workbook_binding_fingerprint TEXT NOT NULL CHECK(length(workbook_binding_fingerprint) = 64),
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64),
    model_item_count INTEGER NOT NULL CHECK(model_item_count > 0),
    shortlist_item_count INTEGER NOT NULL CHECK(shortlist_item_count > 0),
    admission_status TEXT NOT NULL CHECK(admission_status = 'PHASE3B1_SYNTHETIC_VALIDATED'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK((admission_sequence = 1 AND predecessor_admission_id IS NULL)
       OR (admission_sequence > 1 AND predecessor_admission_id IS NOT NULL))
);
CREATE TABLE model_workbook_writer_sheet (
    admission_id TEXT NOT NULL REFERENCES model_workbook_writer_admission(admission_id),
    sheet_role TEXT NOT NULL CHECK(sheet_role IN ('MODEL','SHORTLIST')),
    sheet_name TEXT NOT NULL CHECK(length(trim(sheet_name)) > 0),
    header_signature TEXT NOT NULL CHECK(length(header_signature) = 64),
    sheet_fingerprint TEXT NOT NULL CHECK(length(sheet_fingerprint) = 64),
    item_count INTEGER NOT NULL CHECK(item_count > 0),
    disposition TEXT NOT NULL CHECK(disposition = 'ADMITTED_ATOMICALLY'),
    PRIMARY KEY(admission_id, sheet_role),
    UNIQUE(admission_id, sheet_name)
);
CREATE TABLE model_workbook_writer_model_item (
    admission_id TEXT NOT NULL REFERENCES model_workbook_writer_admission(admission_id),
    stable_source_reference TEXT NOT NULL UNIQUE CHECK(length(stable_source_reference) = 64),
    source_occurrence_id INTEGER NOT NULL UNIQUE REFERENCES portfolio_holding_source_occurrence(portfolio_holding_source_occurrence_id),
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    source_payload_sha256 TEXT NOT NULL CHECK(length(source_payload_sha256) = 64),
    parsed_payload_json TEXT NOT NULL,
    parsed_payload_sha256 TEXT NOT NULL CHECK(length(parsed_payload_sha256) = 64),
    sustainability TEXT NULL,
    parsed_fields_fingerprint TEXT NOT NULL CHECK(length(parsed_fields_fingerprint) = 64),
    PRIMARY KEY(admission_id, stable_source_reference)
);
CREATE TABLE model_workbook_writer_shortlist_item (
    admission_id TEXT NOT NULL REFERENCES model_workbook_writer_admission(admission_id),
    stable_source_reference TEXT NOT NULL UNIQUE CHECK(length(stable_source_reference) = 64),
    source_occurrence_id INTEGER NOT NULL UNIQUE REFERENCES shortlist_entry_source_occurrence(shortlist_entry_source_occurrence_id),
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    source_payload_sha256 TEXT NOT NULL CHECK(length(source_payload_sha256) = 64),
    parsed_payload_json TEXT NOT NULL,
    parsed_payload_sha256 TEXT NOT NULL CHECK(length(parsed_payload_sha256) = 64),
    PRIMARY KEY(admission_id, stable_source_reference)
);
CREATE TABLE model_workbook_writer_outbox (
    outbox_id TEXT PRIMARY KEY CHECK(length(outbox_id) = 64),
    admission_id TEXT NOT NULL REFERENCES model_workbook_writer_admission(admission_id),
    work_type TEXT NOT NULL CHECK(work_type IN ('FILE_FINALIZATION','ARTIFACT_REFRESH')),
    state TEXT NOT NULL CHECK(state = 'PENDING'),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(admission_id, work_type)
);
CREATE TRIGGER model_workbook_writer_authority_immutable_update BEFORE UPDATE ON model_workbook_writer_authority BEGIN SELECT RAISE(ABORT, 'writer authorities are immutable'); END;
CREATE TRIGGER model_workbook_writer_authority_immutable_delete BEFORE DELETE ON model_workbook_writer_authority BEGIN SELECT RAISE(ABORT, 'writer authorities are immutable'); END;
CREATE TRIGGER model_workbook_writer_admission_immutable_update BEFORE UPDATE ON model_workbook_writer_admission BEGIN SELECT RAISE(ABORT, 'writer admissions are immutable'); END;
CREATE TRIGGER model_workbook_writer_admission_immutable_delete BEFORE DELETE ON model_workbook_writer_admission BEGIN SELECT RAISE(ABORT, 'writer admissions are immutable'); END;
CREATE TRIGGER model_workbook_writer_sheet_immutable_update BEFORE UPDATE ON model_workbook_writer_sheet BEGIN SELECT RAISE(ABORT, 'writer sheets are immutable'); END;
CREATE TRIGGER model_workbook_writer_sheet_immutable_delete BEFORE DELETE ON model_workbook_writer_sheet BEGIN SELECT RAISE(ABORT, 'writer sheets are immutable'); END;
CREATE TRIGGER model_workbook_writer_model_item_immutable_update BEFORE UPDATE ON model_workbook_writer_model_item BEGIN SELECT RAISE(ABORT, 'writer model items are immutable'); END;
CREATE TRIGGER model_workbook_writer_model_item_immutable_delete BEFORE DELETE ON model_workbook_writer_model_item BEGIN SELECT RAISE(ABORT, 'writer model items are immutable'); END;
CREATE TRIGGER model_workbook_writer_shortlist_item_immutable_update BEFORE UPDATE ON model_workbook_writer_shortlist_item BEGIN SELECT RAISE(ABORT, 'writer shortlist items are immutable'); END;
CREATE TRIGGER model_workbook_writer_shortlist_item_immutable_delete BEFORE DELETE ON model_workbook_writer_shortlist_item BEGIN SELECT RAISE(ABORT, 'writer shortlist items are immutable'); END;
CREATE TRIGGER model_workbook_writer_outbox_immutable_update BEFORE UPDATE ON model_workbook_writer_outbox BEGIN SELECT RAISE(ABORT, 'writer outbox is immutable in Phase 3B.1'); END;
CREATE TRIGGER model_workbook_writer_outbox_immutable_delete BEFORE DELETE ON model_workbook_writer_outbox BEGIN SELECT RAISE(ABORT, 'writer outbox is immutable in Phase 3B.1'); END;
"""

_OBJECT_NAMES = frozenset(
    {
        "model_workbook_writer_authority",
        "model_workbook_writer_admission",
        "model_workbook_writer_sheet",
        "model_workbook_writer_model_item",
        "model_workbook_writer_shortlist_item",
        "model_workbook_writer_outbox",
        "model_workbook_writer_authority_immutable_update",
        "model_workbook_writer_authority_immutable_delete",
        "model_workbook_writer_admission_immutable_update",
        "model_workbook_writer_admission_immutable_delete",
        "model_workbook_writer_sheet_immutable_update",
        "model_workbook_writer_sheet_immutable_delete",
        "model_workbook_writer_model_item_immutable_update",
        "model_workbook_writer_model_item_immutable_delete",
        "model_workbook_writer_shortlist_item_immutable_update",
        "model_workbook_writer_shortlist_item_immutable_delete",
        "model_workbook_writer_outbox_immutable_update",
        "model_workbook_writer_outbox_immutable_delete",
    }
)
FEATURE_FINGERPRINT = canonical_fingerprint(
    {
        "contract_version": CONTRACT_VERSION,
        "ddl": " ".join(_SCHEMA_SQL.split()),
        "feature_id": FEATURE_ID,
        "objects": tuple(sorted(_OBJECT_NAMES)),
        "status": NON_OPERATIONAL_STATUS,
        "workbook_format": WORKBOOK_FORMAT,
    }
)


def initialize_synthetic_writer_database(path: Path) -> None:
    """Create a new schema-v3/Phase-3B.1 database below the temp directory."""
    _validate_new_temporary_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    try:
        initialize_schema(connection)
        install_phase3b1_schema(connection)
    except BaseException:
        connection.close()
        if path.exists():
            path.unlink()
        raise
    connection.close()


def install_phase3b1_schema(connection: sqlite3.Connection) -> None:
    """Install the additive synthetic writer schema on a recognized v3 store."""
    database_row = next(
        (
            row
            for row in connection.execute("PRAGMA database_list")
            if str(row[1]) == "main"
        ),
        None,
    )
    if database_row is None or not str(database_row[2]):
        raise ModelPortfolioPhase3BError(
            "Phase 3B.1 schema installation requires a temporary file database"
        )
    _validate_existing_temporary_file(Path(str(database_row[2])), "database")
    validate_schema(connection)
    names = _schema_object_names(connection)
    present = names & _OBJECT_NAMES
    marker = _feature_marker(connection)
    if present and present != _OBJECT_NAMES:
        raise ModelPortfolioPhase3BError("Phase 3B.1 schema is partially installed")
    if not present and marker:
        raise ModelPortfolioPhase3BError("Phase 3B.1 marker exists without schema")
    if not present:
        with transaction(connection):
            _execute_schema(connection)
            connection.execute(
                "INSERT INTO schema_feature_contract(feature_id,revision,contract_fingerprint) VALUES(?,?,?)",
                (FEATURE_ID, FEATURE_REVISION, FEATURE_FINGERPRINT),
            )
    validate_phase3b1_schema(connection)


def admit_synthetic_workbook(
    database_path: Path,
    request: SyntheticWorkbookAdmissionRequest,
    *,
    _failure_hook: Callable[[str, sqlite3.Connection], None] | None = None,
    _after_commit_hook: Callable[[], None] | None = None,
) -> SyntheticWorkbookAdmissionResult:
    """Admit both synthetic sheets and pending work in one outer transaction."""
    database = _validate_existing_temporary_file(database_path, "database")
    workbook_path = _validate_existing_temporary_file(request.workbook_path, "workbook")
    workbook_sha256 = _file_sha256(workbook_path)
    if workbook_sha256 != request.expected_workbook_sha256:
        raise ModelPortfolioPhase3BError("synthetic workbook SHA-256 changed")
    _validate_request(request)
    retained_path = _retain_workbook(
        request.retention_root, workbook_path, workbook_sha256
    )
    workbook = _load_workbook(retained_path)
    request_fingerprint = _request_fingerprint(request, workbook)

    connection = sqlite3.connect(database, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        if connection.in_transaction:
            raise ModelPortfolioPhase3BError("writer must own the transaction boundary")
        connection.execute("BEGIN IMMEDIATE")
        validate_phase3b1_contracts(connection)
        existing = connection.execute(
            "SELECT * FROM model_workbook_writer_admission WHERE admission_id=?",
            (request.admission_id,),
        ).fetchone()
        if existing is not None:
            result = _validate_replay(
                connection,
                request,
                workbook,
                workbook_sha256,
                request_fingerprint,
                existing,
            )
            connection.rollback()
            return result
        _validate_new_admission_state(connection, request, workbook.snapshot_date)
        if _installed_dataset_bound_state(connection):
            raise ModelPortfolioPhase3BError(
                "new evidence cannot inherit installed dataset-bound contracts"
            )
        before_model = source_dataset_fingerprint(connection)
        before_shortlist = shortlist_dataset_fingerprint(connection)
        if before_model != request.expected_model_fingerprint_before:
            raise ModelPortfolioPhase3BError("expected model dataset binding is stale")
        if before_shortlist != request.expected_shortlist_fingerprint_before:
            raise ModelPortfolioPhase3BError(
                "expected shortlist dataset binding is stale"
            )
        _call_hook(_failure_hook, "before_candidate_write", connection)
        authority = connection.execute(
            "SELECT * FROM model_workbook_writer_authority WHERE authority_id=?",
            (request.authority_id,),
        ).fetchone()
        if authority is None:
            connection.execute(
                "INSERT INTO model_workbook_writer_authority(authority_id,contract_version,operational_status,authorization_reference) VALUES(?,?,?,?)",
                (
                    request.authority_id,
                    CONTRACT_VERSION,
                    NON_OPERATIONAL_STATUS,
                    request.authorization_reference,
                ),
            )
        elif (
            int(authority["contract_version"]) != CONTRACT_VERSION
            or str(authority["operational_status"]) != NON_OPERATIONAL_STATUS
            or str(authority["authorization_reference"])
            != request.authorization_reference
        ):
            raise ModelPortfolioPhase3BError("writer authority binding conflicts")

        source_file_id, source_sheet_ids = _insert_source_envelope(
            connection, request, workbook, workbook_sha256
        )
        admission_sequence = int(
            connection.execute(
                "SELECT coalesce(max(admission_sequence),0)+1 FROM model_workbook_writer_admission"
            ).fetchone()[0]
        )
        # The receipt is inserted after source rows because it binds their after-state.
        model_items = _insert_model_rows(
            connection,
            request.admission_id,
            workbook,
            source_file_id,
            source_sheet_ids[MODEL_ROLE],
            workbook_sha256,
        )
        _call_hook(_failure_hook, "after_model_sheet", connection)
        shortlist_items = _insert_shortlist_rows(
            connection,
            request.admission_id,
            workbook,
            source_file_id,
            source_sheet_ids[SHORTLIST_ROLE],
            workbook_sha256,
        )
        _call_hook(_failure_hook, "after_shortlist_sheet", connection)
        after_model = source_dataset_fingerprint(connection)
        after_shortlist = shortlist_dataset_fingerprint(connection)
        binding = _workbook_binding_fingerprint(workbook, workbook_sha256)
        connection.execute(
            """INSERT INTO model_workbook_writer_admission(
                   admission_id,authority_id,contract_version,admission_sequence,
                   predecessor_admission_id,workbook_sha256,filename,retained_relative_path,snapshot_date,
                   expected_model_fingerprint_before,expected_shortlist_fingerprint_before,
                   model_fingerprint_after,shortlist_fingerprint_after,
                   workbook_binding_fingerprint,request_fingerprint,model_item_count,
                   shortlist_item_count,admission_status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PHASE3B1_SYNTHETIC_VALIDATED')""",
            (
                request.admission_id,
                request.authority_id,
                CONTRACT_VERSION,
                admission_sequence,
                request.expected_predecessor_admission_id,
                workbook_sha256,
                workbook_path.name,
                _retained_relative_path(workbook_sha256),
                workbook.snapshot_date,
                before_model,
                before_shortlist,
                after_model,
                after_shortlist,
                binding,
                request_fingerprint,
                len(model_items),
                len(shortlist_items),
            ),
        )
        for sheet in workbook.sheets:
            connection.execute(
                "INSERT INTO model_workbook_writer_sheet VALUES(?,?,?,?,?,?,'ADMITTED_ATOMICALLY')",
                (
                    request.admission_id,
                    sheet.role,
                    sheet.name,
                    canonical_fingerprint(sheet.headers),
                    sheet.fingerprint,
                    len(sheet.rows),
                ),
            )
        _insert_writer_items(
            connection, request.admission_id, model_items, shortlist_items
        )
        _insert_outbox(
            connection, request.admission_id, binding, after_model, after_shortlist
        )
        _call_hook(_failure_hook, "before_final_validation", connection)
        validate_phase3b1_contracts(connection)
        _call_hook(_failure_hook, "after_final_validation", connection)
        connection.commit()
        result = SyntheticWorkbookAdmissionResult(
            request.admission_id,
            workbook.snapshot_date,
            len(model_items),
            len(shortlist_items),
            len(_OUTBOX_WORK),
            after_model,
            after_shortlist,
            False,
        )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
    if _after_commit_hook is not None:
        _after_commit_hook()
    return result


def validate_phase3b1_contracts(connection: sqlite3.Connection) -> None:
    """Validate the installed writer contract, receipts, rows, and corrections."""
    validate_phase3b1_schema(connection)
    from portfolio_advisor.database.migrations.shortlist_classification import (
        validate_classification_corrections_if_present,
    )
    from portfolio_advisor.database.migrations.shortlist_zero_null import (
        validate_corrections_if_present,
    )
    from portfolio_advisor.database.model_portfolio_phase1 import (
        FEATURE_ID as PHASE1_FEATURE_ID,
    )
    from portfolio_advisor.database.model_portfolio_phase1 import (
        validate_phase1_contracts,
    )

    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        validate_corrections_if_present(connection)
        validate_classification_corrections_if_present(connection)
        phase1 = connection.execute(
            "SELECT 1 FROM schema_feature_contract WHERE feature_id=?",
            (PHASE1_FEATURE_ID,),
        ).fetchone()
        if phase1 is not None:
            validate_phase1_contracts(connection)
        _validate_phase3b1_records(connection)
    finally:
        connection.row_factory = previous_factory


def validate_phase3b1_schema(connection: sqlite3.Connection) -> None:
    validate_schema(connection)
    if _schema_object_names(connection) & _OBJECT_NAMES != _OBJECT_NAMES:
        raise ModelPortfolioPhase3BError("Phase 3B.1 schema is absent or partial")
    if _feature_marker(connection) != [(FEATURE_REVISION, FEATURE_FINGERPRINT)]:
        raise ModelPortfolioPhase3BError("Phase 3B.1 feature marker is stale")
    if _schema_objects(connection) != _expected_schema_objects():
        raise ModelPortfolioPhase3BError("Phase 3B.1 schema objects are damaged")


def shortlist_dataset_fingerprint(connection: sqlite3.Connection) -> str:
    """Fingerprint immutable shortlist evidence by portable source identity."""
    rows = connection.execute(
        """SELECT sf.sha256, sh.sheet_name, o.source_row_number,
                  snapshot.snapshot_date, instrument.isin,
                  o.observed_product_name, o.observed_currency_code,
                  o.observed_asset_class, o.observed_sub_asset_class,
                  o.source_payload_json, o.conflict_status
           FROM shortlist_entry_source_occurrence AS o
           JOIN shortlist_snapshot AS snapshot
             ON snapshot.shortlist_snapshot_id=o.shortlist_snapshot_id
           JOIN instrument ON instrument.instrument_id=o.instrument_id
           JOIN source_sheet AS sh ON sh.source_sheet_id=o.source_sheet_id
           JOIN source_file AS sf ON sf.source_file_id=sh.source_file_id
           ORDER BY sf.sha256, sh.sheet_name, o.source_row_number,
                    snapshot.snapshot_date, instrument.isin,
                    o.observed_product_name, o.observed_currency_code,
                    o.observed_asset_class, o.observed_sub_asset_class,
                    o.source_payload_json, o.conflict_status"""
    ).fetchall()
    return canonical_fingerprint([tuple(row) for row in rows])


def _load_workbook(path: Path) -> _Workbook:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelPortfolioPhase3BError(
            "synthetic workbook is not valid UTF-8 JSON"
        ) from error
    if not isinstance(payload, dict) or set(payload) != {
        "format",
        "snapshot_date",
        "sheets",
    }:
        raise ModelPortfolioPhase3BError("synthetic workbook envelope fields differ")
    if payload["format"] != WORKBOOK_FORMAT:
        raise ModelPortfolioPhase3BError("unsupported synthetic workbook format")
    snapshot_date = _iso_date(payload["snapshot_date"], "snapshot_date")
    raw_sheets = payload["sheets"]
    if not isinstance(raw_sheets, list) or len(raw_sheets) != 2:
        raise ModelPortfolioPhase3BError(
            "workbook must contain exactly two visible sheets"
        )
    sheets: list[_Sheet] = []
    for raw in raw_sheets:
        if not isinstance(raw, dict) or set(raw) != {"role", "name", "headers", "rows"}:
            raise ModelPortfolioPhase3BError("synthetic sheet fields differ")
        role = _text(raw["role"], "sheet role")
        name = _text(raw["name"], "sheet name")
        headers_value = raw["headers"]
        rows_value = raw["rows"]
        if not isinstance(headers_value, list) or not all(
            isinstance(x, str) for x in headers_value
        ):
            raise ModelPortfolioPhase3BError("sheet headers must be text")
        headers = tuple(headers_value)
        expected_headers = (
            MODEL_HEADERS
            if role == MODEL_ROLE
            else SHORTLIST_HEADERS
            if role == SHORTLIST_ROLE
            else ()
        )
        expected_name = (
            MODEL_SHEET_NAME
            if role == MODEL_ROLE
            else SHORTLIST_SHEET_NAME
            if role == SHORTLIST_ROLE
            else ""
        )
        if headers != expected_headers or name != expected_name:
            raise ModelPortfolioPhase3BError(
                "sheet role, name, or headers are unsupported"
            )
        if not isinstance(rows_value, list) or not rows_value:
            raise ModelPortfolioPhase3BError("each workbook sheet must contain rows")
        rows: list[dict[str, object]] = []
        for raw_row in rows_value:
            if not isinstance(raw_row, dict) or set(raw_row) != {
                "source_row",
                "raw_values",
                "values",
            }:
                raise ModelPortfolioPhase3BError("synthetic row fields differ")
            if not isinstance(raw_row["source_row"], int) or raw_row["source_row"] <= 0:
                raise ModelPortfolioPhase3BError(
                    "source_row must be a positive integer"
                )
            raw_values = raw_row["raw_values"]
            values = raw_row["values"]
            if (
                not isinstance(raw_values, dict)
                or tuple(raw_values) != headers
                or not isinstance(values, dict)
                or tuple(values) != headers
            ):
                raise ModelPortfolioPhase3BError(
                    "raw and parsed row values must follow the exact header order"
                )
            rows.append(
                {
                    "source_row": raw_row["source_row"],
                    "raw_values": raw_values,
                    "values": values,
                }
            )
        if len({row["source_row"] for row in rows}) != len(rows):
            raise ModelPortfolioPhase3BError(
                "source row identities are duplicated within a sheet"
            )
        sheets.append(_Sheet(role, name, headers, tuple(rows)))
    if {sheet.role for sheet in sheets} != {MODEL_ROLE, SHORTLIST_ROLE}:
        raise ModelPortfolioPhase3BError(
            "workbook sheet roles are missing or duplicated"
        )
    return _Workbook(snapshot_date, tuple(sheets))


def _insert_source_envelope(
    connection: sqlite3.Connection,
    request: SyntheticWorkbookAdmissionRequest,
    workbook: _Workbook,
    workbook_sha256: str,
) -> tuple[int, dict[str, int]]:
    cursor = connection.execute(
        "INSERT INTO source_file(filename,sha256,source_type,source_date) VALUES(?,?,'SYNTHETIC_DUAL_SHEET_WORKBOOK',?)",
        (request.workbook_path.name, workbook_sha256, workbook.snapshot_date),
    )
    source_file_id = _last_id(cursor)
    sheet_ids: dict[str, int] = {}
    for sheet in workbook.sheets:
        cursor = connection.execute(
            "INSERT INTO source_sheet(source_file_id,sheet_name) VALUES(?,?)",
            (source_file_id, sheet.name),
        )
        sheet_ids[sheet.role] = _last_id(cursor)
    return source_file_id, sheet_ids


def _insert_model_rows(
    connection: sqlite3.Connection,
    admission_id: str,
    workbook: _Workbook,
    source_file_id: int,
    source_sheet_id: int,
    workbook_sha256: str,
) -> list[tuple[str, int, int, str, str, str, str | None, str]]:
    sheet = workbook.sheet(MODEL_ROLE)
    parsed_rows = [_validated_model_row(row) for row in sheet.rows]
    duplicate_keys = Counter(
        (row["portfolio_name"], row["isin"]) for row in parsed_rows
    )
    metric_ids = _metric_ids(connection, _MODEL_METRICS)
    snapshots: dict[str, int] = {}
    items: list[tuple[str, int, int, str, str, str, str | None, str]] = []
    for row in parsed_rows:
        instrument_id = _instrument_id(connection, row["isin"], row["product_name"])
        _insert_alias(
            connection, instrument_id, source_file_id, "MODEL_XLS", row["product_name"]
        )
        snapshot_id = snapshots.get(row["portfolio_name"])
        if snapshot_id is None:
            portfolio = connection.execute(
                "SELECT portfolio_id FROM portfolio WHERE portfolio_name=? AND portfolio_type='MODEL'",
                (row["portfolio_name"],),
            ).fetchone()
            if portfolio is None:
                portfolio_id = _last_id(
                    connection.execute(
                        "INSERT INTO portfolio(portfolio_name,portfolio_type) VALUES(?,'MODEL')",
                        (row["portfolio_name"],),
                    )
                )
            else:
                portfolio_id = int(portfolio[0])
            existing = connection.execute(
                "SELECT 1 FROM portfolio_snapshot WHERE portfolio_id=? AND snapshot_date=?",
                (portfolio_id, workbook.snapshot_date),
            ).fetchone()
            if existing is not None:
                raise ModelPortfolioPhase3BError("model snapshot date already exists")
            snapshot_id = _last_id(
                connection.execute(
                    "INSERT INTO portfolio_snapshot(portfolio_id,snapshot_date,source_sheet_id) VALUES(?,?,?)",
                    (portfolio_id, workbook.snapshot_date, source_sheet_id),
                )
            )
            snapshots[row["portfolio_name"]] = snapshot_id
        payload = _canonical_json(row["source_values"])
        payload_sha = _sha256_text(payload)
        semantics = (
            "UNRESOLVED_DUPLICATE_SEMANTICS"
            if duplicate_keys[(row["portfolio_name"], row["isin"])] > 1
            else "SOURCE_REPORTED"
        )
        occurrence_id = _last_id(
            connection.execute(
                """INSERT INTO portfolio_holding_source_occurrence(
                       portfolio_snapshot_id,instrument_id,source_sheet_id,source_row_number,
                       reported_weight,observed_product_name,observed_currency_code,
                       observed_currency_risk,observed_asset_class,observed_sub_asset_class,
                       source_payload_json,source_payload_sha256,source_semantics_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot_id,
                    instrument_id,
                    source_sheet_id,
                    row["source_row"],
                    row["allocation"],
                    row["product_name"],
                    row["currency"],
                    row["currency_risk"],
                    row["asset_class"],
                    row["sub_asset_class"],
                    payload,
                    payload_sha,
                    semantics,
                ),
            )
        )
        for code, _name, header in _MODEL_METRICS:
            value = _number_or_none(
                row["parsed_values"][header], header, zero_is_null=True
            )
            if value is not None:
                connection.execute(
                    """INSERT INTO instrument_metric_observation(
                           instrument_id,metric_id,observation_date,value,provenance_type,
                           source_file_id,source_reference)
                       VALUES(?,?,?,?, 'PROVIDER_REPORTED',?,?)""",
                    (
                        instrument_id,
                        metric_ids[code],
                        workbook.snapshot_date,
                        value,
                        source_file_id,
                        f"MODEL_PHASE3B1:{workbook_sha256}:{sheet.name}:{row['source_row']}:{header}",
                    ),
                )
        stable = canonical_fingerprint(
            {
                "isin": row["isin"],
                "portfolio_name": row["portfolio_name"],
                "snapshot_date": workbook.snapshot_date,
                "source_file_sha256": workbook_sha256,
                "source_row_number": row["source_row"],
                "source_sheet_name": sheet.name,
            }
        )
        items.append(
            (
                stable,
                occurrence_id,
                row["source_row"],
                payload_sha,
                (parsed_payload := _canonical_json(row["parsed_values"])),
                _sha256_text(parsed_payload),
                row["parsed_fields"].sustainability,
                row["parsed_fields"].fingerprint,
            )
        )
    return items


def _insert_shortlist_rows(
    connection: sqlite3.Connection,
    admission_id: str,
    workbook: _Workbook,
    source_file_id: int,
    source_sheet_id: int,
    workbook_sha256: str,
) -> list[tuple[str, int, int, str, str, str]]:
    del admission_id
    sheet = workbook.sheet(SHORTLIST_ROLE)
    parsed_rows = [_validated_shortlist_row(row) for row in sheet.rows]
    counts = Counter(row["isin"] for row in parsed_rows)
    metric_ids = _metric_ids(connection, _SHORTLIST_METRICS)
    snapshot_id = _last_id(
        connection.execute(
            "INSERT INTO shortlist_snapshot(snapshot_date,source_sheet_id) VALUES(?,?)",
            (workbook.snapshot_date, source_sheet_id),
        )
    )
    entries: dict[str, int] = {}
    items: list[tuple[str, int, int, str, str, str]] = []
    for row in parsed_rows:
        instrument_id = _instrument_id(connection, row["isin"], row["product_name"])
        _insert_alias(
            connection,
            instrument_id,
            source_file_id,
            "SHORTLIST_XLS",
            row["product_name"],
        )
        conflict = (
            "SOURCE_METADATA_CONFLICT" if counts[row["isin"]] > 1 else "SOURCE_REPORTED"
        )
        entry_id = entries.get(row["isin"])
        if entry_id is None:
            entry_id = _last_id(
                connection.execute(
                    "INSERT INTO shortlist_entry(shortlist_snapshot_id,instrument_id,source_row_number,status) VALUES(?,?,?,?)",
                    (snapshot_id, instrument_id, row["source_row"], conflict),
                )
            )
            entries[row["isin"]] = entry_id
        payload = _canonical_json(row["source_values"])
        payload_sha = _sha256_text(payload)
        occurrence_id = _last_id(
            connection.execute(
                """INSERT INTO shortlist_entry_source_occurrence(
                       shortlist_snapshot_id,instrument_id,source_sheet_id,source_row_number,
                       observed_product_name,observed_currency_code,observed_asset_class,
                       observed_sub_asset_class,source_payload_json,conflict_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot_id,
                    instrument_id,
                    source_sheet_id,
                    row["source_row"],
                    row["product_name"],
                    row["currency"],
                    row["asset_class"],
                    row["sub_asset_class"],
                    payload,
                    conflict,
                ),
            )
        )
        connection.execute(
            "INSERT INTO shortlist_entry_lineage(shortlist_entry_id,source_occurrence_id) VALUES(?,?)",
            (entry_id, occurrence_id),
        )
        for code, _name, header in _SHORTLIST_METRICS:
            value = _number_or_none(
                row["parsed_values"][header], header, zero_is_null=False
            )
            if value is not None:
                connection.execute(
                    """INSERT INTO instrument_metric_observation(
                           instrument_id,metric_id,observation_date,value,provenance_type,
                           source_file_id,source_reference)
                       VALUES(?,?,?,?, 'PROVIDER_REPORTED',?,?)""",
                    (
                        instrument_id,
                        metric_ids[code],
                        workbook.snapshot_date,
                        value,
                        source_file_id,
                        f"SHORTLIST:{workbook_sha256}:{sheet.name}:{row['source_row']}:{header}",
                    ),
                )
        stable = canonical_fingerprint(
            {
                "isin": row["isin"],
                "snapshot_date": workbook.snapshot_date,
                "source_file_sha256": workbook_sha256,
                "source_row_number": row["source_row"],
                "source_sheet_name": sheet.name,
            }
        )
        parsed_payload = _canonical_json(row["parsed_values"])
        items.append(
            (
                stable,
                occurrence_id,
                row["source_row"],
                payload_sha,
                parsed_payload,
                _sha256_text(parsed_payload),
            )
        )
    return items


def _insert_writer_items(
    connection: sqlite3.Connection,
    admission_id: str,
    model_items: Sequence[tuple[str, int, int, str, str, str, str | None, str]],
    shortlist_items: Sequence[tuple[str, int, int, str, str, str]],
) -> None:
    connection.executemany(
        "INSERT INTO model_workbook_writer_model_item VALUES(?,?,?,?,?,?,?,?,?)",
        ((admission_id, *item) for item in model_items),
    )
    connection.executemany(
        "INSERT INTO model_workbook_writer_shortlist_item VALUES(?,?,?,?,?,?,?)",
        ((admission_id, *item) for item in shortlist_items),
    )


def _insert_outbox(
    connection: sqlite3.Connection,
    admission_id: str,
    workbook_binding: str,
    model_after: str,
    shortlist_after: str,
) -> None:
    for work_type in _OUTBOX_WORK:
        payload = _canonical_json(
            {
                "admission_id": admission_id,
                "model_fingerprint_after": model_after,
                "shortlist_fingerprint_after": shortlist_after,
                "work_type": work_type,
                "workbook_binding_fingerprint": workbook_binding,
            }
        )
        connection.execute(
            "INSERT INTO model_workbook_writer_outbox VALUES(?,?,?,'PENDING',?,?,CURRENT_TIMESTAMP)",
            (
                canonical_fingerprint(
                    {"admission_id": admission_id, "work_type": work_type}
                ),
                admission_id,
                work_type,
                payload,
                _sha256_text(payload),
            ),
        )


def _validate_phase3b1_records(connection: sqlite3.Connection) -> None:
    authorities = connection.execute(
        "SELECT * FROM model_workbook_writer_authority ORDER BY authority_id"
    ).fetchall()
    for authority in authorities:
        if (
            int(authority["contract_version"]) != CONTRACT_VERSION
            or str(authority["operational_status"]) != NON_OPERATIONAL_STATUS
            or not str(authority["authorization_reference"]).strip()
        ):
            raise ModelPortfolioPhase3BError("writer authority binding is invalid")
    admissions = connection.execute(
        "SELECT * FROM model_workbook_writer_admission ORDER BY admission_sequence"
    ).fetchall()
    if [int(row["admission_sequence"]) for row in admissions] != list(
        range(1, len(admissions) + 1)
    ):
        raise ModelPortfolioPhase3BError("writer admission sequence is not contiguous")
    prior: sqlite3.Row | None = None
    for admission in admissions:
        admission_id = str(admission["admission_id"])
        if int(admission["contract_version"]) != CONTRACT_VERSION:
            raise ModelPortfolioPhase3BError(
                "writer admission contract version is stale"
            )
        expected_predecessor = None if prior is None else str(prior["admission_id"])
        if admission["predecessor_admission_id"] != expected_predecessor:
            raise ModelPortfolioPhase3BError("writer predecessor chain is invalid")
        if prior is not None and (
            str(admission["expected_model_fingerprint_before"])
            != str(prior["model_fingerprint_after"])
            or str(admission["expected_shortlist_fingerprint_before"])
            != str(prior["shortlist_fingerprint_after"])
        ):
            raise ModelPortfolioPhase3BError(
                "writer dataset fingerprint chain is broken"
            )
        for field in (
            "workbook_sha256",
            "expected_model_fingerprint_before",
            "expected_shortlist_fingerprint_before",
            "model_fingerprint_after",
            "shortlist_fingerprint_after",
            "workbook_binding_fingerprint",
            "request_fingerprint",
        ):
            _require_sha256(str(admission[field]), field)
        source = connection.execute(
            "SELECT filename FROM source_file WHERE sha256=?",
            (admission["workbook_sha256"],),
        ).fetchall()
        if [str(row[0]) for row in source] != [str(admission["filename"])]:
            raise ModelPortfolioPhase3BError(
                "writer receipt source evidence is missing"
            )
        sheets = connection.execute(
            "SELECT * FROM model_workbook_writer_sheet WHERE admission_id=? ORDER BY sheet_role",
            (admission_id,),
        ).fetchall()
        if {str(row["sheet_role"]) for row in sheets} != {MODEL_ROLE, SHORTLIST_ROLE}:
            raise ModelPortfolioPhase3BError("writer sheet inventory is incomplete")
        for sheet in sheets:
            _require_sha256(str(sheet["header_signature"]), "header_signature")
            _require_sha256(str(sheet["sheet_fingerprint"]), "sheet_fingerprint")
            expected_count = (
                int(admission["model_item_count"])
                if str(sheet["sheet_role"]) == MODEL_ROLE
                else int(admission["shortlist_item_count"])
            )
            if int(sheet["item_count"]) != expected_count:
                raise ModelPortfolioPhase3BError("writer sheet item count is stale")
        ordered_sheets = sorted(
            sheets,
            key=lambda row: (
                str(row["sheet_role"]) != MODEL_ROLE,
                str(row["sheet_role"]),
            ),
        )
        expected_binding = canonical_fingerprint(
            {
                "format": WORKBOOK_FORMAT,
                "sha256": str(admission["workbook_sha256"]),
                "snapshot_date": str(admission["snapshot_date"]),
                "sheets": tuple(
                    (
                        str(sheet["sheet_role"]),
                        str(sheet["sheet_name"]),
                        str(sheet["sheet_fingerprint"]),
                    )
                    for sheet in ordered_sheets
                ),
            }
        )
        authority = connection.execute(
            "SELECT authorization_reference FROM model_workbook_writer_authority WHERE authority_id=?",
            (admission["authority_id"],),
        ).fetchone()
        if authority is None:
            raise ModelPortfolioPhase3BError("writer admission authority is missing")
        expected_request = canonical_fingerprint(
            {
                "admission_id": admission_id,
                "authority_id": str(admission["authority_id"]),
                "authorization_reference": str(authority[0]),
                "expected_model_fingerprint_before": str(
                    admission["expected_model_fingerprint_before"]
                ),
                "expected_predecessor_admission_id": admission[
                    "predecessor_admission_id"
                ],
                "expected_shortlist_fingerprint_before": str(
                    admission["expected_shortlist_fingerprint_before"]
                ),
                "expected_workbook_sha256": str(admission["workbook_sha256"]),
                "filename": str(admission["filename"]),
                "retained_relative_path": str(admission["retained_relative_path"]),
                "workbook_binding_fingerprint": expected_binding,
            }
        )
        if (
            str(admission["workbook_binding_fingerprint"]) != expected_binding
            or str(admission["request_fingerprint"]) != expected_request
        ):
            raise ModelPortfolioPhase3BError("writer receipt fingerprint is stale")
        model_count = int(
            connection.execute(
                "SELECT count(*) FROM model_workbook_writer_model_item WHERE admission_id=?",
                (admission_id,),
            ).fetchone()[0]
        )
        shortlist_count = int(
            connection.execute(
                "SELECT count(*) FROM model_workbook_writer_shortlist_item WHERE admission_id=?",
                (admission_id,),
            ).fetchone()[0]
        )
        if (model_count, shortlist_count) != (
            int(admission["model_item_count"]),
            int(admission["shortlist_item_count"]),
        ):
            raise ModelPortfolioPhase3BError("writer receipt item counts are stale")
        _validate_model_item_bindings(connection, admission_id)
        _validate_shortlist_item_bindings(connection, admission_id)
        outbox = connection.execute(
            "SELECT * FROM model_workbook_writer_outbox WHERE admission_id=? ORDER BY work_type",
            (admission_id,),
        ).fetchall()
        if {str(row["work_type"]) for row in outbox} != set(_OUTBOX_WORK):
            raise ModelPortfolioPhase3BError("writer pending work is incomplete")
        for row in outbox:
            expected_payload = _canonical_json(
                {
                    "admission_id": admission_id,
                    "model_fingerprint_after": str(
                        admission["model_fingerprint_after"]
                    ),
                    "shortlist_fingerprint_after": str(
                        admission["shortlist_fingerprint_after"]
                    ),
                    "work_type": str(row["work_type"]),
                    "workbook_binding_fingerprint": str(
                        admission["workbook_binding_fingerprint"]
                    ),
                }
            )
            expected_outbox_id = canonical_fingerprint(
                {"admission_id": admission_id, "work_type": str(row["work_type"])}
            )
            if (
                str(row["state"]) != "PENDING"
                or str(row["outbox_id"]) != expected_outbox_id
                or str(row["payload_json"]) != expected_payload
                or _sha256_text(expected_payload) != str(row["payload_sha256"])
            ):
                raise ModelPortfolioPhase3BError("writer pending-work binding is stale")
        prior = admission
    if admissions:
        latest = admissions[-1]
        if str(latest["model_fingerprint_after"]) != source_dataset_fingerprint(
            connection
        ):
            raise ModelPortfolioPhase3BError("latest writer model fingerprint is stale")
        if str(latest["shortlist_fingerprint_after"]) != shortlist_dataset_fingerprint(
            connection
        ):
            raise ModelPortfolioPhase3BError(
                "latest writer shortlist fingerprint is stale"
            )
    model_coverage = connection.execute(
        """SELECT
               (SELECT count(*) FROM portfolio_holding_source_occurrence AS occurrence
                JOIN source_sheet ON source_sheet.source_sheet_id=occurrence.source_sheet_id
                JOIN source_file ON source_file.source_file_id=source_sheet.source_file_id
                WHERE source_file.source_type='SYNTHETIC_DUAL_SHEET_WORKBOOK'),
               (SELECT count(*) FROM model_workbook_writer_model_item)"""
    ).fetchone()
    shortlist_coverage = connection.execute(
        """SELECT
               (SELECT count(*) FROM shortlist_entry_source_occurrence AS occurrence
                JOIN source_sheet ON source_sheet.source_sheet_id=occurrence.source_sheet_id
                JOIN source_file ON source_file.source_file_id=source_sheet.source_file_id
                WHERE source_file.source_type='SYNTHETIC_DUAL_SHEET_WORKBOOK'),
               (SELECT count(*) FROM model_workbook_writer_shortlist_item)"""
    ).fetchone()
    if (
        tuple(model_coverage)
        != (sum(int(row["model_item_count"]) for row in admissions),) * 2
    ):
        raise ModelPortfolioPhase3BError(
            "writer model occurrence coverage is incomplete"
        )
    if (
        tuple(shortlist_coverage)
        != (sum(int(row["shortlist_item_count"]) for row in admissions),) * 2
    ):
        raise ModelPortfolioPhase3BError(
            "writer shortlist occurrence coverage is incomplete"
        )


def _validate_model_item_bindings(
    connection: sqlite3.Connection, admission_id: str
) -> None:
    rows = connection.execute(
        """SELECT item.*, occurrence.source_payload_json,
                  occurrence.source_payload_sha256 AS occurrence_payload_sha256,
                  source_file.sha256 AS source_file_sha256,
                  source_sheet.sheet_name, snapshot.snapshot_date,
                  portfolio.portfolio_name, instrument.isin
           FROM model_workbook_writer_model_item AS item
           JOIN portfolio_holding_source_occurrence AS occurrence
             ON occurrence.portfolio_holding_source_occurrence_id=item.source_occurrence_id
           JOIN portfolio_snapshot AS snapshot
             ON snapshot.portfolio_snapshot_id=occurrence.portfolio_snapshot_id
           JOIN portfolio ON portfolio.portfolio_id=snapshot.portfolio_id
           JOIN instrument ON instrument.instrument_id=occurrence.instrument_id
           JOIN source_sheet ON source_sheet.source_sheet_id=occurrence.source_sheet_id
           JOIN source_file ON source_file.source_file_id=source_sheet.source_file_id
           WHERE item.admission_id=? ORDER BY item.stable_source_reference""",
        (admission_id,),
    ).fetchall()
    for row in rows:
        expected_reference = canonical_fingerprint(
            {
                "isin": str(row["isin"]),
                "portfolio_name": str(row["portfolio_name"]),
                "snapshot_date": str(row["snapshot_date"]),
                "source_file_sha256": str(row["source_file_sha256"]),
                "source_row_number": int(row["source_row_number"]),
                "source_sheet_name": str(row["sheet_name"]),
            }
        )
        payload = str(row["source_payload_json"])
        parsed_payload = str(row["parsed_payload_json"])
        try:
            values = json.loads(parsed_payload)
            parsed = normalize_parsed_fields(
                {field: values[field] for field in RAW_ONLY_FIELDS}
            )
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ModelPortfolioPhase3BError(
                "writer model source payload is malformed"
            ) from error
        if (
            str(row["stable_source_reference"]) != expected_reference
            or str(row["source_payload_sha256"]) != _sha256_text(payload)
            or str(row["occurrence_payload_sha256"]) != _sha256_text(payload)
            or str(row["parsed_payload_sha256"]) != _sha256_text(parsed_payload)
            or row["sustainability"] != parsed.sustainability
            or str(row["parsed_fields_fingerprint"]) != parsed.fingerprint
        ):
            raise ModelPortfolioPhase3BError("writer model item binding is stale")
        expected_metrics = tuple(
            sorted(
                (
                    code,
                    value,
                    f"MODEL_PHASE3B1:{row['source_file_sha256']}:{row['sheet_name']}:{row['source_row_number']}:{header}",
                )
                for code, _name, header in _MODEL_METRICS
                if (value := _number_or_none(values[header], header, zero_is_null=True))
                is not None
            )
        )
        stored_metrics = tuple(
            (
                str(metric[0]),
                float(metric[1]),
                str(metric[2]),
            )
            for metric in connection.execute(
                """SELECT definition.metric_code, observation.value,
                          observation.source_reference
                   FROM instrument_metric_observation AS observation
                   JOIN metric_definition AS definition
                     ON definition.metric_id=observation.metric_id
                   WHERE observation.instrument_id=(
                         SELECT instrument_id FROM portfolio_holding_source_occurrence
                         WHERE portfolio_holding_source_occurrence_id=?)
                     AND observation.observation_date=?
                     AND observation.source_reference LIKE ?
                   ORDER BY definition.metric_code, observation.source_reference""",
                (
                    row["source_occurrence_id"],
                    row["snapshot_date"],
                    f"MODEL_PHASE3B1:{row['source_file_sha256']}:{row['sheet_name']}:{row['source_row_number']}:%",
                ),
            )
        )
        if stored_metrics != expected_metrics:
            raise ModelPortfolioPhase3BError("writer model metric binding is stale")


def _validate_shortlist_item_bindings(
    connection: sqlite3.Connection, admission_id: str
) -> None:
    rows = connection.execute(
        """SELECT item.*, occurrence.source_payload_json,
                  source_file.sha256 AS source_file_sha256,
                  source_sheet.sheet_name, snapshot.snapshot_date, instrument.isin
           FROM model_workbook_writer_shortlist_item AS item
           JOIN shortlist_entry_source_occurrence AS occurrence
             ON occurrence.shortlist_entry_source_occurrence_id=item.source_occurrence_id
           JOIN shortlist_snapshot AS snapshot
             ON snapshot.shortlist_snapshot_id=occurrence.shortlist_snapshot_id
           JOIN instrument ON instrument.instrument_id=occurrence.instrument_id
           JOIN source_sheet ON source_sheet.source_sheet_id=occurrence.source_sheet_id
           JOIN source_file ON source_file.source_file_id=source_sheet.source_file_id
           WHERE item.admission_id=? ORDER BY item.stable_source_reference""",
        (admission_id,),
    ).fetchall()
    for row in rows:
        expected_reference = canonical_fingerprint(
            {
                "isin": str(row["isin"]),
                "snapshot_date": str(row["snapshot_date"]),
                "source_file_sha256": str(row["source_file_sha256"]),
                "source_row_number": int(row["source_row_number"]),
                "source_sheet_name": str(row["sheet_name"]),
            }
        )
        payload = str(row["source_payload_json"])
        parsed_payload = str(row["parsed_payload_json"])
        try:
            values = json.loads(parsed_payload)
        except json.JSONDecodeError as error:
            raise ModelPortfolioPhase3BError(
                "writer shortlist source payload is malformed"
            ) from error
        if (
            str(row["stable_source_reference"]) != expected_reference
            or str(row["source_payload_sha256"]) != _sha256_text(payload)
            or str(row["parsed_payload_sha256"]) != _sha256_text(parsed_payload)
        ):
            raise ModelPortfolioPhase3BError("writer shortlist item binding is stale")
        expected_metrics = tuple(
            sorted(
                (
                    code,
                    value,
                    f"SHORTLIST:{row['source_file_sha256']}:{row['sheet_name']}:{row['source_row_number']}:{header}",
                )
                for code, _name, header in _SHORTLIST_METRICS
                if (
                    value := _number_or_none(values[header], header, zero_is_null=False)
                )
                is not None
            )
        )
        stored_metrics = tuple(
            (
                str(metric[0]),
                float(metric[1]),
                str(metric[2]),
            )
            for metric in connection.execute(
                """SELECT definition.metric_code, observation.value,
                          observation.source_reference
                   FROM instrument_metric_observation AS observation
                   JOIN metric_definition AS definition
                     ON definition.metric_id=observation.metric_id
                   WHERE observation.instrument_id=(
                         SELECT instrument_id FROM shortlist_entry_source_occurrence
                         WHERE shortlist_entry_source_occurrence_id=?)
                     AND observation.observation_date=?
                     AND observation.source_reference LIKE ?
                   ORDER BY definition.metric_code, observation.source_reference""",
                (
                    row["source_occurrence_id"],
                    row["snapshot_date"],
                    f"SHORTLIST:{row['source_file_sha256']}:{row['sheet_name']}:{row['source_row_number']}:%",
                ),
            )
        )
        if stored_metrics != expected_metrics:
            raise ModelPortfolioPhase3BError("writer shortlist metric binding is stale")


def _validate_replay(
    connection: sqlite3.Connection,
    request: SyntheticWorkbookAdmissionRequest,
    workbook: _Workbook,
    workbook_sha256: str,
    request_fingerprint: str,
    existing: sqlite3.Row,
) -> SyntheticWorkbookAdmissionResult:
    if (
        str(existing["authority_id"]) != request.authority_id
        or str(existing["workbook_sha256"]) != workbook_sha256
        or str(existing["filename"]) != request.workbook_path.name
        or str(existing["retained_relative_path"])
        != _retained_relative_path(workbook_sha256)
        or str(existing["snapshot_date"]) != workbook.snapshot_date
        or existing["predecessor_admission_id"]
        != request.expected_predecessor_admission_id
        or str(existing["expected_model_fingerprint_before"])
        != request.expected_model_fingerprint_before
        or str(existing["expected_shortlist_fingerprint_before"])
        != request.expected_shortlist_fingerprint_before
        or str(existing["workbook_binding_fingerprint"])
        != _workbook_binding_fingerprint(workbook, workbook_sha256)
        or str(existing["request_fingerprint"]) != request_fingerprint
    ):
        raise ModelPortfolioPhase3BError("writer replay bindings do not exactly match")
    validate_phase3b1_contracts(connection)
    return SyntheticWorkbookAdmissionResult(
        request.admission_id,
        workbook.snapshot_date,
        int(existing["model_item_count"]),
        int(existing["shortlist_item_count"]),
        len(_OUTBOX_WORK),
        str(existing["model_fingerprint_after"]),
        str(existing["shortlist_fingerprint_after"]),
        True,
    )


def _validate_new_admission_state(
    connection: sqlite3.Connection,
    request: SyntheticWorkbookAdmissionRequest,
    snapshot_date: str,
) -> None:
    latest = connection.execute(
        "SELECT admission_id FROM model_workbook_writer_admission ORDER BY admission_sequence DESC LIMIT 1"
    ).fetchone()
    expected = None if latest is None else str(latest[0])
    if request.expected_predecessor_admission_id != expected:
        raise ModelPortfolioPhase3BError("writer predecessor binding is stale")
    conflicts = connection.execute(
        "SELECT admission_id FROM model_workbook_writer_admission WHERE snapshot_date=?",
        (snapshot_date,),
    ).fetchall()
    if conflicts:
        raise ModelPortfolioPhase3BError(
            "snapshot date already has a different writer admission"
        )


def _installed_dataset_bound_state(connection: sqlite3.Connection) -> bool:
    names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'shortlist_%correction%' OR name LIKE 'v_effective_shortlist_%'"
        )
    }
    markers = connection.execute(
        "SELECT feature_id FROM schema_feature_contract WHERE feature_id LIKE 'SHORTLIST_%'"
    ).fetchall()
    phase1 = connection.execute(
        "SELECT 1 FROM schema_feature_contract WHERE feature_id='MODEL_PORTFOLIO_CONSOLIDATION_PHASE1'"
    ).fetchone()
    migration_manifest = connection.execute(
        "SELECT 1 FROM migration_build_manifest WHERE singleton=1"
    ).fetchone()
    shortlist_manifest = connection.execute(
        "SELECT 1 FROM shortlist_stage_manifest WHERE singleton=1"
    ).fetchone()
    return bool(names or markers or phase1 or migration_manifest or shortlist_manifest)


def _validated_model_row(row: Mapping[str, object]) -> dict[str, Any]:
    source_row, raw_values, values = _row_parts(row)
    parsed = normalize_parsed_fields(
        {field: values[field] for field in RAW_ONLY_FIELDS}
    )
    allocation = _number_or_none(
        values["Allocation (%)"], "Allocation (%)", zero_is_null=False
    )
    if allocation is None or allocation < 0:
        raise ModelPortfolioPhase3BError(
            "model allocation must be a non-negative number"
        )
    isin = _isin(values["ISIN"])
    return {
        "source_row": source_row,
        "source_values": raw_values,
        "parsed_values": values,
        "portfolio_name": _text(values["Portfolio Name"], "Portfolio Name"),
        "product_name": _text(values["Product"], "Product"),
        "isin": isin,
        "allocation": allocation,
        "asset_class": _optional_text(values["Asset Class"]),
        "sub_asset_class": _optional_text(values["Sub-Asset Class"]),
        "currency": _optional_text(values["Currency"]),
        "currency_risk": _optional_text(values["Currency Risk"]),
        "parsed_fields": parsed,
    }


def _validated_shortlist_row(row: Mapping[str, object]) -> dict[str, Any]:
    source_row, raw_values, values = _row_parts(row)
    for _code, _name, header in _SHORTLIST_METRICS:
        _number_or_none(values[header], header, zero_is_null=False)
    return {
        "source_row": source_row,
        "source_values": raw_values,
        "parsed_values": values,
        "product_name": _text(values["Product"], "Product"),
        "isin": _isin(values["ISIN"]),
        "asset_class": _optional_text(values["Asset Class"]),
        "sub_asset_class": _optional_text(values["Sub-Asset Class"]),
        "currency": _optional_text(values["Currency"]),
    }


def _row_parts(
    row: Mapping[str, object],
) -> tuple[int, Mapping[str, object], Mapping[str, object]]:
    source_row = row["source_row"]
    raw_values = row["raw_values"]
    values = row["values"]
    if (
        not isinstance(source_row, int)
        or not isinstance(raw_values, dict)
        or not isinstance(values, dict)
    ):
        raise ModelPortfolioPhase3BError("synthetic row is malformed")
    return source_row, raw_values, values


def _metric_ids(
    connection: sqlite3.Connection, metrics: Sequence[tuple[str, str, str]]
) -> dict[str, int]:
    result = {
        str(row[1]): int(row[0])
        for row in connection.execute(
            "SELECT metric_id,metric_code FROM metric_definition"
        )
    }
    for code, name, _header in metrics:
        if code not in result:
            result[code] = _last_id(
                connection.execute(
                    "INSERT INTO metric_definition(metric_code,name,unit,description) VALUES(?,?,'RATIO','Synthetic Phase 3B.1 provider-reported metric')",
                    (code, name),
                )
            )
    return result


def _instrument_id(connection: sqlite3.Connection, isin: str, name: str) -> int:
    row = connection.execute(
        "SELECT instrument_id FROM instrument WHERE isin=?", (isin,)
    ).fetchone()
    return int(row[0]) if row is not None else insert_instrument(connection, isin, name)


def _insert_alias(
    connection: sqlite3.Connection,
    instrument_id: int,
    source_file_id: int,
    source_type: str,
    source_name: str,
) -> None:
    normalized = " ".join(source_name.casefold().split())
    existing = connection.execute(
        "SELECT instrument_id FROM instrument_alias WHERE source_type=? AND normalized_source_name=? AND mapping_status IN ('EXPLICIT_ISIN_VALID','EXACT_ALIAS_CONFIRMED','MANUAL_CONFIRMED')",
        (source_type, normalized),
    ).fetchone()
    if existing is not None:
        if int(existing[0]) != instrument_id:
            raise ModelPortfolioPhase3BError(
                "source alias conflicts with another instrument"
            )
        return
    connection.execute(
        """INSERT INTO instrument_alias(
               instrument_id,source_file_id,source_type,source_name,
               normalized_source_name,mapping_status,resolution_evidence)
           VALUES(?,?,?,?,?,'EXPLICIT_ISIN_VALID','synthetic source supplied valid ISIN')""",
        (instrument_id, source_file_id, source_type, source_name, normalized),
    )


def _request_fingerprint(
    request: SyntheticWorkbookAdmissionRequest, workbook: _Workbook
) -> str:
    return canonical_fingerprint(
        {
            "admission_id": request.admission_id,
            "authority_id": request.authority_id,
            "authorization_reference": request.authorization_reference,
            "expected_model_fingerprint_before": request.expected_model_fingerprint_before,
            "expected_predecessor_admission_id": request.expected_predecessor_admission_id,
            "expected_shortlist_fingerprint_before": request.expected_shortlist_fingerprint_before,
            "expected_workbook_sha256": request.expected_workbook_sha256,
            "filename": request.workbook_path.name,
            "retained_relative_path": _retained_relative_path(
                request.expected_workbook_sha256
            ),
            "workbook_binding_fingerprint": _workbook_binding_fingerprint(
                workbook, request.expected_workbook_sha256
            ),
        }
    )


def _workbook_binding_fingerprint(workbook: _Workbook, workbook_sha256: str) -> str:
    return canonical_fingerprint(
        {
            "format": WORKBOOK_FORMAT,
            "sha256": workbook_sha256,
            "snapshot_date": workbook.snapshot_date,
            "sheets": tuple(
                (sheet.role, sheet.name, sheet.fingerprint) for sheet in workbook.sheets
            ),
        }
    )


def _validate_request(request: SyntheticWorkbookAdmissionRequest) -> None:
    for name, value in (
        ("admission_id", request.admission_id),
        ("authority_id", request.authority_id),
        ("authorization_reference", request.authorization_reference),
    ):
        if not value.strip():
            raise ModelPortfolioPhase3BError(f"{name} must be non-empty")
    for name, value in (
        ("expected_workbook_sha256", request.expected_workbook_sha256),
        (
            "expected_model_fingerprint_before",
            request.expected_model_fingerprint_before,
        ),
        (
            "expected_shortlist_fingerprint_before",
            request.expected_shortlist_fingerprint_before,
        ),
    ):
        _require_sha256(value, name)


def _validate_new_temporary_path(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        raise ModelPortfolioPhase3BError("synthetic writer target must not exist")
    _require_below_temp(path.parent.resolve())
    return path.resolve()


def _retain_workbook(root: Path, source: Path, expected_sha256: str) -> Path:
    if root.is_symlink():
        raise ModelPortfolioPhase3BError("retention root must not be a symlink")
    if root.exists() and not root.is_dir():
        raise ModelPortfolioPhase3BError("retention root must be a directory")
    _require_below_temp(root.parent.resolve() if not root.exists() else root.resolve())
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    _require_below_temp(resolved_root)
    destination = resolved_root / _retained_relative_path(expected_sha256)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ModelPortfolioPhase3BError("retained workbook must not be a symlink")
    if not destination.exists():
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{expected_sha256}.", suffix=".pending", dir=destination.parent
            )
            temporary_path = Path(temporary_name)
            with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
                for block in iter(lambda: reader.read(1024 * 1024), b""):
                    writer.write(block)
                writer.flush()
                os.fsync(writer.fileno())
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                pass
        except BaseException:
            if destination.exists() and _file_sha256(destination) != expected_sha256:
                raise ModelPortfolioPhase3BError(
                    "concurrently retained workbook SHA-256 conflicts"
                )
            raise
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    retained = _validate_existing_temporary_file(destination, "retained workbook")
    if _file_sha256(retained) != expected_sha256:
        raise ModelPortfolioPhase3BError("retained workbook SHA-256 conflicts")
    return retained


def _retained_relative_path(workbook_sha256: str) -> str:
    return f"workbooks/{workbook_sha256}.json"


def _validate_existing_temporary_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ModelPortfolioPhase3BError(f"{label} must be an existing ordinary file")
    resolved = path.resolve()
    _require_below_temp(resolved)
    if resolved.stat().st_nlink != 1:
        raise ModelPortfolioPhase3BError(f"{label} hard links are not allowed")
    return resolved


def _require_below_temp(path: Path) -> None:
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        path.relative_to(temp_root)
    except ValueError as error:
        raise ModelPortfolioPhase3BError(
            "Phase 3B.1 paths must resolve below the system temporary directory"
        ) from error


def _number_or_none(value: object, field: str, *, zero_is_null: bool) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelPortfolioPhase3BError(
            f"{field} must be a parsed finite number or NULL"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ModelPortfolioPhase3BError(f"{field} must be finite")
    return None if zero_is_null and number == 0.0 else number


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelPortfolioPhase3BError(f"{field} must be non-empty text")
    return value


def _optional_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ModelPortfolioPhase3BError(
            "optional classification fields must be text or NULL"
        )
    return value


def _isin(value: object) -> str:
    isin = _text(value, "ISIN")
    if not is_valid_isin(isin):
        raise ModelPortfolioPhase3BError("synthetic workbook contains an invalid ISIN")
    return isin


def _iso_date(value: object, field: str) -> str:
    text = _text(value, field)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise ModelPortfolioPhase3BError(f"{field} is not an ISO date") from error


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _last_id(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise ModelPortfolioPhase3BError("SQLite insert did not return an identifier")
    return int(cursor.lastrowid)


def _require_sha256(value: str, field: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ModelPortfolioPhase3BError(f"{field} must be lowercase SHA-256")


def _schema_object_names(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master")}


def _schema_objects(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str], ...]:
    rows = connection.execute(
        """SELECT type,name,tbl_name,sql FROM sqlite_master
           WHERE sql IS NOT NULL AND name IN ({}) ORDER BY type,name""".format(
            ",".join("?" for _ in _OBJECT_NAMES)
        ),
        tuple(sorted(_OBJECT_NAMES)),
    ).fetchall()
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]), " ".join(str(row[3]).split()))
        for row in rows
    )


@lru_cache(maxsize=1)
def _expected_schema_objects() -> tuple[tuple[str, str, str, str], ...]:
    with sqlite3.connect(":memory:") as scratch:
        initialize_schema(scratch)
        _execute_schema(scratch)
        return _schema_objects(scratch)


def _feature_marker(connection: sqlite3.Connection) -> list[tuple[int, str]]:
    return [
        (int(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT revision,contract_fingerprint FROM schema_feature_contract WHERE feature_id=?",
            (FEATURE_ID,),
        )
    ]


def _execute_schema(connection: sqlite3.Connection) -> None:
    statement = ""
    for line in _SCHEMA_SQL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ModelPortfolioPhase3BError("Phase 3B.1 schema SQL is incomplete")


def _call_hook(
    hook: Callable[[str, sqlite3.Connection], None] | None,
    point: str,
    connection: sqlite3.Connection,
) -> None:
    if hook is not None:
        hook(point, connection)
