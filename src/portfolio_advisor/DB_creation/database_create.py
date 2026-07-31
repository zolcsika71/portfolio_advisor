"""Create and populate the portfolio SQLite database.

The command-line entry point passes files from ``model_portfolios_shortlist_xls``
to: func:`import_file` and uses ``db/shortlist.sqlite` as the destination.
The importer deliberately receives the destination path from its caller, so
the existing ``model_portfolio.sqlite`` database is never opened or changed by
the default shortlist workflow.

The database schema and import behavior remain consistent with the original
model-portfolio importer: the same field names are used, the date is derived
from each filename in ``YYYY/MM/DD`` format, and files whose date is already
present are skipped.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Final

from .excel_processing import add_date_field, prepare_rows, read_target_worksheet
from .text_normalization import normalized_key

DEFAULT_INPUT_DIR: Final = Path(
    "/Users/zoltanka/Documents/Prog/Python/portfolio_advisor/data/xls/import"
)
DEFAULT_PROCESSED_DIR: Final = Path(
    "/Users/zoltanka/Documents/Prog/Python/portfolio_advisor/data/xls/processed"
)
DEFAULT_DATABASE_PATH: Final = Path(
    "/Users/zoltanka/Documents/Prog/Python/portfolio_advisor/database/model_portfolio.sqlite"
)

# Matches an eight-digit date immediately before the file extension.
# Example: ``portfolio_20250726.xlsx`` -> ``20250726``.
DATE_PATTERN: Final = re.compile(r"(\d{8})(?=\.[^.]+$)")


# Worksheet names can differ by language or formatting. These normalized
# names are mapped to stable, application-specific SQLite table names.
# noinspection SpellCheckingInspection
TABLE_NAME_OVERRIDES: Final = {
    "modell portfóliók": "model_portfolios",
    "model portfolios": "model_portfolios",
}


# Column names expected in the source worksheet.
WORKSHEET_COLUMNS: Final = (
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


# Database rows include the source worksheet columns plus the import date.
MODEL_PORTFOLIOS_COLUMNS: Final = ("Date", *WORKSHEET_COLUMNS)


# Defines the expected schema for each supported database table.
EXPECTED_TABLE_COLUMNS: Final = {
    "model_portfolios": MODEL_PORTFOLIOS_COLUMNS,
}


# Columns listed here are textual. All other supported columns are numeric.
TEXT_COLUMNS: Final = {
    "Date",
    "Portfolio Name",
    "Product",
    "ISIN",
    "Asset Class",
    "Sub-Asset Class",
    "Product Type",
    "Currency",
    "Currency Risk",
    "Sustainability",
}


class DatabaseError(Exception):
    """Represent a database-layer failure without exposing SQLite internals."""


class DatabaseSession(AbstractContextManager[sqlite3.Connection]):
    """Manage a SQLite connection and its commit/rollback lifecycle."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        """Open the database connection when entering the context manager."""
        try:
            connection = sqlite3.connect(self.database_path)
        except sqlite3.Error as error:
            raise DatabaseError(
                f"Could not open database: {self.database_path}"
            ) from error

        self.connection = connection
        return connection

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit successful work or roll back the transaction after an error."""
        assert self.connection is not None

        try:
            if error_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        except sqlite3.Error as sqlite_error:
            raise DatabaseError(
                "Could not complete database transaction"
            ) from sqlite_error
        finally:
            # Always close the connection, regardless of transaction outcome.
            self.connection.close()

        # Returning None allows any original exception to propagate normally.
        return None


def extract_date(file_path: Path) -> str:
    """Extract a filename date and return it in ``YYYY/MM/DD`` format.

    The source filename must end with an eight-digit date immediately before
    its extension, such as ``portfolio_20250726.xlsx``.
    """
    match = DATE_PATTERN.search(file_path.name)

    if match is None:
        raise ValueError(
            f"Filename does not end in an eight-digit date: {file_path.name}"
        )

    parsed_date = datetime.strptime(match.group(1), "%Y%m%d")  # noqa: DTZ007
    return parsed_date.strftime("%Y/%m/%d")


