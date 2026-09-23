"""Phase 1 model-portfolio consolidation contracts and opt-in readers.

This module does not select an operational database, ingest workbooks, or
authorize cutover.  Its write APIs are intended for synthetic temporary
databases until a later phase supplies an approved writer and authority epoch.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from numbers import Real
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.database.migrations.model_portfolio_dry_run import (
    SchemaV3ModelPortfolioRepository,
)
from portfolio_advisor.database.schema.v3 import (
    transaction,
    validate_schema,
)
from portfolio_advisor.history.mnb_otc import (
    MnbOtcError,
    MnbOtcObservation,
    decimal_text,
    parse_decimal,
    parse_otc_price,
    parse_transaction_count,
)

FEATURE_ID = "MODEL_PORTFOLIO_CONSOLIDATION_PHASE1"
FEATURE_REVISION = 1
CONTRACT_VERSION = 1
PARSER_VERSION = "MODEL_WORKBOOK_PARSER_V1"
NON_OPERATIONAL_STATUS = "PHASE1_NON_OPERATIONAL"

RAW_ONLY_FIELDS = (
    "Sustainability",
    "YTD",
    "3 Years",
    "5 Years",
    "3Y Sharpe Ratio",
    "5Y Sharpe Ratio",
    "3Y Volatility",
    "Information Ratio",
)

_EXTENDED_METRICS = (
    ("YTD", "YTD", "ytd"),
    ("RETURN_3Y", "Return 3 years", "return_3y"),
    ("RETURN_5Y", "Return 5 years", "return_5y"),
    ("SHARPE_RATIO_3Y", "Sharpe ratio 3 years", "sharpe_ratio_3y"),
    ("SHARPE_RATIO_5Y", "Sharpe ratio 5 years", "sharpe_ratio_5y"),
    ("VOLATILITY_3Y", "Volatility 3 years", "volatility_3y"),
    ("INFORMATION_RATIO", "Information ratio", "information_ratio"),
)

FEATURE_FINGERPRINT = canonical_fingerprint(
    {
        "contract_version": CONTRACT_VERSION,
        "extended_metric_codes": tuple(
            code for code, _name, _field in _EXTENDED_METRICS
        ),
        "feature_id": FEATURE_ID,
        "parser_version": PARSER_VERSION,
        "raw_only_fields": RAW_ONLY_FIELDS,
        "revision": FEATURE_REVISION,
        "status": NON_OPERATIONAL_STATUS,
        "tables": (
            "model_import_batch",
            "model_mnb_otc_evidence_observation",
            "model_mnb_otc_evidence_source",
            "model_source_authority_epoch",
            "model_source_occurrence_typed_extension",
            "model_workbook_admission",
            "model_workbook_admission_item",
        ),
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ModelPortfolioPhase1Error(RuntimeError):
    """A Phase 1 schema, provenance, or replay contract failed closed."""


@dataclass(frozen=True, slots=True)
class SourceAuthorityEpoch:
    """A non-operational authority binding used only for Phase 1 rehearsal."""

    epoch_id: str
    baseline_source_sha256: str
    baseline_dataset_fingerprint: str
    parser_version: str
    ranking_policy_sha256: str
    authorization_reference: str


@dataclass(frozen=True, slots=True)
class ParsedModelFields:
    """The eight legacy-parsed fields that previously existed only in raw JSON."""

    sustainability: str | None
    ytd: float | None
    return_3y: float | None
    return_5y: float | None
    sharpe_ratio_3y: float | None
    sharpe_ratio_5y: float | None
    volatility_3y: float | None
    information_ratio: float | None

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(asdict(self))


@dataclass(frozen=True, slots=True)
class ModelOccurrenceBinding:
    """Stable source identity plus parsed values for one existing occurrence."""

    source_occurrence_id: int
    source_file_sha256: str
    source_sheet_name: str
    source_row_number: int
    snapshot_date: str
    portfolio_name: str
    isin: str
    source_payload_sha256: str
    parsed_fields: ParsedModelFields

    @property
    def stable_source_reference(self) -> str:
        return canonical_fingerprint(
            {
                "isin": self.isin,
                "portfolio_name": self.portfolio_name,
                "snapshot_date": self.snapshot_date,
                "source_file_sha256": self.source_file_sha256,
                "source_row_number": self.source_row_number,
                "source_sheet_name": self.source_sheet_name,
            }
        )


@dataclass(frozen=True, slots=True)
class WorkbookAdmissionRequest:
    """Exact, replayable normalization request for an already staged workbook."""

    admission_id: str
    batch_id: str
    authority: SourceAuthorityEpoch
    filename: str
    source_file_sha256: str
    snapshot_date: str
    source_sheet_name: str
    header_signature: str
    expected_source_dataset_fingerprint: str
    authorization_reference: str
    items: tuple[ModelOccurrenceBinding, ...]


@dataclass(frozen=True, slots=True)
class WorkbookAdmissionResult:
    admission_id: str
    batch_id: str
    item_count: int
    admission_fingerprint: str
    typed_projection_fingerprint: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class TypedModelOccurrence:
    source_occurrence_id: int
    stable_source_reference: str
    portfolio_name: str
    isin: str
    source_row_number: int
    source_semantics_status: str
    fields: ParsedModelFields


@dataclass(frozen=True, slots=True)
class MnbEvidenceBinding:
    portable_evidence_role: str
    authorization_reference: str


_SCHEMA_SQL = """
CREATE TABLE model_source_authority_epoch (
    epoch_id TEXT PRIMARY KEY CHECK(length(trim(epoch_id)) > 0),
    contract_version INTEGER NOT NULL CHECK(contract_version = 1),
    operational_status TEXT NOT NULL CHECK(operational_status = 'PHASE1_NON_OPERATIONAL'),
    baseline_source_sha256 TEXT NOT NULL CHECK(length(baseline_source_sha256) = 64),
    baseline_dataset_fingerprint TEXT NOT NULL CHECK(length(baseline_dataset_fingerprint) = 64),
    parser_version TEXT NOT NULL CHECK(length(trim(parser_version)) > 0),
    ranking_policy_sha256 TEXT NOT NULL CHECK(length(ranking_policy_sha256) = 64),
    authorization_reference TEXT NOT NULL CHECK(length(trim(authorization_reference)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE model_workbook_admission (
    admission_id TEXT PRIMARY KEY CHECK(length(trim(admission_id)) > 0),
    epoch_id TEXT NOT NULL REFERENCES model_source_authority_epoch(epoch_id),
    contract_version INTEGER NOT NULL CHECK(contract_version = 1),
    source_file_sha256 TEXT NOT NULL CHECK(length(source_file_sha256) = 64),
    filename TEXT NOT NULL CHECK(length(trim(filename)) > 0),
    snapshot_date TEXT NOT NULL CHECK(length(snapshot_date) = 10),
    source_sheet_name TEXT NOT NULL CHECK(length(trim(source_sheet_name)) > 0),
    header_signature TEXT NOT NULL CHECK(length(header_signature) = 64),
    expected_source_dataset_fingerprint TEXT NOT NULL CHECK(length(expected_source_dataset_fingerprint) = 64),
    parser_version TEXT NOT NULL CHECK(length(trim(parser_version)) > 0),
    authorization_reference TEXT NOT NULL CHECK(length(trim(authorization_reference)) > 0),
    admission_fingerprint TEXT NOT NULL UNIQUE CHECK(length(admission_fingerprint) = 64),
    item_count INTEGER NOT NULL CHECK(item_count > 0),
    admission_status TEXT NOT NULL CHECK(admission_status = 'PHASE1_VALIDATED_TEMPORARY'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(snapshot_date, source_sheet_name)
);
CREATE TABLE model_workbook_admission_item (
    admission_id TEXT NOT NULL REFERENCES model_workbook_admission(admission_id),
    stable_source_reference TEXT NOT NULL CHECK(length(stable_source_reference) = 64),
    source_occurrence_id INTEGER NOT NULL UNIQUE
        REFERENCES portfolio_holding_source_occurrence(portfolio_holding_source_occurrence_id),
    source_file_sha256 TEXT NOT NULL CHECK(length(source_file_sha256) = 64),
    source_sheet_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    snapshot_date TEXT NOT NULL CHECK(length(snapshot_date) = 10),
    portfolio_name TEXT NOT NULL,
    isin TEXT NOT NULL,
    source_payload_sha256 TEXT NOT NULL CHECK(length(source_payload_sha256) = 64),
    parsed_fields_fingerprint TEXT NOT NULL CHECK(length(parsed_fields_fingerprint) = 64),
    PRIMARY KEY(admission_id, stable_source_reference)
);
CREATE TABLE model_source_occurrence_typed_extension (
    source_occurrence_id INTEGER PRIMARY KEY
        REFERENCES portfolio_holding_source_occurrence(portfolio_holding_source_occurrence_id),
    admission_id TEXT NOT NULL,
    stable_source_reference TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    sustainability TEXT NULL,
    parsed_fields_fingerprint TEXT NOT NULL CHECK(length(parsed_fields_fingerprint) = 64),
    FOREIGN KEY(admission_id, stable_source_reference)
        REFERENCES model_workbook_admission_item(admission_id, stable_source_reference),
    UNIQUE(stable_source_reference)
);
CREATE TABLE model_import_batch (
    batch_id TEXT PRIMARY KEY CHECK(length(trim(batch_id)) > 0),
    admission_id TEXT NOT NULL UNIQUE REFERENCES model_workbook_admission(admission_id),
    batch_sequence INTEGER NOT NULL UNIQUE CHECK(batch_sequence > 0),
    source_dataset_fingerprint_before TEXT NOT NULL CHECK(length(source_dataset_fingerprint_before) = 64),
    source_dataset_fingerprint_after TEXT NOT NULL CHECK(length(source_dataset_fingerprint_after) = 64),
    typed_projection_fingerprint TEXT NOT NULL CHECK(length(typed_projection_fingerprint) = 64),
    outcome TEXT NOT NULL CHECK(outcome = 'PHASE1_NORMALIZATION_ONLY'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE model_mnb_otc_evidence_source (
    source_document_hash TEXT PRIMARY KEY CHECK(length(source_document_hash) = 64),
    contract_version INTEGER NOT NULL CHECK(contract_version = 1),
    portable_evidence_role TEXT NOT NULL CHECK(length(trim(portable_evidence_role)) > 0),
    original_source_document TEXT NOT NULL CHECK(length(trim(original_source_document)) > 0),
    source_identity TEXT NOT NULL CHECK(source_identity = 'mnb_otc'),
    evidence_type TEXT NOT NULL CHECK(evidence_type = 'WEEKLY_OTC_AGGREGATE_NOT_NAV'),
    authorization_reference TEXT NOT NULL CHECK(length(trim(authorization_reference)) > 0),
    source_fingerprint TEXT NOT NULL UNIQUE CHECK(length(source_fingerprint) = 64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE model_mnb_otc_evidence_observation (
    source TEXT NOT NULL CHECK(source = 'mnb_otc'),
    isin TEXT NOT NULL,
    period_start TEXT NOT NULL CHECK(length(period_start) = 10),
    period_end TEXT NOT NULL CHECK(length(period_end) = 10),
    instrument_name TEXT NOT NULL,
    currency TEXT NOT NULL CHECK(currency = 'HUF'),
    nominal_value_huf_thousand TEXT NOT NULL,
    purchase_value_huf_thousand TEXT NOT NULL,
    average_price TEXT NOT NULL,
    minimum_price TEXT NOT NULL,
    maximum_price TEXT NOT NULL,
    transaction_count INTEGER NOT NULL CHECK(transaction_count > 0),
    price_type TEXT NOT NULL CHECK(price_type = 'OTC_WEEKLY_TRANSACTION_AVERAGE'),
    frequency TEXT NOT NULL CHECK(frequency = 'WEEKLY_OTC_AGGREGATE'),
    source_document_hash TEXT NOT NULL REFERENCES model_mnb_otc_evidence_source(source_document_hash),
    observation_fingerprint TEXT NOT NULL UNIQUE CHECK(length(observation_fingerprint) = 64),
    PRIMARY KEY(source, isin, period_start, period_end)
);
CREATE TRIGGER model_source_authority_epoch_immutable_update BEFORE UPDATE ON model_source_authority_epoch
BEGIN SELECT RAISE(ABORT, 'model authority epochs are immutable'); END;
CREATE TRIGGER model_source_authority_epoch_immutable_delete BEFORE DELETE ON model_source_authority_epoch
BEGIN SELECT RAISE(ABORT, 'model authority epochs are immutable'); END;
CREATE TRIGGER model_workbook_admission_immutable_update BEFORE UPDATE ON model_workbook_admission
BEGIN SELECT RAISE(ABORT, 'model workbook admissions are immutable'); END;
CREATE TRIGGER model_workbook_admission_immutable_delete BEFORE DELETE ON model_workbook_admission
BEGIN SELECT RAISE(ABORT, 'model workbook admissions are immutable'); END;
CREATE TRIGGER model_workbook_admission_item_immutable_update BEFORE UPDATE ON model_workbook_admission_item
BEGIN SELECT RAISE(ABORT, 'model workbook admission items are immutable'); END;
CREATE TRIGGER model_workbook_admission_item_immutable_delete BEFORE DELETE ON model_workbook_admission_item
BEGIN SELECT RAISE(ABORT, 'model workbook admission items are immutable'); END;
CREATE TRIGGER model_source_occurrence_typed_extension_immutable_update BEFORE UPDATE ON model_source_occurrence_typed_extension
BEGIN SELECT RAISE(ABORT, 'model typed occurrences are immutable'); END;
CREATE TRIGGER model_source_occurrence_typed_extension_immutable_delete BEFORE DELETE ON model_source_occurrence_typed_extension
BEGIN SELECT RAISE(ABORT, 'model typed occurrences are immutable'); END;
CREATE TRIGGER model_import_batch_immutable_update BEFORE UPDATE ON model_import_batch
BEGIN SELECT RAISE(ABORT, 'model import batches are immutable'); END;
CREATE TRIGGER model_import_batch_immutable_delete BEFORE DELETE ON model_import_batch
BEGIN SELECT RAISE(ABORT, 'model import batches are immutable'); END;
CREATE TRIGGER model_phase1_metric_observation_immutable_update
BEFORE UPDATE ON instrument_metric_observation
WHEN OLD.source_reference LIKE 'MODEL_PHASE1:%'
BEGIN SELECT RAISE(ABORT, 'model Phase 1 metric observations are immutable'); END;
CREATE TRIGGER model_phase1_metric_observation_immutable_delete
BEFORE DELETE ON instrument_metric_observation
WHEN OLD.source_reference LIKE 'MODEL_PHASE1:%'
BEGIN SELECT RAISE(ABORT, 'model Phase 1 metric observations are immutable'); END;
CREATE TRIGGER model_mnb_otc_evidence_source_immutable_update BEFORE UPDATE ON model_mnb_otc_evidence_source
BEGIN SELECT RAISE(ABORT, 'MNB OTC evidence sources are immutable'); END;
CREATE TRIGGER model_mnb_otc_evidence_source_immutable_delete BEFORE DELETE ON model_mnb_otc_evidence_source
BEGIN SELECT RAISE(ABORT, 'MNB OTC evidence sources are immutable'); END;
CREATE TRIGGER model_mnb_otc_evidence_observation_immutable_update BEFORE UPDATE ON model_mnb_otc_evidence_observation
BEGIN SELECT RAISE(ABORT, 'MNB OTC evidence observations are immutable'); END;
CREATE TRIGGER model_mnb_otc_evidence_observation_immutable_delete BEFORE DELETE ON model_mnb_otc_evidence_observation
BEGIN SELECT RAISE(ABORT, 'MNB OTC evidence observations are immutable'); END;
"""

_OBJECT_NAMES = frozenset(
    {
        "model_import_batch",
        "model_mnb_otc_evidence_observation",
        "model_mnb_otc_evidence_observation_immutable_delete",
        "model_mnb_otc_evidence_observation_immutable_update",
        "model_mnb_otc_evidence_source",
        "model_mnb_otc_evidence_source_immutable_delete",
        "model_mnb_otc_evidence_source_immutable_update",
        "model_phase1_metric_observation_immutable_delete",
        "model_phase1_metric_observation_immutable_update",
        "model_source_authority_epoch",
        "model_source_authority_epoch_immutable_delete",
        "model_source_authority_epoch_immutable_update",
        "model_source_occurrence_typed_extension",
        "model_source_occurrence_typed_extension_immutable_delete",
        "model_source_occurrence_typed_extension_immutable_update",
        "model_workbook_admission",
        "model_workbook_admission_immutable_delete",
        "model_workbook_admission_immutable_update",
        "model_workbook_admission_item",
        "model_workbook_admission_item_immutable_delete",
        "model_workbook_admission_item_immutable_update",
        "model_import_batch_immutable_delete",
        "model_import_batch_immutable_update",
    }
)


def normalize_parsed_fields(values: Mapping[str, object]) -> ParsedModelFields:
    """Normalize the eight post-parser fields using legacy numeric-zero semantics.

    This accepts parsed Python values, not raw JSON strings.  A text ``"0"`` is
    therefore rejected rather than silently reinterpreted as either zero or NULL.
    """
    if set(values) != set(RAW_ONLY_FIELDS):
        missing = sorted(set(RAW_ONLY_FIELDS) - set(values))
        extra = sorted(set(values) - set(RAW_ONLY_FIELDS))
        raise ModelPortfolioPhase1Error(
            f"parsed Phase 1 fields differ: missing={missing}, extra={extra}"
        )
    sustainability_value = values["Sustainability"]
    if sustainability_value is not None and (
        not isinstance(sustainability_value, str) or not sustainability_value.strip()
    ):
        raise ModelPortfolioPhase1Error(
            "parsed Sustainability must be a non-empty string or NULL"
        )
    return ParsedModelFields(
        sustainability=sustainability_value,
        ytd=_parsed_number(values["YTD"], "YTD"),
        return_3y=_parsed_number(values["3 Years"], "3 Years"),
        return_5y=_parsed_number(values["5 Years"], "5 Years"),
        sharpe_ratio_3y=_parsed_number(values["3Y Sharpe Ratio"], "3Y Sharpe Ratio"),
        sharpe_ratio_5y=_parsed_number(values["5Y Sharpe Ratio"], "5Y Sharpe Ratio"),
        volatility_3y=_parsed_number(values["3Y Volatility"], "3Y Volatility"),
        information_ratio=_parsed_number(
            values["Information Ratio"], "Information Ratio"
        ),
    )


def install_phase1_schema(connection: sqlite3.Connection) -> None:
    """Atomically install the additive Phase 1 contract on recognized schema v3."""
    validate_schema(connection)
    names = _schema_object_names(connection)
    present = _OBJECT_NAMES & names
    marker = _feature_marker(connection)
    if present and present != _OBJECT_NAMES:
        raise ModelPortfolioPhase1Error("Phase 1 schema is partially installed")
    if not present and marker:
        raise ModelPortfolioPhase1Error("Phase 1 marker exists without schema objects")
    if not present:
        with transaction(connection):
            _execute_schema(connection)
            connection.execute(
                "INSERT INTO schema_feature_contract(feature_id, revision, contract_fingerprint) VALUES(?,?,?)",
                (FEATURE_ID, FEATURE_REVISION, FEATURE_FINGERPRINT),
            )
    validate_phase1_schema(connection)


def validate_phase1_schema(connection: sqlite3.Connection) -> None:
    """Require the exact Phase 1 DDL and feature marker."""
    validate_schema(connection)
    present = _OBJECT_NAMES & _schema_object_names(connection)
    if present != _OBJECT_NAMES:
        raise ModelPortfolioPhase1Error("Phase 1 schema is absent or partial")
    if _feature_marker(connection) != [(FEATURE_REVISION, FEATURE_FINGERPRINT)]:
        raise ModelPortfolioPhase1Error("Phase 1 feature marker is missing or stale")
    if _schema_objects(connection) != _expected_schema_objects():
        raise ModelPortfolioPhase1Error(
            "Phase 1 schema objects are damaged or incompatible"
        )


def source_dataset_fingerprint(connection: sqlite3.Connection) -> str:
    """Fingerprint model source occurrences by portable evidence identities."""
    rows = connection.execute(
        """SELECT sf.sha256, sh.sheet_name, o.source_row_number,
                  s.snapshot_date, p.portfolio_name, i.isin,
                  o.reported_weight, o.observed_product_name,
                  o.observed_currency_code, o.observed_currency_risk,
                  o.observed_asset_class, o.observed_sub_asset_class,
                  o.source_payload_sha256, o.source_semantics_status
           FROM portfolio_holding_source_occurrence AS o
           JOIN portfolio_snapshot AS s ON s.portfolio_snapshot_id=o.portfolio_snapshot_id
           JOIN portfolio AS p ON p.portfolio_id=s.portfolio_id
           JOIN instrument AS i ON i.instrument_id=o.instrument_id
           JOIN source_sheet AS sh ON sh.source_sheet_id=o.source_sheet_id
           JOIN source_file AS sf ON sf.source_file_id=sh.source_file_id
           WHERE p.portfolio_type='MODEL'
           ORDER BY sf.sha256, sh.sheet_name, o.source_row_number,
                    s.snapshot_date, p.portfolio_name, i.isin,
                    o.reported_weight, o.observed_product_name,
                    o.observed_currency_code, o.observed_currency_risk,
                    o.observed_asset_class, o.observed_sub_asset_class,
                    o.source_payload_sha256, o.source_semantics_status"""
    ).fetchall()
    return canonical_fingerprint([tuple(row) for row in rows])


def admit_workbook_normalization(
    connection: sqlite3.Connection, request: WorkbookAdmissionRequest
) -> WorkbookAdmissionResult:
    """Admit typed fields for existing synthetic source occurrences atomically."""
    validate_phase1_schema(connection)
    _validate_request(request)
    current_fingerprint = source_dataset_fingerprint(connection)
    if current_fingerprint != request.expected_source_dataset_fingerprint:
        raise ModelPortfolioPhase1Error("model source dataset fingerprint changed")
    if request.authority.baseline_dataset_fingerprint != current_fingerprint:
        raise ModelPortfolioPhase1Error("authority epoch is bound to another dataset")
    rows = _validated_item_rows(connection, request)
    admission_fingerprint = _admission_fingerprint(request, rows)
    existing = connection.execute(
        "SELECT * FROM model_workbook_admission WHERE admission_id=?",
        (request.admission_id,),
    ).fetchone()
    if existing is not None:
        return _validate_replay(
            connection, request, rows, admission_fingerprint, existing
        )
    date_conflict = connection.execute(
        """SELECT admission_id FROM model_workbook_admission
           WHERE snapshot_date=? AND source_sheet_name=?""",
        (request.snapshot_date, request.source_sheet_name),
    ).fetchone()
    if date_conflict is not None:
        raise ModelPortfolioPhase1Error(
            "snapshot date and sheet already have a different admission"
        )
    try:
        with transaction(connection):
            _insert_or_validate_authority(connection, request.authority)
            connection.execute(
                """INSERT INTO model_workbook_admission(
                       admission_id, epoch_id, contract_version, source_file_sha256,
                       filename, snapshot_date, source_sheet_name, header_signature,
                       expected_source_dataset_fingerprint, parser_version,
                       authorization_reference, admission_fingerprint, item_count,
                       admission_status
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'PHASE1_VALIDATED_TEMPORARY')""",
                (
                    request.admission_id,
                    request.authority.epoch_id,
                    CONTRACT_VERSION,
                    request.source_file_sha256,
                    request.filename,
                    request.snapshot_date,
                    request.source_sheet_name,
                    request.header_signature,
                    request.expected_source_dataset_fingerprint,
                    request.authority.parser_version,
                    request.authorization_reference,
                    admission_fingerprint,
                    len(rows),
                ),
            )
            metric_ids = _extended_metric_ids(connection)
            for item, row in zip(request.items, rows, strict=True):
                connection.execute(
                    """INSERT INTO model_workbook_admission_item VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        request.admission_id,
                        item.stable_source_reference,
                        item.source_occurrence_id,
                        item.source_file_sha256,
                        item.source_sheet_name,
                        item.source_row_number,
                        item.snapshot_date,
                        item.portfolio_name,
                        item.isin,
                        item.source_payload_sha256,
                        item.parsed_fields.fingerprint,
                    ),
                )
                _insert_typed_fields(connection, request, item, metric_ids)
            typed_fingerprint = typed_projection_fingerprint(connection)
            batch_sequence = int(
                connection.execute(
                    "SELECT coalesce(max(batch_sequence), 0) + 1 FROM model_import_batch"
                ).fetchone()[0]
            )
            connection.execute(
                """INSERT INTO model_import_batch VALUES(?,?,?,?,?,?,'PHASE1_NORMALIZATION_ONLY',CURRENT_TIMESTAMP)""",
                (
                    request.batch_id,
                    request.admission_id,
                    batch_sequence,
                    current_fingerprint,
                    current_fingerprint,
                    typed_fingerprint,
                ),
            )
            validate_phase1_contracts(
                connection, expected_epoch_id=request.authority.epoch_id
            )
    except sqlite3.IntegrityError as error:
        raise ModelPortfolioPhase1Error(
            "Phase 1 admission conflicts with installed immutable state"
        ) from error
    return WorkbookAdmissionResult(
        request.admission_id,
        request.batch_id,
        len(rows),
        admission_fingerprint,
        typed_fingerprint,
        False,
    )


def validate_phase1_contracts(
    connection: sqlite3.Connection, *, expected_epoch_id: str | None = None
) -> None:
    """Validate schema, stable bindings, receipts, MNB evidence, and corrections."""
    validate_phase1_schema(connection)
    _validate_installed_shortlist_corrections(connection)
    epochs = connection.execute(
        "SELECT * FROM model_source_authority_epoch ORDER BY epoch_id"
    ).fetchall()
    if (
        expected_epoch_id is not None
        and [row for row in epochs if str(row["epoch_id"]) == expected_epoch_id] == []
    ):
        raise ModelPortfolioPhase1Error("requested Phase 1 authority epoch is absent")
    current_dataset = source_dataset_fingerprint(connection)
    for epoch in epochs:
        for field in (
            "baseline_source_sha256",
            "baseline_dataset_fingerprint",
            "ranking_policy_sha256",
        ):
            _require_sha256(str(epoch[field]), field)
        if (
            int(epoch["contract_version"]) != CONTRACT_VERSION
            or str(epoch["operational_status"]) != NON_OPERATIONAL_STATUS
            or str(epoch["baseline_dataset_fingerprint"]) != current_dataset
            or str(epoch["parser_version"]) != PARSER_VERSION
        ):
            raise ModelPortfolioPhase1Error("Phase 1 authority binding is stale")
    admissions = connection.execute(
        "SELECT * FROM model_workbook_admission ORDER BY admission_id"
    ).fetchall()
    expected_extended_metrics = 0
    for admission in admissions:
        admission_id = str(admission["admission_id"])
        epoch = connection.execute(
            "SELECT * FROM model_source_authority_epoch WHERE epoch_id=?",
            (admission["epoch_id"],),
        ).fetchone()
        if epoch is None or (
            int(admission["contract_version"]) != CONTRACT_VERSION
            or str(admission["admission_status"]) != "PHASE1_VALIDATED_TEMPORARY"
            or str(admission["parser_version"]) != str(epoch["parser_version"])
            or str(admission["expected_source_dataset_fingerprint"]) != current_dataset
        ):
            raise ModelPortfolioPhase1Error("workbook admission binding is stale")
        for field in (
            "source_file_sha256",
            "header_signature",
            "expected_source_dataset_fingerprint",
            "admission_fingerprint",
        ):
            _require_sha256(str(admission[field]), field)
        source_file = connection.execute(
            "SELECT filename FROM source_file WHERE sha256=?",
            (admission["source_file_sha256"],),
        ).fetchall()
        if len(source_file) != 1 or str(source_file[0][0]) != str(
            admission["filename"]
        ):
            raise ModelPortfolioPhase1Error(
                "workbook admission filename does not match source evidence"
            )
        item_rows = connection.execute(
            """SELECT * FROM model_workbook_admission_item
               WHERE admission_id=? ORDER BY stable_source_reference""",
            (admission_id,),
        ).fetchall()
        if int(admission["item_count"]) != len(item_rows):
            raise ModelPortfolioPhase1Error("workbook admission item count mismatch")
        bindings = tuple(_binding_from_stored_row(connection, row) for row in item_rows)
        expected_extended_metrics += sum(
            sum(
                getattr(binding.parsed_fields, field) is not None
                for _code, _name, field in _EXTENDED_METRICS
            )
            for binding in bindings
        )
        expected_rows = _validated_item_rows_from_bindings(connection, bindings)
        request_payload = _stored_admission_payload(connection, admission, bindings)
        if str(admission["admission_fingerprint"]) != canonical_fingerprint(
            {"admission": request_payload, "items": expected_rows}
        ):
            raise ModelPortfolioPhase1Error("workbook admission fingerprint mismatch")
        batch = connection.execute(
            "SELECT * FROM model_import_batch WHERE admission_id=?", (admission_id,)
        ).fetchall()
        if len(batch) != 1 or (
            str(batch[0]["source_dataset_fingerprint_before"]) != current_dataset
            or str(batch[0]["source_dataset_fingerprint_after"]) != current_dataset
            or str(batch[0]["outcome"]) != "PHASE1_NORMALIZATION_ONLY"
        ):
            raise ModelPortfolioPhase1Error("model import batch binding is stale")
    expected_typed = sum(int(row["item_count"]) for row in admissions)
    actual_typed = int(
        connection.execute(
            "SELECT count(*) FROM model_source_occurrence_typed_extension"
        ).fetchone()[0]
    )
    if expected_typed != actual_typed:
        raise ModelPortfolioPhase1Error("typed occurrence coverage is incomplete")
    actual_extended_metrics = int(
        connection.execute(
            """SELECT count(*) FROM instrument_metric_observation
               WHERE source_reference LIKE 'MODEL_PHASE1:%'"""
        ).fetchone()[0]
    )
    if expected_extended_metrics != actual_extended_metrics:
        raise ModelPortfolioPhase1Error(
            "extended provider-reported metric coverage is incomplete"
        )
    batches = connection.execute(
        "SELECT * FROM model_import_batch ORDER BY batch_sequence"
    ).fetchall()
    if [int(row["batch_sequence"]) for row in batches] != list(
        range(1, len(batches) + 1)
    ):
        raise ModelPortfolioPhase1Error("model import batch sequence is not contiguous")
    for batch in batches:
        projection = typed_projection_fingerprint(
            connection, max_batch_sequence=int(batch["batch_sequence"])
        )
        if str(batch["typed_projection_fingerprint"]) != projection:
            raise ModelPortfolioPhase1Error("typed projection fingerprint is stale")
    _validate_mnb_contract(connection)


def typed_projection_fingerprint(
    connection: sqlite3.Connection, *, max_batch_sequence: int | None = None
) -> str:
    query = """SELECT extension.stable_source_reference, extension.parser_version,
                      extension.sustainability,
                      extension.parsed_fields_fingerprint
               FROM model_source_occurrence_typed_extension AS extension"""
    parameters: tuple[int, ...] = ()
    if max_batch_sequence is not None:
        query += """ JOIN model_import_batch AS batch
                       ON batch.admission_id=extension.admission_id
                     WHERE batch.batch_sequence <= ?"""
        parameters = (max_batch_sequence,)
    query += " ORDER BY extension.stable_source_reference"
    rows = connection.execute(query, parameters).fetchall()
    return canonical_fingerprint([tuple(row) for row in rows])


def admit_mnb_otc_evidence(
    connection: sqlite3.Connection,
    observation: MnbOtcObservation,
    binding: MnbEvidenceBinding,
) -> bool:
    """Append one synthetic Phase 1 MNB aggregate, or accept an exact replay."""
    validate_phase1_schema(connection)
    if (
        not binding.portable_evidence_role.strip()
        or not binding.authorization_reference.strip()
    ):
        raise ModelPortfolioPhase1Error(
            "MNB portable role and authorization are required"
        )
    source_fingerprint = _mnb_source_fingerprint(observation, binding)
    observation_fingerprint = _mnb_observation_fingerprint(observation)
    existing = connection.execute(
        """SELECT * FROM model_mnb_otc_evidence_observation
           WHERE source=? AND isin=? AND period_start=? AND period_end=?""",
        (
            observation.source,
            observation.isin,
            observation.period_start.isoformat(),
            observation.period_end.isoformat(),
        ),
    ).fetchone()
    if existing is not None:
        if (
            str(existing["observation_fingerprint"]) != observation_fingerprint
            or str(existing["source_document_hash"]) != observation.source_document_hash
        ):
            raise ModelPortfolioPhase1Error(
                "conflicting MNB OTC evidence for the same reporting period"
            )
        source = connection.execute(
            "SELECT * FROM model_mnb_otc_evidence_source WHERE source_document_hash=?",
            (observation.source_document_hash,),
        ).fetchone()
        if source is None or str(source["source_fingerprint"]) != source_fingerprint:
            raise ModelPortfolioPhase1Error("MNB OTC replay provenance differs")
        _validate_mnb_contract(connection)
        return False
    try:
        with transaction(connection):
            source = connection.execute(
                "SELECT * FROM model_mnb_otc_evidence_source WHERE source_document_hash=?",
                (observation.source_document_hash,),
            ).fetchone()
            if source is None:
                connection.execute(
                    """INSERT INTO model_mnb_otc_evidence_source VALUES(
                           ?,1,?,?,'mnb_otc','WEEKLY_OTC_AGGREGATE_NOT_NAV',?,?,CURRENT_TIMESTAMP
                       )""",
                    (
                        observation.source_document_hash,
                        binding.portable_evidence_role,
                        observation.source_document,
                        binding.authorization_reference,
                        source_fingerprint,
                    ),
                )
            elif str(source["source_fingerprint"]) != source_fingerprint:
                raise ModelPortfolioPhase1Error("MNB OTC source binding conflicts")
            connection.execute(
                """INSERT INTO model_mnb_otc_evidence_observation VALUES(
                       ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                   )""",
                (
                    observation.source,
                    observation.isin,
                    observation.period_start.isoformat(),
                    observation.period_end.isoformat(),
                    observation.instrument_name,
                    observation.currency,
                    decimal_text(observation.nominal_value_huf_thousand),
                    decimal_text(observation.purchase_value_huf_thousand),
                    decimal_text(observation.average_price),
                    decimal_text(observation.minimum_price),
                    decimal_text(observation.maximum_price),
                    observation.transaction_count,
                    observation.price_type,
                    observation.frequency,
                    observation.source_document_hash,
                    observation_fingerprint,
                ),
            )
            _validate_mnb_contract(connection)
    except (sqlite3.IntegrityError, MnbOtcError) as error:
        raise ModelPortfolioPhase1Error("invalid MNB OTC evidence admission") from error
    return True


class AnalyticalModelPortfolioRepository(SchemaV3ModelPortfolioRepository):
    """Explicit opt-in Phase 1 reader; it is never selected by defaults."""

    def __init__(self, database_path: Path, *, authority_epoch_id: str) -> None:
        super().__init__(database_path)
        self.authority_epoch_id = authority_epoch_id

    def _connection(self) -> sqlite3.Connection:
        connection = super()._connection()
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            validate_phase1_contracts(
                connection, expected_epoch_id=self.authority_epoch_id
            )
        except BaseException:
            connection.close()
            raise
        return connection

    def latest_observation_date(self) -> date:
        dates = self.observation_dates()
        if not dates:
            raise ModelPortfolioPhase1Error("no model observation dates are available")
        return dates[-1]

    def load_typed_occurrences(
        self, observation_date: date
    ) -> tuple[TypedModelOccurrence, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT o.portfolio_holding_source_occurrence_id,
                          extension.stable_source_reference, p.portfolio_name,
                          i.isin, o.source_row_number, o.source_semantics_status,
                          extension.sustainability,
                          extension.parsed_fields_fingerprint
                   FROM portfolio_holding_source_occurrence AS o
                   JOIN portfolio_snapshot AS snapshot
                     ON snapshot.portfolio_snapshot_id=o.portfolio_snapshot_id
                   JOIN portfolio AS p ON p.portfolio_id=snapshot.portfolio_id
                   JOIN instrument AS i ON i.instrument_id=o.instrument_id
                   JOIN model_source_occurrence_typed_extension AS extension
                     ON extension.source_occurrence_id=o.portfolio_holding_source_occurrence_id
                   WHERE snapshot.snapshot_date=?
                   ORDER BY p.portfolio_name, i.isin, o.source_row_number""",
                (observation_date.isoformat(),),
            ).fetchall()
            result: list[TypedModelOccurrence] = []
            for row in rows:
                fields = _parsed_fields_from_metrics(
                    connection,
                    source_occurrence_id=int(row[0]),
                    stable_source_reference=str(row[1]),
                    sustainability=row[6],
                )
                if fields.fingerprint != str(row[7]):
                    raise ModelPortfolioPhase1Error(
                        "typed occurrence parsed-field fingerprint is stale"
                    )
                result.append(
                    TypedModelOccurrence(
                        source_occurrence_id=int(row[0]),
                        stable_source_reference=str(row[1]),
                        portfolio_name=str(row[2]),
                        isin=str(row[3]),
                        source_row_number=int(row[4]),
                        source_semantics_status=str(row[5]),
                        fields=fields,
                    )
                )
        return tuple(result)


class AnalyticalMnbOtcRepository:
    """Read-only adapter for the dedicated analytical MNB evidence contract."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def observations(self, isin: str | None = None) -> tuple[MnbOtcObservation, ...]:
        if not self.database_path.is_file():
            raise ModelPortfolioPhase1Error(
                f"analytical database missing: {self.database_path}"
            )
        with sqlite3.connect(
            f"file:{self.database_path.resolve()}?mode=ro", uri=True
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            validate_phase1_contracts(connection)
            query = """SELECT observation.*, source.original_source_document
                       FROM model_mnb_otc_evidence_observation AS observation
                       JOIN model_mnb_otc_evidence_source AS source
                         ON source.source_document_hash=observation.source_document_hash"""
            parameters: tuple[str, ...] = ()
            if isin is not None:
                query += " WHERE observation.isin=?"
                parameters = (isin.strip().upper(),)
            query += " ORDER BY period_start, period_end"
            rows = connection.execute(query, parameters).fetchall()
        return tuple(
            _mnb_from_row(row, str(row["original_source_document"])) for row in rows
        )


def request_from_json(payload: Mapping[str, object]) -> WorkbookAdmissionRequest:
    """Parse an exact Phase 1 request document without permissive defaults."""
    expected = {
        "admission_id",
        "batch_id",
        "authority",
        "filename",
        "source_file_sha256",
        "snapshot_date",
        "source_sheet_name",
        "header_signature",
        "expected_source_dataset_fingerprint",
        "authorization_reference",
        "items",
    }
    if set(payload) != expected:
        raise ModelPortfolioPhase1Error("Phase 1 request fields do not match contract")
    authority_value = payload["authority"]
    items_value = payload["items"]
    if not isinstance(authority_value, Mapping) or not isinstance(items_value, list):
        raise ModelPortfolioPhase1Error("authority and items have invalid types")
    authority = SourceAuthorityEpoch(**_string_mapping(authority_value))
    items: list[ModelOccurrenceBinding] = []
    for value in items_value:
        if not isinstance(value, Mapping):
            raise ModelPortfolioPhase1Error("each Phase 1 item must be an object")
        parsed = value.get("parsed_fields")
        if not isinstance(parsed, Mapping):
            raise ModelPortfolioPhase1Error("each item requires parsed_fields")
        item_values = dict(value)
        item_values["parsed_fields"] = normalize_parsed_fields(parsed)
        items.append(ModelOccurrenceBinding(**item_values))
    return WorkbookAdmissionRequest(
        admission_id=_required_payload_string(payload, "admission_id"),
        batch_id=_required_payload_string(payload, "batch_id"),
        authority=authority,
        filename=_required_payload_string(payload, "filename"),
        source_file_sha256=_required_payload_string(payload, "source_file_sha256"),
        snapshot_date=_required_payload_string(payload, "snapshot_date"),
        source_sheet_name=_required_payload_string(payload, "source_sheet_name"),
        header_signature=_required_payload_string(payload, "header_signature"),
        expected_source_dataset_fingerprint=_required_payload_string(
            payload, "expected_source_dataset_fingerprint"
        ),
        authorization_reference=_required_payload_string(
            payload, "authorization_reference"
        ),
        items=tuple(items),
    )


def _parsed_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise ModelPortfolioPhase1Error(
            f"parsed {field} must be a number or NULL; raw strings are evidence only"
        )
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ModelPortfolioPhase1Error(f"parsed {field} must be finite")
    return None if parsed == 0.0 else parsed


def _validate_request(request: WorkbookAdmissionRequest) -> None:
    for label, value in (
        ("admission_id", request.admission_id),
        ("batch_id", request.batch_id),
        ("filename", request.filename),
        ("source_sheet_name", request.source_sheet_name),
        ("authorization_reference", request.authorization_reference),
        ("epoch_id", request.authority.epoch_id),
        ("parser_version", request.authority.parser_version),
        ("epoch authorization", request.authority.authorization_reference),
    ):
        if not value.strip():
            raise ModelPortfolioPhase1Error(f"{label} is required")
    for label, value in (
        ("source_file_sha256", request.source_file_sha256),
        ("header_signature", request.header_signature),
        (
            "expected_source_dataset_fingerprint",
            request.expected_source_dataset_fingerprint,
        ),
        ("baseline_source_sha256", request.authority.baseline_source_sha256),
        (
            "baseline_dataset_fingerprint",
            request.authority.baseline_dataset_fingerprint,
        ),
        ("ranking_policy_sha256", request.authority.ranking_policy_sha256),
    ):
        _require_sha256(value, label)
    try:
        date.fromisoformat(request.snapshot_date)
    except ValueError as error:
        raise ModelPortfolioPhase1Error("snapshot_date must be ISO format") from error
    if not request.items:
        raise ModelPortfolioPhase1Error("at least one occurrence binding is required")
    if request.authority.parser_version != PARSER_VERSION:
        raise ModelPortfolioPhase1Error("unsupported model parser version")
    for item in request.items:
        if (
            isinstance(item.source_occurrence_id, bool)
            or not isinstance(item.source_occurrence_id, int)
            or item.source_occurrence_id <= 0
            or isinstance(item.source_row_number, bool)
            or not isinstance(item.source_row_number, int)
            or item.source_row_number <= 0
        ):
            raise ModelPortfolioPhase1Error(
                "occurrence and source-row identifiers must be positive integers"
            )
        for label, value in (
            ("item source sheet", item.source_sheet_name),
            ("item portfolio", item.portfolio_name),
            ("item ISIN", item.isin),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ModelPortfolioPhase1Error(f"{label} is required")
        _require_sha256(item.source_file_sha256, "item source file SHA-256")
        _require_sha256(item.source_payload_sha256, "item source payload SHA-256")
        try:
            date.fromisoformat(item.snapshot_date)
        except (TypeError, ValueError) as error:
            raise ModelPortfolioPhase1Error(
                "item snapshot_date must be ISO format"
            ) from error
    if any(
        item.source_file_sha256 != request.source_file_sha256 for item in request.items
    ):
        raise ModelPortfolioPhase1Error("item source hash differs from workbook hash")
    if any(
        item.snapshot_date != request.snapshot_date
        or item.source_sheet_name != request.source_sheet_name
        for item in request.items
    ):
        raise ModelPortfolioPhase1Error("item date or sheet differs from admission")
    stable = [item.stable_source_reference for item in request.items]
    if len(stable) != len(set(stable)):
        raise ModelPortfolioPhase1Error("stable source references are not unique")


def _validated_item_rows(
    connection: sqlite3.Connection, request: WorkbookAdmissionRequest
) -> tuple[tuple[object, ...], ...]:
    rows = _validated_item_rows_from_bindings(connection, request.items)
    source_file = connection.execute(
        "SELECT filename FROM source_file WHERE sha256=?",
        (request.source_file_sha256,),
    ).fetchall()
    if len(source_file) != 1 or str(source_file[0][0]) != request.filename:
        raise ModelPortfolioPhase1Error(
            "workbook filename does not match staged source evidence"
        )
    return rows


def _validated_item_rows_from_bindings(
    connection: sqlite3.Connection, items: Sequence[ModelOccurrenceBinding]
) -> tuple[tuple[object, ...], ...]:
    result: list[tuple[object, ...]] = []
    for item in sorted(items, key=lambda value: value.stable_source_reference):
        row = connection.execute(
            """SELECT sf.sha256, sh.sheet_name, o.source_row_number,
                      snapshot.snapshot_date, p.portfolio_name, i.isin,
                      o.source_payload_sha256, sf.source_type
               FROM portfolio_holding_source_occurrence AS o
               JOIN portfolio_snapshot AS snapshot
                 ON snapshot.portfolio_snapshot_id=o.portfolio_snapshot_id
               JOIN portfolio AS p ON p.portfolio_id=snapshot.portfolio_id
               JOIN instrument AS i ON i.instrument_id=o.instrument_id
               JOIN source_sheet AS sh ON sh.source_sheet_id=o.source_sheet_id
               JOIN source_file AS sf ON sf.source_file_id=sh.source_file_id
               WHERE o.portfolio_holding_source_occurrence_id=?""",
            (item.source_occurrence_id,),
        ).fetchone()
        expected = (
            item.source_file_sha256,
            item.source_sheet_name,
            item.source_row_number,
            item.snapshot_date,
            item.portfolio_name,
            item.isin,
            item.source_payload_sha256,
        )
        if row is None or tuple(row[:7]) != expected or str(row[7]) != "MODEL_XLS":
            raise ModelPortfolioPhase1Error(
                "occurrence binding does not match immutable source evidence"
            )
        result.append(
            (
                item.stable_source_reference,
                item.source_occurrence_id,
                *expected,
                item.parsed_fields.fingerprint,
                *asdict(item.parsed_fields).values(),
            )
        )
    return tuple(result)


def _admission_fingerprint(
    request: WorkbookAdmissionRequest, rows: tuple[tuple[object, ...], ...]
) -> str:
    return canonical_fingerprint(
        {"admission": _request_payload(request), "items": rows}
    )


def _request_payload(request: WorkbookAdmissionRequest) -> dict[str, object]:
    return {
        "admission_id": request.admission_id,
        "authority": asdict(request.authority),
        "authorization_reference": request.authorization_reference,
        "batch_id": request.batch_id,
        "expected_source_dataset_fingerprint": request.expected_source_dataset_fingerprint,
        "filename": request.filename,
        "header_signature": request.header_signature,
        "source_file_sha256": request.source_file_sha256,
        "source_sheet_name": request.source_sheet_name,
        "snapshot_date": request.snapshot_date,
    }


def _stored_admission_payload(
    connection: sqlite3.Connection,
    admission: sqlite3.Row,
    bindings: tuple[ModelOccurrenceBinding, ...],
) -> dict[str, object]:
    del bindings
    epoch = connection.execute(
        "SELECT * FROM model_source_authority_epoch WHERE epoch_id=?",
        (admission["epoch_id"],),
    ).fetchone()
    batch = connection.execute(
        "SELECT batch_id FROM model_import_batch WHERE admission_id=?",
        (admission["admission_id"],),
    ).fetchone()
    if epoch is None or batch is None:
        raise ModelPortfolioPhase1Error("admission authority or batch is missing")
    return {
        "admission_id": str(admission["admission_id"]),
        "authority": {
            "authorization_reference": str(epoch["authorization_reference"]),
            "baseline_dataset_fingerprint": str(epoch["baseline_dataset_fingerprint"]),
            "baseline_source_sha256": str(epoch["baseline_source_sha256"]),
            "epoch_id": str(epoch["epoch_id"]),
            "parser_version": str(epoch["parser_version"]),
            "ranking_policy_sha256": str(epoch["ranking_policy_sha256"]),
        },
        "authorization_reference": str(admission["authorization_reference"]),
        "batch_id": str(batch[0]),
        "expected_source_dataset_fingerprint": str(
            admission["expected_source_dataset_fingerprint"]
        ),
        "filename": str(admission["filename"]),
        "header_signature": str(admission["header_signature"]),
        "source_file_sha256": str(admission["source_file_sha256"]),
        "source_sheet_name": str(admission["source_sheet_name"]),
        "snapshot_date": str(admission["snapshot_date"]),
    }


def _insert_or_validate_authority(
    connection: sqlite3.Connection, authority: SourceAuthorityEpoch
) -> None:
    existing = connection.execute(
        "SELECT * FROM model_source_authority_epoch WHERE epoch_id=?",
        (authority.epoch_id,),
    ).fetchone()
    expected = (
        authority.epoch_id,
        CONTRACT_VERSION,
        NON_OPERATIONAL_STATUS,
        authority.baseline_source_sha256,
        authority.baseline_dataset_fingerprint,
        authority.parser_version,
        authority.ranking_policy_sha256,
        authority.authorization_reference,
    )
    if existing is not None:
        if tuple(existing)[:8] != expected:
            raise ModelPortfolioPhase1Error("authority epoch replay differs")
        return
    connection.execute(
        """INSERT INTO model_source_authority_epoch(
               epoch_id, contract_version, operational_status,
               baseline_source_sha256, baseline_dataset_fingerprint,
               parser_version, ranking_policy_sha256, authorization_reference
           ) VALUES(?,?,?,?,?,?,?,?)""",
        expected,
    )


def _insert_typed_fields(
    connection: sqlite3.Connection,
    request: WorkbookAdmissionRequest,
    item: ModelOccurrenceBinding,
    metric_ids: Mapping[str, int],
) -> None:
    fields = item.parsed_fields
    connection.execute(
        """INSERT INTO model_source_occurrence_typed_extension VALUES(
               ?,?,?,?,?,?
           )""",
        (
            item.source_occurrence_id,
            request.admission_id,
            item.stable_source_reference,
            request.authority.parser_version,
            fields.sustainability,
            fields.fingerprint,
        ),
    )
    occurrence = connection.execute(
        """SELECT o.instrument_id, snapshot.snapshot_date, source.source_file_id
           FROM portfolio_holding_source_occurrence AS o
           JOIN portfolio_snapshot AS snapshot
             ON snapshot.portfolio_snapshot_id=o.portfolio_snapshot_id
           JOIN source_sheet AS sheet ON sheet.source_sheet_id=o.source_sheet_id
           JOIN source_file AS source ON source.source_file_id=sheet.source_file_id
           WHERE o.portfolio_holding_source_occurrence_id=?""",
        (item.source_occurrence_id,),
    ).fetchone()
    if occurrence is None:
        raise ModelPortfolioPhase1Error("typed occurrence source disappeared")
    for code, _name, field in _EXTENDED_METRICS:
        value = getattr(fields, field)
        if value is None:
            continue
        connection.execute(
            """INSERT INTO instrument_metric_observation(
                   instrument_id, metric_id, observation_date, value,
                   provenance_type, source_file_id, source_reference
               ) VALUES(?,?,?,?, 'PROVIDER_REPORTED',?,?)""",
            (
                int(occurrence[0]),
                metric_ids[code],
                str(occurrence[1]),
                value,
                int(occurrence[2]),
                _extended_metric_reference(item.stable_source_reference, code),
            ),
        )


def _extended_metric_ids(connection: sqlite3.Connection) -> dict[str, int]:
    result: dict[str, int] = {}
    for code, name, _field in _EXTENDED_METRICS:
        row = connection.execute(
            "SELECT metric_id, unit FROM metric_definition WHERE metric_code=?",
            (code,),
        ).fetchone()
        if row is None:
            cursor = connection.execute(
                """INSERT INTO metric_definition(metric_code, name, unit, description)
                   VALUES(?,?,'RATIO','Model workbook provider-reported metric')""",
                (code, name),
            )
            if cursor.lastrowid is None:
                raise ModelPortfolioPhase1Error(
                    "SQLite did not return an extended metric id"
                )
            result[code] = int(cursor.lastrowid)
        else:
            if str(row[1]) != "RATIO":
                raise ModelPortfolioPhase1Error(
                    f"existing metric definition {code} has incompatible units"
                )
            result[code] = int(row[0])
    return result


def _extended_metric_reference(stable_source_reference: str, code: str) -> str:
    return f"MODEL_PHASE1:{stable_source_reference}:{code}"


def _parsed_fields_from_metrics(
    connection: sqlite3.Connection,
    *,
    source_occurrence_id: int,
    stable_source_reference: str,
    sustainability: object,
) -> ParsedModelFields:
    if sustainability is not None and not isinstance(sustainability, str):
        raise ModelPortfolioPhase1Error(
            "typed Sustainability has an invalid stored representation"
        )
    rows = connection.execute(
        """SELECT definition.metric_code, observation.value,
                  observation.source_reference,
                  observation.instrument_id=occurrence.instrument_id,
                  observation.observation_date=snapshot.snapshot_date,
                  observation.source_file_id=source.source_file_id,
                  observation.provenance_type
           FROM instrument_metric_observation AS observation
           JOIN metric_definition AS definition
             ON definition.metric_id=observation.metric_id
           JOIN portfolio_holding_source_occurrence AS occurrence
             ON occurrence.portfolio_holding_source_occurrence_id=?
           JOIN portfolio_snapshot AS snapshot
             ON snapshot.portfolio_snapshot_id=occurrence.portfolio_snapshot_id
           JOIN source_sheet AS sheet ON sheet.source_sheet_id=occurrence.source_sheet_id
           JOIN source_file AS source ON source.source_file_id=sheet.source_file_id
           WHERE observation.source_reference LIKE ?
           ORDER BY definition.metric_code""",
        (source_occurrence_id, f"MODEL_PHASE1:{stable_source_reference}:%"),
    ).fetchall()
    values: dict[str, float] = {}
    fields_by_code = {code: field for code, _name, field in _EXTENDED_METRICS}
    for row in rows:
        code = str(row[0])
        if (
            code not in fields_by_code
            or str(row[2]) != _extended_metric_reference(stable_source_reference, code)
            or code in values
            or tuple(int(row[index]) for index in (3, 4, 5)) != (1, 1, 1)
            or str(row[6]) != "PROVIDER_REPORTED"
        ):
            raise ModelPortfolioPhase1Error(
                "extended provider-reported metric binding is invalid"
            )
        values[code] = float(row[1])
    return ParsedModelFields(
        sustainability=sustainability,
        ytd=values.get("YTD"),
        return_3y=values.get("RETURN_3Y"),
        return_5y=values.get("RETURN_5Y"),
        sharpe_ratio_3y=values.get("SHARPE_RATIO_3Y"),
        sharpe_ratio_5y=values.get("SHARPE_RATIO_5Y"),
        volatility_3y=values.get("VOLATILITY_3Y"),
        information_ratio=values.get("INFORMATION_RATIO"),
    )


def _validate_replay(
    connection: sqlite3.Connection,
    request: WorkbookAdmissionRequest,
    rows: tuple[tuple[object, ...], ...],
    admission_fingerprint: str,
    existing: sqlite3.Row,
) -> WorkbookAdmissionResult:
    validate_phase1_contracts(connection, expected_epoch_id=request.authority.epoch_id)
    if str(existing["admission_fingerprint"]) != admission_fingerprint:
        raise ModelPortfolioPhase1Error("workbook admission replay differs")
    stored = connection.execute(
        """SELECT stable_source_reference, source_occurrence_id,
                  source_file_sha256, source_sheet_name, source_row_number,
                  snapshot_date, portfolio_name, isin, source_payload_sha256,
                  parsed_fields_fingerprint
           FROM model_workbook_admission_item WHERE admission_id=?
           ORDER BY stable_source_reference""",
        (request.admission_id,),
    ).fetchall()
    if tuple(tuple(row) for row in stored) != tuple(row[:10] for row in rows):
        raise ModelPortfolioPhase1Error("workbook admission replay items differ")
    batch = connection.execute(
        "SELECT * FROM model_import_batch WHERE admission_id=?",
        (request.admission_id,),
    ).fetchone()
    if batch is None or str(batch["batch_id"]) != request.batch_id:
        raise ModelPortfolioPhase1Error("workbook admission replay batch differs")
    return WorkbookAdmissionResult(
        request.admission_id,
        request.batch_id,
        len(rows),
        admission_fingerprint,
        str(batch["typed_projection_fingerprint"]),
        True,
    )


def _binding_from_stored_row(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> ModelOccurrenceBinding:
    extension = connection.execute(
        "SELECT * FROM model_source_occurrence_typed_extension WHERE source_occurrence_id=?",
        (row["source_occurrence_id"],),
    ).fetchone()
    if extension is None:
        raise ModelPortfolioPhase1Error("typed occurrence is missing")
    fields = _parsed_fields_from_metrics(
        connection,
        source_occurrence_id=int(row["source_occurrence_id"]),
        stable_source_reference=str(row["stable_source_reference"]),
        sustainability=extension["sustainability"],
    )
    if fields.fingerprint != str(
        row["parsed_fields_fingerprint"]
    ) or fields.fingerprint != str(extension["parsed_fields_fingerprint"]):
        raise ModelPortfolioPhase1Error("parsed field fingerprint mismatch")
    binding = ModelOccurrenceBinding(
        source_occurrence_id=int(row["source_occurrence_id"]),
        source_file_sha256=str(row["source_file_sha256"]),
        source_sheet_name=str(row["source_sheet_name"]),
        source_row_number=int(row["source_row_number"]),
        snapshot_date=str(row["snapshot_date"]),
        portfolio_name=str(row["portfolio_name"]),
        isin=str(row["isin"]),
        source_payload_sha256=str(row["source_payload_sha256"]),
        parsed_fields=fields,
    )
    if binding.stable_source_reference != str(row["stable_source_reference"]):
        raise ModelPortfolioPhase1Error("stable source reference mismatch")
    return binding


def _mnb_source_fingerprint(
    observation: MnbOtcObservation, binding: MnbEvidenceBinding
) -> str:
    return canonical_fingerprint(
        {
            "authorization_reference": binding.authorization_reference,
            "contract_version": CONTRACT_VERSION,
            "evidence_type": "WEEKLY_OTC_AGGREGATE_NOT_NAV",
            "original_source_document": observation.source_document,
            "portable_evidence_role": binding.portable_evidence_role,
            "source_document_hash": observation.source_document_hash,
            "source_identity": observation.source,
        }
    )


def _mnb_observation_fingerprint(observation: MnbOtcObservation) -> str:
    return canonical_fingerprint(observation.as_dict())


def _validate_mnb_contract(connection: sqlite3.Connection) -> None:
    sources = connection.execute(
        "SELECT * FROM model_mnb_otc_evidence_source ORDER BY source_document_hash"
    ).fetchall()
    for source in sources:
        binding = MnbEvidenceBinding(
            portable_evidence_role=str(source["portable_evidence_role"]),
            authorization_reference=str(source["authorization_reference"]),
        )
        rows = connection.execute(
            """SELECT * FROM model_mnb_otc_evidence_observation
               WHERE source_document_hash=? ORDER BY period_start, period_end""",
            (source["source_document_hash"],),
        ).fetchall()
        if not rows:
            raise ModelPortfolioPhase1Error("MNB evidence source has no observation")
        for row in rows:
            observation = _mnb_from_row(row, str(source["original_source_document"]))
            if str(source["source_fingerprint"]) != _mnb_source_fingerprint(
                observation, binding
            ):
                raise ModelPortfolioPhase1Error("MNB source fingerprint mismatch")
            if str(row["observation_fingerprint"]) != _mnb_observation_fingerprint(
                observation
            ):
                raise ModelPortfolioPhase1Error("MNB observation fingerprint mismatch")


def _mnb_from_row(
    row: sqlite3.Row, source_document: str | None = None
) -> MnbOtcObservation:
    if source_document is None:
        source_document = str(row["source_document_hash"])
    return MnbOtcObservation(
        source=str(row["source"]),
        isin=str(row["isin"]),
        instrument_name=str(row["instrument_name"]),
        currency=str(row["currency"]),
        period_start=date.fromisoformat(str(row["period_start"])),
        period_end=date.fromisoformat(str(row["period_end"])),
        nominal_value_huf_thousand=parse_decimal(
            str(row["nominal_value_huf_thousand"]), "persisted nominal value"
        ),
        purchase_value_huf_thousand=parse_decimal(
            str(row["purchase_value_huf_thousand"]), "persisted purchase value"
        ),
        average_price=parse_otc_price(
            str(row["average_price"]), "persisted average price"
        ),
        minimum_price=parse_otc_price(
            str(row["minimum_price"]), "persisted minimum price"
        ),
        maximum_price=parse_otc_price(
            str(row["maximum_price"]), "persisted maximum price"
        ),
        transaction_count=parse_transaction_count(str(row["transaction_count"])),
        price_type=str(row["price_type"]),
        frequency=str(row["frequency"]),
        source_document=source_document,
        source_document_hash=str(row["source_document_hash"]),
    )


def _validate_installed_shortlist_corrections(connection: sqlite3.Connection) -> None:
    from portfolio_advisor.database.migrations.shortlist_classification import (
        validate_classification_corrections_if_present,
    )
    from portfolio_advisor.database.migrations.shortlist_zero_null import (
        validate_corrections_if_present,
    )

    validate_corrections_if_present(connection)
    validate_classification_corrections_if_present(connection)


def _feature_marker(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT revision, contract_fingerprint FROM schema_feature_contract WHERE feature_id=?",
            (FEATURE_ID,),
        ).fetchall()
    ]


def _schema_object_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view','trigger','index')"
        )
    }


def _schema_objects(connection: sqlite3.Connection) -> tuple[tuple[str, ...], ...]:
    placeholders = ",".join("?" for _ in _OBJECT_NAMES)
    return tuple(
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            " ".join(str(row[3]).split()),
        )
        for row in connection.execute(
            f"""SELECT type, name, tbl_name, sql FROM sqlite_master
                 WHERE sql IS NOT NULL AND name IN ({placeholders})
                 ORDER BY type, name""",
            tuple(sorted(_OBJECT_NAMES)),
        )
    )


def _expected_schema_objects() -> tuple[tuple[str, ...], ...]:
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE schema_feature_contract(feature_id TEXT PRIMARY KEY, revision INTEGER, contract_fingerprint TEXT)"
        )
        connection.execute(
            "CREATE TABLE portfolio_holding_source_occurrence(portfolio_holding_source_occurrence_id INTEGER PRIMARY KEY)"
        )
        connection.execute(
            """CREATE TABLE instrument_metric_observation(
                   instrument_metric_observation_id INTEGER PRIMARY KEY,
                   source_reference TEXT NOT NULL)"""
        )
        _execute_schema(connection)
        return _schema_objects(connection)


def _execute_schema(connection: sqlite3.Connection) -> None:
    statement = ""
    for line in _SCHEMA_SQL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ModelPortfolioPhase1Error("Phase 1 schema SQL is incomplete")


def _require_sha256(value: str, label: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ModelPortfolioPhase1Error(f"{label} must be lowercase SHA-256")


def _string_mapping(value: Mapping[object, object]) -> dict[str, str]:
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise ModelPortfolioPhase1Error("authority fields must be strings")
    return {str(key): str(item) for key, item in value.items()}


def _required_payload_string(payload: Mapping[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise ModelPortfolioPhase1Error(f"{key} must be a string")
    return value
