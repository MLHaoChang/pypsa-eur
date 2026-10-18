<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-02B — narrow chat and workbench layout

Starting actual master: `dbc1fa7003a6a005b5b419e67c33920a341afe58` (PR369).
Scope: layout and portable browser regression. No harness admission, model,
solver, native parameter schema or engineering evidence changes.

## Reproduction

The built React app runs against an authenticated native FastAPI backend with
isolated SQLite, private ephemeral owner/project roots, real login and CSRF.
Successful application HTTP is never intercepted. The original five-state
320px browser regression failed all five states: document scrollWidth721 versus
clientWidth320, including when both chat surfaces were closed. The companion
itself fit between x24 and296.

Both screen-reader mode descriptions in AppHeader had absolute positioning
without a positioned ancestor: their static positions extended to721px outside
the clipped workbench. A reversible runtime probe changed only the mode group's
position: original721, relative320, restored721. Adding `relative` to that group
anchors its descriptions locally without hiding their accessibility content.

A stricter normal-pointer dock check then found its saved420px width extending
from x99 to519 at320px. Three page/companion states passed; the two dock states
failed. The workbench body now provides a relative containing block. Below640px
the still-mounted dock overlays that body and caps its rendered width to100%,
while preserving the saved desktop width. This initial five-state regression
then passed all five states. No new global overflow clipping was added.

The first expanded matrix passed68/70 cases. Native Guided card review exposed
an additional persistent1px shift of the workbench's hidden scroll container,
not document overflow. A repeated short-touch probe reproduced seven failures;
the shift remained after a2s diagnostic wait. Tagged native scroll tracing
attributed movement to transcript `scrollIntoView` scrolling outer ancestors.
The repair scrolls only the transcript element with `scrollTo`, preserving the
existing bottom-follow/scroll-up/expand decisions. Its existing expand
regression now also requires every scroll target to be the transcript.
Diagnostic style changes, wrappers and timing probes are removed from the
permanent runner. No global scroll reset or relaxed geometry tolerance is used.

At the640px breakpoint, the saved700px dock also failed all three open-dock
states. The dock may now shrink with its flex container above the breakpoint,
with an explicit zero intrinsic minimum; its saved width is not changed.

Final selected matrix: Guided/Expert at320×850 mouse,320×600 touch,375×850 touch,
420×600 touch,640×850 mouse,768×600 touch and1440×1000 mouse. Seven states each:
closed workbench, open companion, expanded companion Access with actual saved
restriction changes, open dock with pointer checking, expanded dock Access,
native card review/denial across both surfaces, and desktop width restoration
with checking/draft retained. This is98 selected cases; final outcome pending.
The native proposal is reviewed in the dock and companion without a forced
click. Companion Approve/Deny use trial hit tests only; the actual denial is in
the dock and native network read-back verifies no write. This does not test an
approved write. All ordinary32 composer regressions are also being rerun.
Component regression135passed across9 suites, including transcript expansion.
Final browser evidence and PR/exact-head CI/premerge remain pending.

## Portable verification

Install frozen Python/frontend dependencies and explicit Playwright/Chromium
prerequisites, as for the preceding composer runner. Use an isolated checkout
with `backend/.env` absent; the helper refuses that file. From the frontend:

```bash
npm run build
PYPSA_COMPOSER_PYTHON=/path/to/test/python CHROMIUM_BIN=/path/to/chromium node scripts/smoke-chat-composer.mjs --narrow
PYPSA_COMPOSER_PYTHON=/path/to/test/python CHROMIUM_BIN=/path/to/chromium node scripts/smoke-chat-composer.mjs
```

If needed, set `PLAYWRIGHT_MODULE_PATH` to the runtime's installed package.json.
The ordinary composer suite continues to refuse every model call. The narrow
suite uses one explicit finite loopback model wire to request a native
bulk-update confirmation; approval/restriction HTTP, SSE, dispatch, denial and
network read-back stay real. It changes an ephemeral personal restriction via
the UI, denies the proposed write and verifies native G1 marginal_cost remains10.
This does not certify an approved write or real-model reasoning.
Provider keys are removed from the backend environment, browser external HTTP
is refused, and no external model, transcription, microphone or paid media
call is made. Retained reports contain source byte hashes and geometry, never
credentials/cookies; private database/project files are removed at teardown.

Real devices, hosted inference/media, independent domain release approval,
real-use calibration and generic whole-application copilot coverage stay pending.
