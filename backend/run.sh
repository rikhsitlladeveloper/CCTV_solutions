#!/usr/bin/env bash
# Start the Numenor backend. Binds to localhost only: this is an on-premise tool.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/uvicorn app.main:app --host "${NUMENOR_HOST:-127.0.0.1}" --port "${NUMENOR_PORT:-8000}" "$@"