def normalize_table_name(sheet_name: str) -> str:
    """Convert a supported worksheet name into its SQLite table name."""
    try:
        return TABLE_NAME_OVERRIDES[normalized_key(sheet_name)]
    except KeyError as error:
        raise ValueError(
            f"No table mapping is configured for worksheet: {sheet_name!r}"
        ) from error


def _quote_identifier(identifier: str) -> str:
    """Safely quote a SQLite table or column identifier."""
    # Escaping embedded quotes prevents malformed SQL identifiers.
    return '"' + identifier.replace('"', '""') + '"'


def _sql_type(column_name: str) -> str:
    """Return the SQLite type required for a database column."""
    return "TEXT" if column_name in TEXT_COLUMNS else "REAL"


def table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[str]:
    """Return the columns currently defined for a SQLite table, in order."""
    rows = connection.execute(
        f"PRAGMA table_info({_quote_identifier(table_name)})"
    ).fetchall()

    # PRAGMA table_info returns the column name at index 1.
    return [row[1] for row in rows]


def ensure_data_table(
    connection: sqlite3.Connection,
    table_name: str,
    columns: tuple[str, ...],
) -> None:
    """Create a table or reject an incompatible existing schema.

    Existing tables are not altered automatically. This prevents an import
    from silently writing data into an unexpected schema.
    """
    if existing_columns := table_columns(connection, table_name):
        if existing_columns != list(columns):
            raise ValueError(
                f"Existing table {table_name!r} has incompatible columns. "
                "Run with --rebuild."
            )
        return

    column_definitions = [
        f"{_quote_identifier(column)} {_sql_type(column)}"
        for column in columns
    ]
    connection.execute(
        f"CREATE TABLE {_quote_identifier(table_name)} "
        f"({', '.join(column_definitions)})"
    )


def date_exists(
    connection: sqlite3.Connection,
    import_date: str,
) -> bool:
    """Return whether portfolio data already exists for the given date."""
    # If the table has not yet been created, no date can have been imported.
    if not table_columns(connection, "model_portfolios"):
        return False

    # The table is created dynamically by ensure_data_table.
    # noinspection SqlResolve
    result = connection.execute(
        'SELECT 1 FROM model_portfolios WHERE "Date" = ? LIMIT 1',
        (import_date,),
    ).fetchone()
    return result is not None


def import_file(
    file_path: Path,
    database_path: Path,
) -> bool:
    """Import one shortlist ``.xls`` workbook atomically into SQLite.

    Returns:
        ``True`` if rows were imported.
        ``False`` if the workbook date was already present in the database.

    Raises:
        ValueError: If the source file is not an ``.xls`` workbook or its
            filename does not contain a valid date.
    """
    # Resolve paths once so status messages and errors show unambiguous paths.
    file_path = file_path.expanduser().resolve()
    database_path = database_path.expanduser().resolve()

    if not file_path.is_file():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    # The shortlist input directory is defined as an .xls-only source. Fail
    # before opening or creating the database if a different file is passed.
    if file_path.suffix.casefold() != ".xls":
        raise ValueError(
            f"Unsupported input format: {file_path.name}. "
            "Expected an .xls workbook."
        )

    import_date = extract_date(file_path)

    # SQLite can create the database file, but its parent directory must exist.
    database_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with DatabaseSession(database_path) as connection:
            # This date-level check makes the import idempotent and prevents
            # duplicating all rows from a workbook that was already imported.
            if date_exists(connection, import_date):
                print(
                    f"Skipped: Date {import_date} already exists "
                    f"in {database_path}"
                )
                return False

            worksheets = read_target_worksheet(file_path)
            row_count = 0

            for sheet_name, worksheet in worksheets.items():
                table_name = normalize_table_name(sheet_name)
                columns = EXPECTED_TABLE_COLUMNS[table_name]

                # Normalize worksheet data before passing it to the database
                # layer so insertion always receives the expected columns.
                frame = prepare_rows(
                    file_path,
                    sheet_name,
                    worksheet,
                    WORKSHEET_COLUMNS,
                )
                frame = add_date_field(frame, import_date)

                # Empty worksheets do not require table creation or insertion.
                if frame.empty:
                    continue

                ensure_data_table(connection, table_name, columns)

                column_sql = ", ".join(
                    _quote_identifier(column) for column in columns
                )
                placeholders = ", ".join("?" for _ in columns)
                insert_sql = (
                    f"INSERT INTO {_quote_identifier(table_name)} "
                    f"({column_sql}) VALUES ({placeholders})"
                )

                connection.executemany(
                    insert_sql,
                    frame.itertuples(index=False, name=None),
                )
                row_count += len(frame)

    except DatabaseError:
        # Preserve errors that already contain database-layer context.
        raise
    except sqlite3.Error as error:
        # Convert raw SQLite exceptions into the public exception type used by
        # callers of this module.
        raise DatabaseError(
            f"Database operation failed: {database_path}"
        ) from error

    print(
        f"Imported {row_count} rows for Date {import_date} "
        f"into {database_path}"
    )
    return True


