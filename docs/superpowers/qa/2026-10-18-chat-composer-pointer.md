<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-02A — composer pointer regression

Starting master: `6691bd671887210a84e6891a513835cad1723abd`.
Scope: composer controls and dictation-settings placement; no harness policy,
model, solver, native parameter or numerical-result changes.

## Reproduction and repair

A portable Chromium runner exercises the built application, actual authenticated
FastAPI backend, real login/CSRF and isolated SQLite/personal Project. App HTTP
is not intercepted with successful fixtures. A loopback-only profile refuses
inference and records any attempted provider call; real provider keys are
removed from the backend process. Layout/settings/checking actions must cause
zero inference/transcription requests. No microphone capture is initiated.

The original source failed six ordinary checkbox clicks: default/88px and 60px
desktop preferences, with Access collapsed or expanded. At the default the
composer started at y888 but Upload a file started at y854 and intercepted the
checkbox centre. At 60px the paste control intercepted it. The toolbar is four
controls tall; a touch-only minimum did not protect desktop.

`ChatPanel` now uses an intrinsic `min-content` desktop floor. The measured
default rendered height is 131px, while the saved preference stays88. A saved60
also remains60 and renders safely. Existing 200px touch minimum is preserved.
The fix grows the container around its contents rather than covering/clipping
the controls or rewriting the user's preference.

The same runner found two additional failures at a saved360px touch composer:
dictation settings opened above the composer and its Close action was obscured
by the app header. The intrinsic floor alone passed14/16 cases. Anchoring the
settings overlay to the composer's bottom fixes the Close action without
increasing global z-index or changing media behavior.

Final runner: **16 passed, 0 failed**, no browser page errors. This includes
desktop and Chromium touch emulation, default/60/88/360 preferences and Access
expanded/collapsed. Each case uses a normal checkbox pointer action, keyboard
toggle, contained toolbar geometry, editable draft and normal settings Close.
The default desktop case also drags the actual resize handle to360 then60,
rechecks pointer access and verifies the saved60 survives a real page reload.

The initial combined runner was8pass/8fail (six checkbox and two settings cases),
then14pass/2fail after the first fix, then16pass after both fixes. These counts
are overlapping iterations, not independent totals. Source byte hashes and
per-case geometry are committed in
[the evidence record](../../../.scratch/live-copilot/evidence/composer-pointer.json).
The runner's recorded HEAD is the starting commit plus the source hashes of
the tested working changes; verify those hashes against the implementation PR.

## Re-run from another checkout

Install the frozen repository test/default Python environments and locked
frontend dependencies if missing. Playwright and Chromium are explicit browser
runner prerequisites; Playwright may be installed separately without changing
the application lockfile. From `pypsa-gui/frontend`:

```bash
npm run build
PYPSA_COMPOSER_PYTHON=/path/to/test/environment/bin/python CHROMIUM_BIN=/path/to/chromium node scripts/smoke-chat-composer.mjs
```

If Playwright is provided by the execution runtime rather than local resolution,
set `PLAYWRIGHT_MODULE_PATH` to its installed `package.json`. The Python helper
derives the repository/backend/dist paths from its own location. It creates
temporary private credentials, database and project roots, and tears them down
with the backend/browser after completion. Public retained artifacts are
geometry/results and failure screenshots; private fixture data is removed.

Local component regression:111passed across ChatAccess, ChatPanel checked
opt-in/companion entry, CompanionLauncher, AssistantDock and DictationControls.
Production build/type-check, Python helper Ruff, Node syntax and diff checks
pass. The browser runner is a required recorded local gate for this slice;
the existing frontend CI job runs type-check/Vitest, not this native backend
browser runner. A green component job alone does not certify geometry.

## Remaining scope

The earlier [eight-scenario permission report](2026-10-18-chat-companion-browser-e2e.md)
remains historical6pass/2fail; this narrower regression does not rerun those
native approval/write scenarios or rewrite their counts. It establishes a fix
for the pointer failure through a new source-pinned journey. LC-02B whole-page
horizontal overflow at320px still needs separate attribution and repair.
Real devices, hosted providers/media, domain calibration and generic copilot
implementation remain pending.
