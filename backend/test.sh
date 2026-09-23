#!/usr/bin/env bash
# Run the backend test suite.
# PYTEST_DISABLE_PLUGIN_AUTOLOAD keeps unrelated system-wide pytest plugins
# (e.g. a ROS install on PYTHONPATH) from being loaded into this run.
set -euo pipefail
cd "$(dirname "$0")"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 exec .venv/bin/python -m pytest "$@"
