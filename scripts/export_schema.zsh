#!/usr/bin/env zsh

set -euo pipefail

PROJECT_ROOT="${0:A:h:h}"
DEFAULT_DATABASE="$PROJECT_ROOT/database/model_portfolio.sqlite"
DEFAULT_OUTPUT="$PROJECT_ROOT/database/schema.sql"

DATABASE_PATH="${1:-$DEFAULT_DATABASE}"
OUTPUT_PATH="${2:-$DEFAULT_OUTPUT}"

if ! command -v sqlite3 >/dev/null 2>&1; then
    print -u2 "ERROR: sqlite3 command not found"
    exit 1
fi

if [[ ! -f "$DATABASE_PATH" ]]; then
    print -u2 "ERROR: SQLite database not found: $DATABASE_PATH"
    exit 1
fi

mkdir -p "${OUTPUT_PATH:h}"
sqlite3 "$DATABASE_PATH" '.schema' > "$OUTPUT_PATH"

print "Exported SQLite schema"
print "Database: $DATABASE_PATH"
print "Output:   $OUTPUT_PATH"
