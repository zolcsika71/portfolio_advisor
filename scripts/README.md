# Scripts

This project provides wrapper scripts to simplify working with the Graphify knowledge base.

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

---

### Update Graphify

Updates the installed Graphify CLI to the latest version.

```bash
./scripts/update_graphify.zsh
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