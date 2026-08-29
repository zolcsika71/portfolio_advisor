"""Read-only legacy model-portfolio to temporary schema-v3 dry-run support.

This is deliberately not a cutover path.  It accepts only an explicit legacy
source and a non-retained, absent destination, and leaves source databases and
workbooks untouched.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from portfolio_advisor.audit.milestone_4 import audit_workbooks
from portfolio_advisor.database.migrations.validation import validate_integrity
from portfolio_advisor.database.repository import (
    HoldingObservation,
    ModelPortfolioRepository,
    RepositoryError,
)
from portfolio_advisor.database.schema.v3 import (
    connect,
    initialize_schema,
    insert_instrument,
    transaction,
)
from portfolio_advisor.metrics.portfolio import calculate_all_portfolio_metrics
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.ranking import rank_portfolios


class ModelPortfolioMigrationError(RuntimeError):
    """A dry run cannot preserve or reconcile retained legacy evidence."""


class CutoverNotAuthorized(RuntimeError):
    """Production migration and cutover are outside this pre-Milestone 7 work."""


@dataclass(frozen=True, slots=True)
class ReconciledSourceRow:
    """One unambiguous legacy row paired with its retained workbook occurrence."""

    observation_date: date
    holding: HoldingObservation
    source: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DryRunResult:
    """Deterministic result summary for a completed temporary migration."""

    source_fingerprints: dict[str, str]
    destination_fingerprint: str
    counts: dict[str, int]
    duplicate_occurrences: dict[str, Any]
    equivalence_by_date: dict[str, dict[str, Any]]
    maximum_numeric_delta: float
    blockers: tuple[str, ...]


_METRICS = (
    ("RETURN_1Y", "Return 1 year", "RATIO", "return_1y"),
    ("SHARPE_RATIO_1Y", "Sharpe ratio 1 year", "RATIO", "sharpe_ratio_1y"),
    ("VOLATILITY_1Y", "Volatility 1 year", "RATIO", "volatility_1y"),
    ("DOWNSIDE_RISK", "Downside risk", "RATIO", "downside_risk"),
    ("MAXIMUM_DRAWDOWN", "Maximum drawdown", "RATIO", "maximum_drawdown"),
)


def execute_model_portfolio_cutover(*_args: object, **_kwargs: object) -> None:
    """Reject every retained-data migration or production cutover attempt."""
    raise CutoverNotAuthorized("model-portfolio schema-v3 cutover is not authorized")


def reconcile_legacy_to_workbook(
    legacy_path: Path, workbook_directory: Path,
) -> tuple[ReconciledSourceRow, ...]:
    """Map every legacy holding to exactly one valid model-sheet source row."""
    repository = ModelPortfolioRepository(legacy_path)
    audit = audit_workbooks(workbook_directory)
    source_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for sheet in audit["files"]:
        if sheet["source_type"] != "MODEL_XLS" or sheet["status"] != "AUDITED":
            continue
        if sheet["snapshot_date"] is None:
            raise ModelPortfolioMigrationError(f"model workbook has no snapshot date: {sheet['file']}")
        source_date = date.fromisoformat(str(sheet["snapshot_date"]))
        source_by_date[source_date].extend(
            {**record, "file_sha256": record.get("file_sha256", sheet.get("file_sha256"))}
            for record in sheet["identity_records"]
        )

    reconciled: list[ReconciledSourceRow] = []
    try:
        dates = repository.observation_dates()
        for observation_date in dates:
            source_rows = source_by_date.get(observation_date, [])
            legacy_rows = repository.load_holdings(observation_date)
            buckets: dict[tuple[str, str, str, float | None], list[dict[str, Any]]] = defaultdict(list)
            for row in source_rows:
                if row["isin"] is None:
                    continue
                buckets[_source_key(row)].append(row)
            for values in buckets.values():
                values.sort(key=lambda item: (str(item["file"]), str(item["sheet"]), int(item["source_row"])))
            for holding in legacy_rows:
                key = _holding_key(holding)
                matches = buckets.get(key, [])
                if len(matches) != 1:
                    raise ModelPortfolioMigrationError(
                        f"legacy row is not unambiguously reconciled for {observation_date.isoformat()} {key!r}: {len(matches)} match(es)"
                    )
                reconciled.append(ReconciledSourceRow(observation_date, holding, matches.pop()))
            leftover = [row for rows in buckets.values() for row in rows]
            if leftover:
                raise ModelPortfolioMigrationError(
                    f"workbook source rows lack exactly one legacy counterpart for {observation_date.isoformat()}: {len(leftover)}"
                )
    except RepositoryError as error:
        raise ModelPortfolioMigrationError(str(error)) from error
    return tuple(reconciled)


def dry_run_model_portfolio_to_v3(
    *, legacy_path: Path, workbook_directory: Path, destination_path: Path, rules_path: Path,
) -> DryRunResult:
    """Create and validate a disposable v3 database, never a retained database."""
    _validate_destination(legacy_path, destination_path)
    source_fingerprints = _source_fingerprints(legacy_path, workbook_directory)
    rows = reconcile_legacy_to_workbook(legacy_path, workbook_directory)
    connection = connect(destination_path)
    try:
        initialize_schema(connection)
        _populate(connection, rows)
        validate_integrity(connection)
    except BaseException:
        connection.close()
        if destination_path.exists():
            destination_path.unlink()
        raise
    connection.close()
    if source_fingerprints != _source_fingerprints(legacy_path, workbook_directory):
        raise ModelPortfolioMigrationError("retained source fingerprint changed during dry run")

    adapter = SchemaV3ModelPortfolioRepository(destination_path)
    equivalence = equivalence_report(ModelPortfolioRepository(legacy_path), adapter, rules_path)
    duplicate = _duplicate_occurrence_report(destination_path)
    counts = _counts(destination_path)
    blockers = ("IE00B7KFL990 duplicate semantics remain UNRESOLVED_DUPLICATE_SEMANTICS",)
    return DryRunResult(
        source_fingerprints=source_fingerprints,
        destination_fingerprint=_destination_content_fingerprint(destination_path),
        counts=counts,
        duplicate_occurrences=duplicate,
        equivalence_by_date=equivalence,
        maximum_numeric_delta=max((float(item["maximum_numeric_delta"]) for item in equivalence.values()), default=0.0),
        blockers=blockers,
    )


def _populate(connection: sqlite3.Connection, rows: tuple[ReconciledSourceRow, ...]) -> None:
    """Insert only source facts; analytical holdings and cash are intentionally absent."""
    source_files: dict[str, int] = {}
    source_sheets: dict[tuple[int, str], int] = {}
    instruments: dict[str, int] = {}
    portfolios: dict[str, int] = {}
    snapshots: dict[tuple[str, str], int] = {}
    aliases: set[tuple[str, str]] = set()
    with transaction(connection):
        for code, name, unit, _ in _METRICS:
            connection.execute(
                "INSERT INTO metric_definition (metric_code, name, unit, description) VALUES (?, ?, ?, ?)",
                (code, name, unit, "Legacy model-portfolio workbook reported observation"),
            )
        metric_ids = {str(row[1]): int(row[0]) for row in connection.execute("SELECT metric_id, metric_code FROM metric_definition")}
        for item in rows:
            source = item.source
            source_file_id = source_files.get(str(source["file"]))
            if source_file_id is None:
                cursor = connection.execute(
                    "INSERT INTO source_file (filename, sha256, source_type, source_date) VALUES (?, ?, 'MODEL_XLS', ?)",
                    (source["file"], source["file_sha256"], item.observation_date.isoformat()),
                )
                source_file_id = _last_insert_id(cursor)
                source_files[str(source["file"])] = source_file_id
            sheet_key = (source_file_id, str(source["sheet"]))
            source_sheet_id = source_sheets.get(sheet_key)
            if source_sheet_id is None:
                cursor = connection.execute("INSERT INTO source_sheet (source_file_id, sheet_name) VALUES (?, ?)", sheet_key)
                source_sheet_id = _last_insert_id(cursor)
                source_sheets[sheet_key] = source_sheet_id
            isin = str(source["isin"])
            instrument_id = instruments.get(isin)
            if instrument_id is None:
                # A stable canonical name is not available where history conflicts.
                instrument_id = insert_instrument(connection, isin, f"UNRESOLVED CANONICAL NAME: {isin}")
                instruments[isin] = instrument_id
            normalized_name = str(source["normalized_product_name"])
            alias_key = ("MODEL_XLS", normalized_name)
            if alias_key not in aliases:
                connection.execute(
                    """INSERT INTO instrument_alias
                       (instrument_id, source_file_id, source_type, source_name, normalized_source_name, mapping_status, resolution_evidence)
                       VALUES (?, ?, 'MODEL_XLS', ?, ?, 'EXPLICIT_ISIN_VALID', 'source-supplied valid ISIN')""",
                    (instrument_id, source_file_id, source["product_name"], normalized_name),
                )
                aliases.add(alias_key)
            portfolio_id = portfolios.get(item.holding.portfolio_name)
            if portfolio_id is None:
                cursor = connection.execute(
                    "INSERT INTO portfolio (portfolio_name, portfolio_type) VALUES (?, 'MODEL')",
                    (item.holding.portfolio_name,),
                )
                portfolio_id = _last_insert_id(cursor)
                portfolios[item.holding.portfolio_name] = portfolio_id
            snapshot_key = (item.holding.portfolio_name, item.observation_date.isoformat())
            snapshot_id = snapshots.get(snapshot_key)
            if snapshot_id is None:
                cursor = connection.execute(
                    "INSERT INTO portfolio_snapshot (portfolio_id, snapshot_date, source_sheet_id) VALUES (?, ?, ?)",
                    (portfolio_id, item.observation_date.isoformat(), source_sheet_id),
                )
                snapshot_id = _last_insert_id(cursor)
                snapshots[snapshot_key] = snapshot_id
            payload = json.dumps(source["source_values"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            semantics = _semantics(source)
            cursor = connection.execute(
                """INSERT INTO portfolio_holding_source_occurrence (
                       portfolio_snapshot_id, instrument_id, source_sheet_id, source_row_number,
                       reported_weight, observed_product_name, observed_currency_code, observed_currency_risk,
                       observed_asset_class, observed_sub_asset_class, source_payload_json, source_payload_sha256,
                       source_semantics_status
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id, instrument_id, source_sheet_id, int(source["source_row"]), item.holding.allocation,
                    source["product_name"], item.holding.currency, item.holding.currency_risk, item.holding.asset_class,
                    source["sub_asset_class"], payload, hashlib.sha256(payload.encode()).hexdigest(), semantics,
                ),
            )
            occurrence_id = _last_insert_id(cursor)
            for code, _name, _unit, field in _METRICS:
                value = getattr(item.holding, field)
                if value is not None:
                    connection.execute(
                        """INSERT INTO instrument_metric_observation
                           (instrument_id, metric_id, observation_date, value, provenance_type, source_file_id, source_reference)
                           VALUES (?, ?, ?, ?, 'PROVIDER_REPORTED', ?, ?)""",
                        (instrument_id, metric_ids[code], item.observation_date.isoformat(), value, source_file_id, _metric_reference(occurrence_id, code)),
                    )


