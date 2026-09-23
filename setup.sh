#!/usr/bin/env bash
# One-time setup for a local Numenor install.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Checking prerequisites"
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
command -v node >/dev/null    || { echo "node is required"; exit 1; }
command -v ffmpeg >/dev/null  || { echo "ffmpeg is required for snapshots and preview"; exit 1; }

echo "==> Creating the backend virtual environment"
if [ ! -x backend/.venv/bin/python ]; then
  python3 -m venv backend/.venv 2>/dev/null || {
    # Some distributions ship python3-venv without ensurepip.
    python3 -m venv --without-pip backend/.venv
    curl -fsSL https://bootstrap.pypa.io/pip/get-pip.py -o /tmp/numenor-get-pip.py
    backend/.venv/bin/python /tmp/numenor-get-pip.py -q
    rm -f /tmp/numenor-get-pip.py
  }
fi
backend/.venv/bin/pip install -q -r backend/requirements-dev.txt

echo "==> Installing frontend dependencies"
(cd frontend && npm install --silent)

echo "==> Building the frontend"
(cd frontend && npm run build >/dev/null)

echo
echo "Setup complete. Start the platform with:  ./start.sh"
