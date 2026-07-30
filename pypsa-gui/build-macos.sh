#!/usr/bin/env bash
#
# Build the macOS app. One command, and the credential gate cannot be skipped.
#
#   bash pypsa-gui/build-macos.sh
#
# The gate is the reason this script exists rather than a README section. Every
# other packaging mistake is a broken build you fix and rebuild; shipping
# `backend/.env` publishes a real ANTHROPIC_API_KEY and a SECRET_KEY identical
# in every install, and there is no recall. A step a human is asked to remember
# is a step that gets skipped on the tired build.
#
# D14: the frozen app is built from a PIP-WHEEL venv on a standalone CPython,
# never from the pixi/conda environment — freezing conda produces a build that
# works on the developer's box and fails on a clean machine. Set BUILD_PYTHON
# to a non-conda python3.13 the first time; the venv is cached afterwards.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
VENV="${BUILD_VENV:-$HERE/.build-venv}"
DIST="${DIST_DIR:-$HERE/dist-app}"
WORK="${WORK_DIR:-$HERE/.build-work}"
APP="$DIST/PyPSA GUI.app"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# ── the build interpreter ───────────────────────────────────────────────────
if [ ! -x "$VENV/bin/python" ]; then
  if [ -z "${BUILD_PYTHON:-}" ]; then
    cat >&2 <<'MSG'
No build venv yet, and BUILD_PYTHON is not set.

Point it at a python3.13 that is NOT the conda/pixi interpreter (D14). A
standalone build works and needs no system install:

  curl -sL -o /tmp/cpython.tar.gz \
    https://github.com/astral-sh/python-build-standalone/releases/download/20260728/cpython-3.13.14%2B20260728-aarch64-apple-darwin-install_only.tar.gz
  tar xzf /tmp/cpython.tar.gz -C /tmp
  BUILD_PYTHON=/tmp/python/bin/python3 bash pypsa-gui/build-macos.sh
MSG
    exit 2
  fi
  step "Creating the build venv with $BUILD_PYTHON"
  "$BUILD_PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  # `proxy_tools` (via pywebview) publishes no wheel — pure Python, no
  # dependencies, no compiler. So this cannot be --only-binary=:all:.
  "$VENV/bin/pip" install --quiet -r "$HERE/gui-requirements.txt" pyinstaller
fi

# ── the SPA ─────────────────────────────────────────────────────────────────
# `dist/` is gitignored, so a checkout never has one, and a STALE one is worse
# than a missing one: the app serves an old UI with no error anywhere.
#
# node/npm come from the PIXI environment — `nodejs >=22` is a pixi dependency
# and there is no separate nodeenv (CLAUDE.md). A bare `npm` here failed with
# `command not found` on the machine that wrote this script, which is exactly
# how a build script rots into a README nobody can run.
PIXI_BIN="$REPO/.pixi/envs/default/bin"
if [ -x "$PIXI_BIN/npm" ]; then
  export PATH="$PIXI_BIN:$PATH"
fi
command -v npm >/dev/null 2>&1 || {
  echo "npm not found. Run 'pixi install' first — node comes from the pixi environment." >&2
  exit 2
}

step "Building the SPA"
( cd "$HERE/frontend" && npm run build )
test -f "$HERE/frontend/dist/spa.html" || {
  echo "frontend/dist/spa.html missing after the build" >&2; exit 1; }

step "Freezing the app"
( cd "$HERE" && "$VENV/bin/pyinstaller" pypsa-gui.spec --noconfirm \
    --distpath "$DIST" --workpath "$WORK" )

# ── the gate ────────────────────────────────────────────────────────────────
# Deliberately AFTER the build and before anything else can touch the output.
# `set -e` makes a non-zero exit here fail the whole script.
step "Checking the bundle for secrets and user data"
"$VENV/bin/python" "$HERE/backend/smoke/check_bundle.py" "$APP"

step "Done"
echo "  $APP"
echo
echo "Install:  rm -rf '/Applications/PyPSA GUI.app' && cp -R '$APP' /Applications/"
echo "Run:      open -a 'PyPSA GUI'"
echo
echo "Unsigned and un-notarised: it opens here because a locally built app"
echo "carries no quarantine flag. Copied or downloaded to another Mac it will"
echo "be blocked by Gatekeeper until workstream J signs it."
