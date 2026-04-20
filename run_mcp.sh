#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$SCRIPT_DIR/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "[ProjectMind] Virtualenv not found at $VENV_PY" >&2
    echo "Create it with:" >&2
    echo "    python -m venv \"$SCRIPT_DIR/.venv\"" >&2
    echo "    \"$SCRIPT_DIR/.venv/bin/pip\" install -e \"$SCRIPT_DIR\"" >&2
    exit 1
fi

exec "$VENV_PY" "$SCRIPT_DIR/mcp_server.py" "$@"
