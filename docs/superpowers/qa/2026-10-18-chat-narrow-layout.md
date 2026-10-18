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
identified transcript `scrollIntoView` as an unbounded ancestor-scroll path.
The repair scrolls only the transcript element with `scrollTo`, preserving the
existing bottom-follow/scroll-up/expand decisions. Its existing expand
regression now also requires every scroll target to be the transcript.
The expanded follow-up still passed97/98. A proposed `preventScroll` on the
opening focus also failed3/56 repeated diagnostic cases and was reverted;
focus alone was not established as the residual cause. The outer frame had
horizontal and vertical scroll space from the overflowing header/status row.
An independent header-reachability RED then failed before any focus or pointer
scroll could reveal the clipped header actions.

The header now wraps below1280px with automatic height and its existing44px
minimum, keeping visible overflow for dropdowns. The account action aligns to
the right of its wrapped row so its opened menu remains within the screen.
The status row keeps nonwrapping text in its own keyboard-focusable horizontal
scroll region with a thin scrollbar. The workbench's existing overflow behavior
is unchanged; no root `overflow-clip` substitution is used.
Review also removed the runner's unnecessary window scroll reset and added
strict zero-scroll assertions for every dock ancestor. Final verification is
pending; no failing case is waived.
Diagnostic style changes, wrappers and timing probes are removed from the
permanent runner. No global scroll reset or relaxed geometry tolerance is used.

At the640px breakpoint, the saved700px dock also failed all three open-dock
states. The dock may now shrink with its flex container above the breakpoint,
with an explicit zero intrinsic minimum; its saved width is not changed.

Final selected matrix: Guided/Expert at320×850 mouse,320×600 touch,375×850 touch,
420×600 touch,640×850 mouse,768×600 touch and1440×1000 mouse. Nine states each:
closed workbench, header controls/menu/keyboard/status/rename, open companion, expanded companion Access with actual saved
restriction changes, open dock with pointer checking, expanded dock Access,
native card review/denial across both surfaces, native Choice picks/free text,
and desktop width restoration
with checking/draft retained. This is126 selected cases; final outcome pending.
The native proposal is reviewed in the dock and companion without a forced
click. Companion Approve/Deny use trial hit tests only; the actual denial is in
the dock and native network read-back verifies no write. This does not test an
approved write. All ordinary32 composer regressions are also being rerun.
Earlier component regression135passed across9 suites, including transcript expansion.
The responsive header snapshot changes only its root responsive classes; its
complete remaining markup assertion stays strict. Final focused component
regression:172passed across15suites, including Choice portal/state, frame
normalization, dictation and toast neighbors. Production build/type-check,
Ruff and Node syntax checks pass. Full browser regressions are running.
The first header follow-up exposed a retained answer intercepting the wrapped
account action. The companion now uses the positioned workbench body as its
containing block and scrolls within that height, below header/tabs. A genuine
account selector ambiguity was corrected by targeting its button, not the
matching email text in its opened menu. The existing32composer regression then
failed3short-touch Close clicks: a70vh dictation popup exceeded the reduced
workbench height. Its containing block is now ChatPanel and its maximum is the
smaller of70vh and available height minus1rem; local scrolling retains all
settings. The original failing short-touch case passed after that repair.

At320×600, the disabled200px composer also left too little transcript space for
native confirmation buttons. Narrow waiting confirmations reclaim that space
with CSS only when streaming; the composer stays mounted and returns after the
decision. An Expert short-screen24-state probe then passed all states.

Spec review identified the same neighboring Choice risk. A native Choice RED
failed its ordinary option click at320×600. The existing ChoiceCard now owns
one set of picks while its DOM portals between transcript and companion.
Narrow choice view prioritizes options, with an explicit allowed free-text
route and Return to choices. View selection is bound to exact choice identity;
Compose remains a separate-message route when free text is forbidden. No
executor, approval or dispatch policy changes. A9-state native probe passed,
including bidirectional pick preservation, native multi-select Send, preserved
unsent draft and an actual free-text turn. The backend history verifies both
answers. The component's no-target inline fallback preserves its existing
standalone frame-normalization contract. Full matrix verification is pending.

The first full126 matrix at source6521e807 returned123pass/3fail, all in
Expert native-choice geometry. The240px sidebar and300px properties panel
forced the320/420px workbench to540px; native focus then shifted that body26px.
Open phone properties now overlay the canvas at their existing300px maximum,
with actual header close/reopen and fit assertions. Existing PropertiesPanel
edit/save regressions14pass. The next strict run exposed a canvas view switch
covering the dock collapse button. Whole-canvas stacking isolation made that
click pass, but Standards review rejected it because schematic viewport dialogs
render in place and could be trapped below the dock. That candidate is not
accepted. A narrower widget-only layering repair and native cancel-only Reset
dialog regression are being verified. All preceding counts are overlapping
iterations; no failed case is waived.

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
suite uses one explicit finite loopback model wire to request native
bulk-update confirmations and ask_user choices; approval/restriction HTTP, SSE, dispatch, denial and
network read-back stay real. It changes an ephemeral personal restriction via
the UI, denies the proposed write and verifies native G1 marginal_cost remains10.
This does not certify an approved write or real-model reasoning.
Provider keys are removed from the backend environment, browser external HTTP
is refused, and no external model, transcription, microphone or paid media
call is made. Retained reports contain source byte hashes and geometry, never
credentials/cookies; private database/project files are removed at teardown.

Real devices, hosted inference/media, independent domain release approval,
real-use calibration and generic whole-application copilot coverage stay pending.
