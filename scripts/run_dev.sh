#!/bin/sh
# Development launcher: ./scripts/run_dev.sh [file.pdf ...]
cd "$(dirname "$0")/.." || exit 1
exec ./.venv/bin/python main.py "$@"