class SchemaV3ModelPortfolioRepository:
    """Read raw v3 source occurrences through the legacy holding contract."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connection(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise ModelPortfolioMigrationError(f"schema-v3 database missing: {self.database_path}")
        return sqlite3.connect(f"file:{self.database_path.resolve()}?mode=ro", uri=True)

    def observation_dates(self) -> tuple[date, ...]:
        with self._connection() as connection:
            values = connection.execute("SELECT DISTINCT snapshot_date FROM portfolio_snapshot ORDER BY snapshot_date").fetchall()
        return tuple(date.fromisoformat(str(row[0])) for row in values)

    def load_holdings(self, observation_date: date) -> list[HoldingObservation]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT o.portfolio_holding_source_occurrence_id, p.portfolio_name, o.observed_product_name, i.isin,
                           o.reported_weight, o.observed_currency_code, o.observed_currency_risk, o.observed_asset_class
                    FROM portfolio_holding_source_occurrence o
                    JOIN portfolio_snapshot s ON s.portfolio_snapshot_id=o.portfolio_snapshot_id
                    JOIN portfolio p ON p.portfolio_id=s.portfolio_id
                    JOIN instrument i ON i.instrument_id=o.instrument_id
                    WHERE s.snapshot_date=?
                    ORDER BY p.portfolio_name, i.isin, o.observed_product_name, o.portfolio_holding_source_occurrence_id""",
                (observation_date.isoformat(),),
            ).fetchall()
            references = [
                _metric_reference(int(row[0]), code)
                for row in rows
                for code, _name, _unit, _field in _METRICS
            ]
            metric_values: dict[tuple[int, str], float] = {}
            for start in range(0, len(references), 500):
                batch = references[start:start + 500]
                placeholders = ", ".join("?" for _ in batch)
                for reference, code, value in connection.execute(
                    f"""SELECT m.source_reference, d.metric_code, m.value
                        FROM instrument_metric_observation m
                        JOIN metric_definition d ON d.metric_id=m.metric_id
                        WHERE m.source_reference IN ({placeholders})""",
                    batch,
                ):
                    occurrence_id = int(str(reference).split(":", 2)[1])
                    metric_values[(occurrence_id, str(code))] = float(value)
        return [
            HoldingObservation(
                str(row[1]), row[2], row[3], row[4], row[5], row[6],
                metric_values.get((int(row[0]), "RETURN_1Y")),
                metric_values.get((int(row[0]), "SHARPE_RATIO_1Y")),
                metric_values.get((int(row[0]), "VOLATILITY_1Y")),
                metric_values.get((int(row[0]), "DOWNSIDE_RISK")),
                metric_values.get((int(row[0]), "MAXIMUM_DRAWDOWN")), row[7],
            )
            for row in rows
        ]


