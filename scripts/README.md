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

## Validated rules

`data/knowledge/validated_rules/README.md` documents the requirements for
reviewed rules. `codex.yaml` records the Codex-facing policy, including the
restriction that Graphify `INFERRED` edges are for discovery only and cannot be
used as executable financial rules without independent review.
