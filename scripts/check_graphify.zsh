#!/usr/bin/env zsh

set -euo pipefail

PROJECT_ROOT="${0:A:h:h}"
KNOWLEDGE_DIR="$PROJECT_ROOT/data/knowledge"
GRAPH_DIR="$KNOWLEDGE_DIR/graphify-out"
GRAPH_FILE="$GRAPH_DIR/graph.json"
REPORT_FILE="$GRAPH_DIR/GRAPH_REPORT.md"
ANALYSIS_FILE="$GRAPH_DIR/.graphify_analysis.json"

print "Graphify verification"
print "====================="
print "Project root: $PROJECT_ROOT"
print

command -v graphify >/dev/null || {
    print -u2 "ERROR: graphify command not found"
    exit 1
}

print "Graphify executable: $(command -v graphify)"
print "Graphify version:    $(graphify --version)"
print

for file in "$GRAPH_FILE" "$REPORT_FILE" "$ANALYSIS_FILE"; do
    if [[ ! -s "$file" ]]; then
        print -u2 "ERROR: missing or empty file: $file"
        exit 1
    fi

    print "OK: $file"
done

print

poetry run python - "$GRAPH_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])

with path.open(encoding="utf-8") as file:
    graph = json.load(file)

required_keys = {
    "directed",
    "multigraph",
    "graph",
    "nodes",
    "links",
    "hyperedges",
    "built_at_commit",
}

missing = required_keys - graph.keys()

if missing:
    raise SystemExit(
        f"ERROR: graph.json is missing keys: {sorted(missing)}"
    )

nodes = graph["nodes"]
links = graph["links"]
hyperedges = graph["hyperedges"]

if not isinstance(nodes, list) or not nodes:
    raise SystemExit("ERROR: nodes is missing, invalid, or empty")

if not isinstance(links, list):
    raise SystemExit("ERROR: links is not a list")

if not isinstance(hyperedges, list):
    raise SystemExit("ERROR: hyperedges is not a list")

print("OK: graph.json is valid JSON")
print(f"Nodes:      {len(nodes)}")
print(f"Links:      {len(links)}")
print(f"Hyperedges: {len(hyperedges)}")
print(f"Commit:     {graph['built_at_commit']}")
PY

print
print "Running Graphify query..."

(
    cd "$KNOWLEDGE_DIR"
    graphify query "Capital Asset Pricing Model (CAPM)"
)

print
print "Graphify verification completed successfully."

print
print "Conclusion"
print "=========="
print "Your Graphify installation is fully operational."
print "The only operational limitation is that 'graphify query' assumes the current"
print "working directory is the root of the knowledge corpus."
print
Recommendation
==============
Use the project's Graphify wrapper script to execute all Graphify queries.
The wrapper script automatically changes to the knowledge corpus directory
before invoking 'graphify query', ensuring the correct graph is always used
regardless of the current working directory.

No changes to the existing project structure or Graphify installation are required.
