#!/bin/bash
# SessionStart hook for Claude Code on the web: install what the project's
# tests and linters need, so every cloud session can run them. Keep it
# idempotent (a re-run is a no-op once installed) and pin versions to the
# project's lockfiles, so sessions test what CI tests.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"

# Install the project's dependencies here, for example:
#   python3 -m venv "$HOME/.venv-project"
#   "$HOME/.venv-project/bin/pip" install -q -r "$REPO/requirements.txt"
#   (cd "$REPO/frontend" && npm ci --no-audit --no-fund --loglevel=error)

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  # Export what later commands need, for example:
  #   echo "export PATH=\"$HOME/.venv-project/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
  :
fi
