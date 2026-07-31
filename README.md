# Portfolio Advisor

Portfolio Advisor imports model-portfolio Excel data into a SQLite database.
The current implementation focuses on the database creation workflow.

## Requirements

- Python 3.12
- Poetry
- Excel `.xls` files containing a visible `modell portfóliók` worksheet

Install dependencies with:

```bash
poetry install
```

## Import workflow

The importer:

1. Finds `.xls` files in the input directory.
2. Reads only the visible `modell portfóliók` worksheet.
3. Fills merged cells and removes empty rows and columns.
4. Translates supported headers and categorical values into English.
5. Creates one `Date` field per row from the eight-digit date in the filename.
6. Inserts the normalized data into SQLite.
7. Moves successfully processed files to the processed directory.

## Default paths

| Purpose | Path |
|---|---|
| Input Excel files | `/Users/zoltanka/Documents/Prog/Python/portfolio_advisor/data/xls/import` |
| Processed files | `/Users/zoltanka/Documents/Prog/Python/portfolio_advisor/data/xls/processed` |
| SQLite database | `/Users/zoltanka/Documents/Prog/Python/portfolio_advisor/database/model_portfolio.sqlite` |

The processed directory and database parent directory are created
automatically.

## Running the importer

From the project root:

```bash
poetry run python -m portfolio_advisor.main
```

The importer also accepts custom locations:

```bash
poetry run python -m portfolio_advisor.main \
  --input-directory /path/to/import \
  --processed-directory /path/to/processed \
  --database /path/to/model_portfolio.sqlite
```

PyCharm can execute this file directly:

```bash
python src/portfolio_advisor/main.py
```

## Input filename requirements

Each workbook must have the `.xls` extension and contain an eight-digit date
immediately before the extension. For example:

```text
portfolio_20250726.xls
```

The date is stored as `2025/07/26` in the database. Workbooks without a valid
date or supported target worksheet remain in the input directory when
processing fails.

## Database schema

The importer creates the `model_portfolios` table with:

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
- `YTD`, `1 Year`, `3 Years`, `5 Years`
- `1Y Sharpe Ratio`, `3Y Sharpe Ratio`, `5Y Sharpe Ratio`
- `1Y Volatility`, `3Y Volatility`
- `Downside Risk`, `Information Ratio`, `Maximum Drawdown`

See [`src/portfolio_advisor/DB_creation/README.md`](src/portfolio_advisor/DB_creation/README.md)
for implementation details.

## Development checks

Run Ruff and compile the package with:

```bash
poetry run ruff check src
python -m compileall -q src
```
