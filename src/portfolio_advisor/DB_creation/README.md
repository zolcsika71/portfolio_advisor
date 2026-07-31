# `portfolio_advisor/DB_creation`

This package imports model-portfolio data from Excel files into SQLite.

## Workflow

The importer:

1. Scans the input directory for `.xls` files.
2. Reads only the visible `modell portfóliók` worksheet.
3. Fills merged cells and removes empty rows and columns.
4. Translates supported Hungarian and English headers into the stable English
   database schema.
5. Translates supported categorical values, rejecting unknown values.
6. Derives one `Date` value per workbook from the eight-digit date in its
   filename, such as `portfolio_20250726.xls` → `2025/07/26`.
7. Inserts the normalized rows into SQLite.
8. Moves successfully processed files to the processed directory.

## Default paths

| Purpose | Path |
|---|---|
| Database creation package | `src/portfolio_advisor/DB_creation` |

The processed directory is created automatically when the importer runs.

## Running the importer

From the project root:

```bash
poetry run python -m portfolio_advisor.main
```

The database module can also be run directly as a package:

```bash
poetry run python -m portfolio_advisor.DB_creation.database_create
```

Optional paths can be supplied explicitly:

```bash
poetry run python -m portfolio_advisor.main \
  --input-directory /path/to/import \
  --processed-directory /path/to/processed \
  --database /path/to/model_portfolio.sqlite
```

The same command-line options are available when executing
`src/portfolio_advisor/main.py` directly from PyCharm.

## Database schema

The importer creates the `model_portfolios` table with these columns:

- `Date`
- `Portfolio Name`
- `Product`
- `ISIN`
- `Allocation (%)`
- `Asset Class`
- `Sub-Asset Class`
- `Currency`
- `Currency Risk`
- `Sustainability`
- `YTD`
- `1 Year`
- `3 Years`
- `5 Years`
- `1Y Sharpe Ratio`
- `3Y Sharpe Ratio`
- `5Y Sharpe Ratio`
- `1Y Volatility`
- `3Y Volatility`
- `Downside Risk`
- `Information Ratio`
- `Maximum Drawdown`

Text fields are stored as SQLite `TEXT`; metric and allocation fields are
stored as SQLite `REAL`.

## Module responsibilities

- `portfolio_advisor/DB_creation/excel_processing.py` reads the target worksheet, normalizes merged cells,
  translates headers and categorical values, validates columns, and adds the
  deterministic `Date` field.
- `portfolio_advisor/DB_creation/database_create.py` manages SQLite connections, creates or validates the
  schema, imports workbooks atomically, and moves processed files.
- `portfolio_advisor/DB_creation/text_normalization.py` provides Unicode-normalized, case-insensitive lookup
  keys.

## File requirements

Input files must:

- have the `.xls` extension;
- contain a visible worksheet named `modell portfóliók` (or its supported
  English equivalent `model portfolios`);
- contain an eight-digit date immediately before the extension; and
- contain headers recognized by `HEADER_TRANSLATIONS`.

Files that fail validation remain in the input directory for inspection.