def process_directory(
    input_directory: Path = DEFAULT_INPUT_DIR,
    database_path: Path = DEFAULT_DATABASE_PATH,
    processed_directory: Path = DEFAULT_PROCESSED_DIR,
) -> tuple[int, int]:
    """Import every ``.xls`` file in *input_directory*.

    Files are processed in filename order and moved only after successful
    import. If the processed directory is the same as the input directory,
    moving is correctly treated as a no-op because the file is already there.

    Returns:
        A ``(imported, skipped)`` count.

    Raises:
        FileNotFoundError: If the input directory does not exist.
        NotADirectoryError: If the input path is not a directory.
        Exception: Any workbook or database error is propagated and stops the
            batch, leaving the failing file in place for inspection.
    """
    input_directory = input_directory.expanduser().resolve()
    database_path = database_path.expanduser().resolve()
    processed_directory = processed_directory.expanduser().resolve()

    if not input_directory.exists():
        raise FileNotFoundError(f"Input directory not found: {input_directory}")
    if not input_directory.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {input_directory}")
    processed_directory.mkdir(parents=True, exist_ok=True)

    files = sorted(
        (path for path in input_directory.iterdir()
         if path.is_file() and path.suffix.casefold() == ".xls"),
        key=lambda path: path.name.casefold(),
    )

    # Create the database as soon as an input workbook is detected. This keeps
    # database creation independent from workbook validation and row count.
    if files:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path):
            pass

    imported = 0
    skipped = 0
    for file_path in files:
        if import_file(file_path, database_path):
            imported += 1
        else:
            skipped += 1

        destination = processed_directory / file_path.name
        if destination.resolve() == file_path.resolve():
            print(f"Processed file already in destination: {file_path.name}")
        else:
            shutil.move(str(file_path), str(destination))
            print(f"Moved processed file to: {destination}")

    print(
        f"Completed {len(files)} .xls file(s): "
        f"{imported} imported, {skipped} skipped"
    )
    return imported, skipped


def main() -> None:
    """Run the batch importer from the command line."""
    parser = argparse.ArgumentParser(
        description="Import all .xls files from the portfolio input directory."
    )
    parser.add_argument(
        "--input-directory",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing .xls files (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite destination (default: {DEFAULT_DATABASE_PATH})",
    )
    parser.add_argument(
        "--processed-directory",
        type=Path,
        default=DEFAULT_PROCESSED_DIR,
        help=(
            "Directory for processed files "
            f"(default: {DEFAULT_PROCESSED_DIR})"
        ),
    )
    args = parser.parse_args()
    process_directory(
        args.input_directory,
        args.database,
        args.processed_directory,
    )


if __name__ == "__main__":
    main()