def equivalence_report(
    legacy: ModelPortfolioRepository, v3: SchemaV3ModelPortfolioRepository, rules_path: Path,
) -> dict[str, dict[str, Any]]:
    """Compare existing calculation and ranking services without reimplementing them."""
    rules = load_ranking_rules(rules_path)
    if legacy.observation_dates() != v3.observation_dates():
        raise ModelPortfolioMigrationError("legacy and schema-v3 observation dates differ")
    reports: dict[str, dict[str, Any]] = {}
    for observation_date in legacy.observation_dates():
        old_rows = legacy.load_holdings(observation_date)
        new_rows = v3.load_holdings(observation_date)
        old_metrics = calculate_all_portfolio_metrics(old_rows)
        new_metrics = calculate_all_portfolio_metrics(new_rows)
        old_ranking, old_warnings = rank_portfolios(old_metrics, rules)
        new_ranking, new_warnings = rank_portfolios(new_metrics, rules)
        old_payload = {"metrics": [asdict(item) for item in old_metrics], "ranking": [asdict(item) for item in old_ranking], "warnings": list(old_warnings)}
        new_payload = {"metrics": [asdict(item) for item in new_metrics], "ranking": [asdict(item) for item in new_ranking], "warnings": list(new_warnings)}
        differences: list[dict[str, Any]] = []
        _compare_exact(old_payload, new_payload, "", differences)
        reports[observation_date.isoformat()] = {
            "candidate_universe_identical": sorted({row.portfolio_name for row in old_rows}) == sorted({row.portfolio_name for row in new_rows}),
            "source_occurrence_count": {"legacy": len(old_rows), "schema_v3": len(new_rows)},
            "allocation_totals_identical": _allocation_totals(old_rows) == _allocation_totals(new_rows),
            "exact": not differences,
            "differences": differences,
            "maximum_numeric_delta": max((float(item.get("absolute_delta", 0.0)) for item in differences), default=0.0),
            "rank_order": [item.metrics.portfolio_name for item in old_ranking if item.rank is not None],
            "selected_winner": next((item.metrics.portfolio_name for item in old_ranking if item.rank == 1), None),
        }
    return reports


