#!/bin/bash
# Finder-friendly development launcher; dependencies live in the repository's venv.
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python3 ]]; then
    echo "Set up Cartografiler's .venv first; see docs/macos.md."
    read -r -p "Press Enter to close." || true
    exit 1
fi
exec .venv/bin/python3 -m branchfm.service "${1:-$HOME}"
