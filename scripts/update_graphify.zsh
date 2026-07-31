#!/usr/bin/env zsh

# Updates the uv-managed Graphify CLI and prints the installed version.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is not installed or is not on PATH." >&2
    exit 1
fi

echo "Updating Graphify..."
uv tool upgrade --python 3.12 graphifyy

if ! command -v graphify >/dev/null 2>&1; then
    echo "Error: Graphify was updated, but the 'graphify' command is not on PATH." >&2
    echo "Run: uv tool update-shell" >&2
    exit 1
fi

echo
echo "Installed Graphify version:"
graphify --version

