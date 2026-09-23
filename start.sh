#!/usr/bin/env bash
# Start Numenor. The backend serves the built frontend, so this is the whole app.
# It binds to localhost by default: this is an on-premise tool, not a public service.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
exec backend/run.sh
