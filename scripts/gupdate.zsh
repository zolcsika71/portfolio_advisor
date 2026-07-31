#!/usr/bin/env zsh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KNOWLEDGE_DIR="$PROJECT_ROOT/data/knowledge"

if [[ ! -d "$KNOWLEDGE_DIR" ]]; then
    echo "ERROR: Knowledge directory not found:"
    echo "  $KNOWLEDGE_DIR"
    exit 1
fi

cd "$KNOWLEDGE_DIR"

graphify update .