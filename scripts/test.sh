#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# The ROS Jazzy environment on some Ubuntu machines exports pytest plugins via
# PYTHONPATH. Disable third-party plugin auto-loading so FinGuard's test suite
# does not depend on an unrelated ROS installation.
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
exec uv run pytest "$@"