def _compare_exact(left: Any, right: Any, path: str, differences: list[dict[str, Any]]) -> None:
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        if left != right:
            differences.append({"field": path, "legacy": left, "schema_v3": right, "absolute_delta": abs(float(left) - float(right)), "cause": "IEEE_754_OR_SERIALIZATION_DIFFERENCE"})
        return
    if type(left) is not type(right):
        differences.append({"field": path, "legacy": left, "schema_v3": right, "cause": "TYPE_OR_MISSING_VALUE_DIFFERENCE"})
    elif isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                differences.append({"field": f"{path}.{key}", "legacy": left.get(key), "schema_v3": right.get(key), "cause": "MISSING_FIELD"})
            else:
                _compare_exact(left[key], right[key], f"{path}.{key}", differences)
    elif isinstance(left, list):
        if len(left) != len(right):
            differences.append({"field": path, "legacy": len(left), "schema_v3": len(right), "cause": "LIST_LENGTH"})
        for index, (a, b) in enumerate(zip(left, right, strict=False)):
            _compare_exact(a, b, f"{path}[{index}]", differences)
    elif left != right:
        differences.append({"field": path, "legacy": left, "schema_v3": right, "cause": "EXACT_VALUE_DIFFERENCE"})


def _source_key(source: dict[str, Any]) -> tuple[str, str, str, float | None]:
    return (str(source["portfolio_name"]), str(source["product_name"]), str(source["isin"]), _float_or_none(source["allocation"]))


