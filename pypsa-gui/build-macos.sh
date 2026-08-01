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
APP="$DIST/PyPSA Studio.app"

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
fi

# ── sync the venv to gui-requirements.txt, EVERY build ──────────────────────
#
# This used to live inside the `if` above, so it ran once — at venv creation —
# and never again. The venv is cached by design, which meant that after the
# first build, editing `gui-requirements.txt` had NO EFFECT on the artifact.
#
# That is how `openpyxl` stayed missing on 2026-07-31: adding the pin fixes
# nothing on its own, and the build reports success either way. A packaging
# manifest the build ignores is worse than no manifest, because it reads as
# authoritative in review.
#
# pip is a no-op when everything is already satisfied, so the cost of getting
# this right is a second or two per build.
#
# `proxy_tools` (via pywebview) publishes no wheel — pure Python, no
# dependencies, no compiler. So this cannot be --only-binary=:all:.
step "Syncing the build venv to gui-requirements.txt"
"$VENV/bin/pip" install --quiet -r "$HERE/gui-requirements.txt" pyinstaller

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

# ── signing and notarisation (workstream J) ─────────────────────────────────
#
# Both are skipped when unconfigured, so the default build is unchanged. The
# `.spec` does the SIGNING itself, as PyInstaller collects each nested binary —
# `codesign --deep` afterwards is deprecated, unreliable across several hundred
# `.so`/`.dylib` files, and fails notarisation naming one file and no reason.
#
#   CODESIGN_IDENTITY="Developer ID Application: NAME (TEAMID)"   # sign
#   NOTARY_PROFILE=pypsa-gui                                      # notarise
#
# The profile is stored once, and holds the app-specific password so it never
# appears in a command line or in this repo:
#
#   xcrun notarytool store-credentials pypsa-gui \
#     --apple-id you@example.com --team-id TEAMID --password <app-specific>
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  step "Verifying the signature"
  # `--strict` because the default accepts things Gatekeeper will not.
  codesign --verify --deep --strict --verbose=2 "$APP"
  echo "signed by: $(codesign -dvv "$APP" 2>&1 | grep -i '^Authority' | head -1)"

  if [ -n "${NOTARY_PROFILE:-}" ]; then
    step "Notarising (Apple's service; usually a few minutes)"
    ZIP="${APP%.app}.zip"
    # ditto, not `zip` — `zip` does not preserve symlinks or resource forks,
    # and the submission is rejected or, worse, notarises a broken copy.
    /usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"
    xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
    rm -f "$ZIP"

    step "Stapling the ticket"
    # So it validates with no network. Without this a first launch offline
    # still shows the Gatekeeper warning.
    xcrun stapler staple "$APP"
    xcrun stapler validate "$APP"
    spctl --assess --type execute --verbose=4 "$APP" || true
  else
    echo
    echo "NOTARY_PROFILE not set — signed but NOT notarised. Gatekeeper still"
    echo "blocks a downloaded copy: signing alone is not enough since Catalina."
  fi
fi

# ── the DMG (workstream J) ──────────────────────────────────────────────────
#
# The `.app` alone is not a distributable: handing someone a folder-that-is-
# really-a-bundle invites them to run it from Downloads, where the first
# `/Applications` copy they do later leaves two installs. A DMG with an
# `Applications` symlink makes drag-to-install the obvious move.
#
# Skip with SKIP_DMG=1 during fast iteration.
if [ -z "${SKIP_DMG:-}" ]; then
  step "Building the DMG"
  STAGE="$WORK/dmg-stage"
  rm -rf "$STAGE" && mkdir -p "$STAGE"
  # ditto, not cp: preserves symlinks, resource forks and the signature.
  /usr/bin/ditto "$APP" "$STAGE/PyPSA Studio.app"
  ln -s /Applications "$STAGE/Applications"

  # MEASURED instructions, not the usual folklore. On macOS 15.0.1 with this
  # bundle: `xattr -dr` fails with `Operation not permitted` on 1855 paths
  # INCLUDING the bundle root and the app stays blocked; the non-recursive
  # form on the .app alone works and the app launches. And Sequoia removed the
  # Control-click -> Open override that spec J.3 tells us to document, so that
  # instruction would have shipped broken.
  cat > "$STAGE/README.txt" <<'READ'
PyPSA Studio — installation
===========================

Requires macOS 14 (Sonoma) or later, on Apple Silicon.

1. Drag "PyPSA Studio" onto the Applications folder in this window.
2. Open it from Applications (Spotlight, Launchpad, or double-click).

On macOS 12 or 13 the app will not start and Finder says "requires a newer
version of macOS" — that is this requirement, not a broken download.


If macOS says "cannot be opened because the developer cannot be verified"
-------------------------------------------------------------------------

This build is not signed with an Apple Developer ID, so macOS blocks it the
first time IF the file was downloaded through a browser or mail client. A copy
handed over on a USB stick, by scp, or from an internal file share usually
carries no such mark and just opens.

Two ways past it, either is fine:

  * System Settings -> Privacy & Security. After the first refusal there is an
    "Open Anyway" button near the bottom of that page. Press it, then open the
    app again.

  * Or, in Terminal, one line:

        xattr -d com.apple.quarantine "/Applications/PyPSA Studio.app"

    Note: NOT `xattr -dr`. The recursive form fails on this app with
    "Operation not permitted" and leaves it blocked.

On macOS 15 (Sequoia) and later, right-clicking the app and choosing Open no
longer works — Apple removed that shortcut. Use one of the two above.


Where your data lives
---------------------

  Projects:  ~/Documents/PyPSA Studio/Projects/     (ordinary folders)
  App data:  ~/Library/Application Support/PyPSA Studio/
  Log:       ~/Library/Application Support/PyPSA Studio/pypsa-gui.log

Upgrading from a build called "PyPSA GUI"? Your existing folders keep
working — the app uses them when they are there, so nothing moves and
nothing is lost.

Already have projects from a pre-desktop install? In the app choose
New project -> From folder, and point it at your old projects directory.
It copies; your originals are left untouched.
READ

  DMG="$DIST/PyPSA-Studio.dmg"
  rm -f "$DMG"
  hdiutil create -volname "PyPSA Studio" -srcfolder "$STAGE" -ov -format UDZO -quiet "$DMG"
  rm -rf "$STAGE"

  if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    # The DMG is signed and notarised in its OWN right. A notarised app inside
    # an unsigned DMG still warns on the container.
    codesign --force --sign "$CODESIGN_IDENTITY" "$DMG"
    if [ -n "${NOTARY_PROFILE:-}" ]; then
      xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
      xcrun stapler staple "$DMG"
    fi
  fi
  echo "  $DMG  ($(du -h "$DMG" | cut -f1))"
fi

step "Done"
echo "  $APP"
echo
echo "Install:  rm -rf '/Applications/PyPSA Studio.app' && cp -R '$APP' /Applications/"
echo "Run:      open -a 'PyPSA Studio'"

if [ -z "${CODESIGN_IDENTITY:-}" ]; then
  cat <<'MSG'

Unsigned and un-notarised. It opens on THIS machine because a locally built
app carries no quarantine flag — that is not evidence it will open anywhere
else. Copied or downloaded to another Mac it is blocked.

On macOS 15 (Sequoia) the old Control-click -> Open bypass is GONE. A recipient
must go to System Settings -> Privacy & Security and press "Open Anyway" after
the first refusal. Signing and notarising removes that step; see the
CODESIGN_IDENTITY / NOTARY_PROFILE block in this script.
MSG
fi
