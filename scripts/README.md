# Scripts

This project provides wrapper scripts to simplify working with the Graphify knowledge base.

The portfolio database importer is run through the application entry point:

```bash
poetry run python -m portfolio_advisor.main
```

It reads `.xls` files from `data/xls/import`, writes to
`database/model_portfolio.sqlite`, and moves successfully processed files to
`data/xls/processed`. Custom paths can be supplied with `--input-directory`,
`--processed-directory`, and `--database`.

## Available Commands

### Verify the Graphify installation

Checks:

- Graphify installation
- Graphify version
- Generated graph files
- JSON validity
- Graph statistics
- Graph query functionality

```bash
./scripts/check_graphify.zsh
```

The verification script checks the Graphify executable, generated graph files,
JSON structure, graph statistics, and query functionality. After a successful
check it prints the recommended wrapper-script workflow.

---

### Query the knowledge graph

Use this script instead of calling `graphify query` directly.

```bash
./scripts/gquery.zsh "<query>"
```

Examples:

```bash
./scripts/gquery.zsh "Maximum Drawdown"
```

```bash
./scripts/gquery.zsh "Capital Asset Pricing Model (CAPM)"
```

```bash
./scripts/gquery.zsh "Black-Litterman Model"
```

```bash
./scripts/gquery.zsh "Conditional Value at Risk"
```

```bash
./scripts/gquery.zsh "Sharpe Ratio"
```

---

### Update the knowledge graph

After adding, removing, or modifying files in:

```text
data/knowledge/
```

run:

```bash
./scripts/gupdate.zsh
```

After adding or changing reviewed rules in:

```text
data/knowledge/validated_rules/
```

run the same graph update and verification workflow. Only reviewed YAML or
Markdown rules in that directory may define executable financial logic.

---

### Update Graphify

Updates the installed Graphify CLI to the latest version.

```bash
./scripts/update_graphify.zsh
```

---

### Export the SQLite schema

Exports the schema from the default portfolio database to
`database/schema.sql`:

```bash
./scripts/export_schema.zsh
```

Optional arguments can override the database and output paths:

```bash
./scripts/export_schema.zsh /path/to/database.sqlite /path/to/schema.sql
```

After upgrading Graphify, verify the installation again:

```bash
./scripts/check_graphify.zsh
```

---

### Audit Erste Market NAV acquisition diagnostics

Validates the deterministic resolution sequence (fund detail page, exact-ISIN
autocomplete fallback, then chart response) against a read-only SQLite source.
It writes a machine-readable JSON audit by default and never inserts, removes,
deduplicates, or repairs source observations.

```bash
poetry run python scripts/validate_erste_mapping.py \
  --limit 66 \
  --audit-output data/audit/erste_nav_diagnostics.json
```

Only `PASS` and `PASS_WITH_FILTERED_SENTINEL` records are marked
`usable_for_backtest`. `NO_ERSTE_MAPPING`,
`INVALID_NAV`, `CONFLICTING_HISTORY`, `NO_CHART_HISTORY`, and `SOURCE_ERROR`
remain fail-closed. The JSON output records resolution attempts, date range,
observation counts, and raw anomaly context; it is diagnostic evidence, not
data cleaning or source acceptance.

`SOURCE_SENTINEL` is the only normalization classification. It is restricted
to the confirmed epoch-era `0.0` record for `IE00B7KFL990` and
`IE00B84J9L26`: the known 1970-01-01 timestamp must be the sole invalid value,
and the remaining source observations must be positive and begin at the
confirmed substantially later source point. The raw record remains in the
audit while exactly that row is omitted from the normalized series. No
interpolation, generic zero/negative filtering, or other data repair occurs.

The command also writes `data/audit/historical_nav_source_coverage.json` by
default. It records source precedence and primary/fallback provenance. Erste
remains priority 1; no secondary source is configured or contacted by this
repository. A primary `NO_ERSTE_MAPPING` is therefore reported as
`SECONDARY_SOURCE_REQUIRED`, and conflicting history as
`RECONCILIATION_REQUIRED`, both fail closed. See
[historical NAV source precedence](../docs/historical_nav_sources.md) for the
source contract and acquisition acceptance conditions.

---

# Recommended Workflow

## First-time project setup

```bash
poetry install

./scripts/check_graphify.zsh
```

To syntax-check the shell wrappers:

```bash
zsh -n scripts/check_graphify.zsh
zsh -n scripts/export_schema.zsh
```

---

## After adding new portfolio PDFs

```bash
./scripts/gupdate.zsh

./scripts/check_graphify.zsh
```

---

## Query the knowledge base

```bash
./scripts/gquery.zsh "Maximum Drawdown"
```

```bash
./scripts/gquery.zsh "Portfolio Optimization"
```

```bash
./scripts/gquery.zsh "Capital Preservation"
```

---

## After updating Graphify

```bash
./scripts/update_graphify.zsh

./scripts/check_graphify.zsh
```

---

# Official Graphify Workflow

Always use the wrapper scripts provided in the `scripts/` directory.

Do **not** execute:

```bash
graphify query "<query>"
```

from the project root.

The wrapper scripts automatically switch to the Graphify knowledge corpus (`data/knowledge`) before executing the Graphify command, ensuring the correct knowledge graph is used regardless of the current working directory.

---

# Script Overview

| Script | Purpose |
|---------|---------|
| `check_graphify.zsh` | Verify the Graphify installation and generated knowledge graph |
| `gquery.zsh` | Query the Graphify knowledge graph |
| `gupdate.zsh` | Update the Graphify knowledge graph after knowledge changes |
| `update_graphify.zsh` | Update the installed Graphify CLI |
| `export_schema.zsh` | Export the SQLite database schema to SQL |
| `validate_erste_mapping.py` | Audit Erste ISIN resolution and raw NAV history diagnostics |

## Validated rules

`data/knowledge/validated_rules/README.md` documents the requirements for
reviewed rules. `codex.yaml` records the Codex-facing policy, including the
restriction that Graphify `INFERRED` edges are for discovery only and cannot be
used as executable financial rules without independent review.