def _holding_key(holding: HoldingObservation) -> tuple[str, str, str, float | None]:
    return (holding.portfolio_name, str(holding.product), str(holding.isin), holding.allocation)


def _float_or_none(value: object) -> float | None:
    return None if value is None or str(value).strip() == "" else float(str(value))


def _semantics(source: dict[str, Any]) -> str:
    unresolved_rows = {
        ("PB Konzervatív USD", 33), ("PB Konzervatív USD", 35),
        ("PB Kiegyensúlyozott USD", 87), ("PB Kiegyensúlyozott USD", 91),
        ("PB Dinamikus USD", 145), ("PB Dinamikus USD", 152),
    }
    if (
        str(source["isin"]) == "IE00B7KFL990"
        and str(source["snapshot_date"]) == "2024-09-17"
        and (str(source["portfolio_name"]), int(source["source_row"])) in unresolved_rows
    ):
        return "UNRESOLVED_DUPLICATE_SEMANTICS"
    return "SOURCE_REPORTED"


def _metric_reference(occurrence_id: int, code: str) -> str:
    return f"OCCURRENCE:{occurrence_id}:{code}"


def _last_insert_id(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise ModelPortfolioMigrationError("SQLite did not return an inserted row id")
    return int(cursor.lastrowid)


def _allocation_totals(rows: list[HoldingObservation]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        totals[row.portfolio_name] += row.allocation or 0.0
    return dict(sorted(totals.items()))


def _validate_destination(legacy_path: Path, destination_path: Path) -> None:
    source = legacy_path.resolve()
    destination = destination_path.resolve()
    if source == destination or destination.name == "portfolio_advisor.sqlite":
        raise ModelPortfolioMigrationError("destination is reserved for retained or production data")
    if destination.exists():
        raise ModelPortfolioMigrationError("dry-run destination must not already exist")
    if source.parent.name == "database" and source.parent in destination.parents:
        raise ModelPortfolioMigrationError("destination must not be under the retained database directory")
    destination.parent.mkdir(parents=True, exist_ok=True)


def _source_fingerprints(legacy_path: Path, workbook_directory: Path) -> dict[str, str]:
    values = {str(legacy_path.resolve()): _sha256(legacy_path)}
    for path in sorted(workbook_directory.rglob("*")):
        if path.is_file() and not path.is_symlink() and path.suffix.casefold() in {".xls", ".xlsx", ".xlsm", ".xlsb"}:
            values[str(path.resolve())] = _sha256(path)
    return values


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        return {table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in (
            "source_file", "source_sheet", "instrument", "instrument_alias", "portfolio", "portfolio_snapshot",
            "portfolio_holding_source_occurrence", "portfolio_holding", "instrument_metric_observation",
        )}


def _duplicate_occurrence_report(path: Path) -> dict[str, Any]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """SELECT p.portfolio_name, s.snapshot_date, o.source_row_number, o.reported_weight, o.source_semantics_status
               FROM portfolio_holding_source_occurrence o JOIN instrument i ON i.instrument_id=o.instrument_id
               JOIN portfolio_snapshot s ON s.portfolio_snapshot_id=o.portfolio_snapshot_id
               JOIN portfolio p ON p.portfolio_id=s.portfolio_id
               WHERE i.isin='IE00B7KFL990' AND s.snapshot_date='2024-09-17'
                 AND o.source_semantics_status='UNRESOLVED_DUPLICATE_SEMANTICS'
               ORDER BY p.portfolio_name, o.source_row_number"""
        ).fetchall()
    return {"count": len(rows), "rows": [dict(zip(("portfolio_name", "snapshot_date", "source_row", "allocation", "semantic_status"), row, strict=True)) for row in rows]}


def _destination_content_fingerprint(path: Path) -> str:
    """Hash stable migrated facts, intentionally excluding SQLite timestamps/pages."""
    tables = (
        "schema_version", "source_file", "source_sheet", "instrument", "instrument_alias", "portfolio",
        "portfolio_snapshot", "portfolio_holding_source_occurrence", "metric_definition",
        "instrument_metric_observation", "portfolio_metric_observation",
    )
    payload: dict[str, list[tuple[object, ...]]] = {}
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        for table in tables:
            columns = [
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
                if not str(row[1]).endswith("_at")
            ]
            quoted = ", ".join(f'"{column}"' for column in columns)
            payload[table] = [tuple(row) for row in connection.execute(f"SELECT {quoted} FROM {table} ORDER BY rowid")]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()
