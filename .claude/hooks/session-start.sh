#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Builds the SAME environment CI and `pixi run gui-tests` use — pixi's `test`
# environment, straight from pixi.lock — so a cloud session can run the whole
# pypsa-gui backend suite, including the GridSpine tests (need the repo's own
# `gridspine` package, Python 3.12, pandapower and lightsim2grid) and the
# desktop tests (need pywebview). A hand-built venv that skipped those reported
# 136 failures that were only missing packages.
#
# Also installs the frontend's npm dependencies so vitest and tsc run.
#
# Idempotent: pixi and npm both no-op when already up to date, and the
# container is cached after this hook completes.
set -euo pipefail

# Local sessions manage their own environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
PIXI_VERSION="v0.68.1"   # keep in step with .github/workflows/test.yaml
PIXI_HOME="${HOME}/.pixi"
PIXI_BIN="${PIXI_HOME}/bin/pixi"

# The cloud proxy re-signs TLS; point pixi (rattler) at its CA bundle.
if [ -f /root/.ccr/ca-bundle.crt ]; then
  export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt
fi

# pixi.sh is not reachable from the sandbox, so fetch the release binary from
# GitHub directly.
if [ ! -x "$PIXI_BIN" ] || ! "$PIXI_BIN" --version 2>/dev/null | grep -q "${PIXI_VERSION#v}"; then
  mkdir -p "${PIXI_HOME}/bin"
  tmp="$(mktemp -d)"
  curl -fsSL --retry 4 --retry-delay 2 \
    -o "${tmp}/pixi.tgz" \
    "https://github.com/prefix-dev/pixi/releases/download/${PIXI_VERSION}/pixi-x86_64-unknown-linux-musl.tar.gz"
  tar -xzf "${tmp}/pixi.tgz" -C "${PIXI_HOME}/bin"
  rm -rf "$tmp"
fi

cd "$REPO"
"$PIXI_BIN" install -e test --locked

# Frontend: `npm install` (not `ci`) so the cached container is reused.
if [ -f pypsa-gui/frontend/package.json ]; then
  (cd pypsa-gui/frontend && npm install --no-audit --no-fund)
fi

# Make pixi and the CA bundle available to every command in the session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export PATH=\"${PIXI_HOME}/bin:\$PATH\""
    if [ -f /root/.ccr/ca-bundle.crt ]; then
      echo "export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt"
    fi
  } >> "$CLAUDE_ENV_FILE"
fi

echo "pypsa-gui test environment ready: pixi run -e test gui-tests (backend), npx vitest run (frontend)"
